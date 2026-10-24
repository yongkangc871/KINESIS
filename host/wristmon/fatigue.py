"""Fatigue trend score (0-100, higher = more fatigued).

FatigueScorer keeps the per-session baseline (mean of the first N valid reps), turns each
valid rep into relative-change features, asks a FatigueModel for a raw score and smooths it
with an EWMA. The default RuleFatigueModel is the weighted rule from the spec; any object
with `predict(features) -> float` can replace it (see load_fatigue_model).
"""
from __future__ import annotations

import json
import math
import pickle
from pathlib import Path
from typing import Protocol

import numpy as np

NAN = float("nan")

# Relative features handed to every model (percent change from baseline; positive = more fatigued).
REL_FEATURES = ["mdf_drop", "mnf_drop", "finsm5_rise", "rms_rise", "lift_time_rise",
                "peak_angle_drop", "sparc_drop"]


class FatigueModel(Protocol):
    def predict(self, features: dict) -> float: ...


class RuleFatigueModel:
    """Weighted sum of clipped, scaled terms. Weights differ for raw vs envelope-only EMG."""

    def __init__(self, fatigue_cfg: dict, emg_mode: str):
        self.weights = fatigue_cfg["weights_raw"] if emg_mode == "raw" else fatigue_cfg["weights_envelope"]
        self.full = fatigue_cfg["full_scale_pct"]

    def predict(self, features: dict) -> float:
        """A missing (nan) feature contributes 0; it never inflates the others."""
        score = 0.0
        for name, w in self.weights.items():
            v = features.get(name, NAN)
            if v is None or not math.isfinite(v):
                continue
            score += w * min(1.0, max(0.0, v / self.full[name]))
        wsum = sum(self.weights.values())
        return 100.0 * score / wsum if wsum > 0 else 0.0


class LinearFatigueModel:
    """Trained linear / logistic model stored as JSON:
    {"features": [...], "weights": [...], "bias": b, "logistic": false, "scale": 100}"""

    def __init__(self, path: str | Path):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        self.features = d["features"]
        self.w = np.asarray(d["weights"], float)
        self.b = float(d.get("bias", 0.0))
        self.logistic = bool(d.get("logistic", False))
        self.scale = float(d.get("scale", 100.0))

    def predict(self, features: dict) -> float:
        x = np.array([features.get(k, 0.0) if math.isfinite(features.get(k, NAN)) else 0.0
                      for k in self.features])
        y = float(self.w @ x + self.b)
        if self.logistic:
            y = 1.0 / (1.0 + math.exp(-y))
        return float(np.clip(y * self.scale, 0.0, 100.0))


class PickleFatigueModel:
    """Any pickled estimator with sklearn-style predict(X) over REL_FEATURES columns."""

    def __init__(self, path: str | Path):
        with open(path, "rb") as f:
            self.model = pickle.load(f)

    def predict(self, features: dict) -> float:
        x = [[features.get(k, 0.0) if math.isfinite(features.get(k, NAN)) else 0.0 for k in REL_FEATURES]]
        return float(np.clip(np.ravel(self.model.predict(x))[0] * 100.0, 0.0, 100.0))


def load_fatigue_model(fatigue_cfg: dict, emg_mode: str, base_dir: Path | None = None) -> FatigueModel:
    spec = str(fatigue_cfg.get("model", "rule"))
    if spec == "rule":
        return RuleFatigueModel(fatigue_cfg, emg_mode)
    kind, _, path = spec.partition(":")
    p = Path(path)
    if not p.is_absolute() and base_dir is not None:
        p = base_dir / p
    if kind == "json":
        return LinearFatigueModel(p)
    if kind == "pickle":
        return PickleFatigueModel(p)
    raise ValueError(f"unknown fatigue model spec: {spec}")


def jasa_label(rms_change_pct: float, mdf_change_pct: float, thr: float) -> str:
    """Joint analysis of EMG spectrum and amplitude (Luttmann et al.)."""
    if not (math.isfinite(rms_change_pct) and math.isfinite(mdf_change_pct)):
        return ""
    up = rms_change_pct > thr
    down = rms_change_pct < -thr
    if up and mdf_change_pct < -thr:
        return "fatigue"
    if up and mdf_change_pct > thr:
        return "force increase"
    if down and mdf_change_pct < -thr:
        return "force decrease"
    if down and mdf_change_pct > thr:
        return "recovery"
    return "stable"


def zone_for(score: float, cfg: dict) -> str:
    if not math.isfinite(score):
        return ""
    if score > cfg["red"]:
        return "red"
    if score >= cfg["amber"]:
        return "amber"
    return "green"


def _pct(new: float, base: float) -> float:
    if not (math.isfinite(new) and math.isfinite(base)) or base == 0:
        return NAN
    return (new - base) / abs(base) * 100.0


class FatigueScorer:
    def __init__(self, fatigue_cfg: dict, emg_mode: str, model: FatigueModel | None = None):
        self.cfg = fatigue_cfg
        self.mode = emg_mode
        self.model = model or RuleFatigueModel(fatigue_cfg, emg_mode)
        self.reset()

    def reset(self):
        self.baseline_reps: list[dict] = []
        self.baseline: dict | None = None
        self.ewma = NAN
        self.history: list[tuple[int, float]] = []

    @staticmethod
    def rep_vector(rep) -> dict:
        f = rep.features
        return {"mdf": f.get("mdf", NAN), "mnf": f.get("mnf", NAN), "finsm5": f.get("finsm5", NAN),
                "rms": f.get("rms_pct", NAN), "lift_s": rep.lift_s, "peak": rep.peak_theta, "sparc": rep.sparc}

    def relative(self, v: dict) -> dict:
        b = self.baseline
        rel = {
            "mdf_drop": -_pct(v["mdf"], b["mdf"]),
            "mnf_drop": -_pct(v["mnf"], b["mnf"]),
            "finsm5_rise": _pct(v["finsm5"], b["finsm5"]),
            "rms_rise": _pct(v["rms"], b["rms"]),
            "lift_time_rise": _pct(v["lift_s"], b["lift_s"]),
            "peak_angle_drop": -_pct(v["peak"], b["peak"]),
            # SPARC is negative; more negative = less smooth = positive "drop".
            "sparc_drop": -_pct(v["sparc"], b["sparc"]) if math.isfinite(b["sparc"]) else NAN,
        }
        return rel

    def update(self, rep) -> tuple[float, str, str, dict]:
        """Scores a valid rep. Returns (smoothed score, zone, JASA label, relative features)."""
        v = self.rep_vector(rep)
        n_base = self.cfg["baseline_reps"]
        if self.baseline is None:
            self.baseline_reps.append(v)
            if len(self.baseline_reps) >= n_base:
                self.baseline = {k: float(np.nanmean([r[k] for r in self.baseline_reps]))
                                 if any(math.isfinite(r[k]) for r in self.baseline_reps) else NAN
                                 for k in v}
            self.ewma = 0.0
            self.history.append((rep.index, 0.0))
            return 0.0, "green", "baseline", {}
        rel = self.relative(v)
        jasa = jasa_label(rel["rms_rise"], -rel["mdf_drop"], self.cfg["jasa_threshold_pct"]) if self.mode == "raw" else ""
        model_in = dict(rel)
        if jasa == "force increase":
            # RMS rose with MDF: more effort, not fatigue, so the amplitude term is ignored.
            model_in["rms_rise"] = 0.0
        raw = float(self.model.predict(model_in))
        a = self.cfg["ewma_alpha"]
        self.ewma = raw if not math.isfinite(self.ewma) else a * raw + (1 - a) * self.ewma
        self.history.append((rep.index, self.ewma))
        return self.ewma, zone_for(self.ewma, self.cfg), jasa, rel
