"""Session summary report (self-contained HTML), written however many reps were counted.

The fatigue section explains the score with the numbers actually used: the configured rule
weights, the per-session baseline and, for the last scored rep, each term's contribution.
With fewer counted reps than the baseline needs, the trend is a demo estimate over every
attempt (counted or not) and is labelled as such.
"""
from __future__ import annotations

import html
import json
import math
from datetime import datetime
from pathlib import Path

from . import faults as F
from .fatigue import FatigueScorer, load_fatigue_model, zone_for
from .config import HOST_DIR

NAN = float("nan")

TERM_NAMES = {
    "mdf_drop": "Median frequency drop (MDF)",
    "finsm5_rise": "Spectral fatigue index rise (FInsm5)",
    "rms_rise": "Muscle effort rise (RMS, %RVC)",
    "lift_time_rise": "Lift time rise",
    "peak_angle_drop": "Peak angle drop",
    "sparc_drop": "Smoothness drop (SPARC)",
}


OFF = 999                       # exercise configs switch a check off with a huge limit (e.g. 1000)


def criteria(cfg: dict) -> list[dict]:
    """Every rep check with the thresholds in force, in the fault priority order.
    `enabled` is False where this exercise switches the check off."""
    sm, fc = cfg["state_machine"], cfg["faults"]
    msgs = fc.get("messages")
    on = lambda *v: all(x < OFF for x in v)   # noqa: E731
    rows = {
        F.SIGNAL_POOR: ("Muscle and motion signals stay clean for the whole rep (no electrode lift-off, "
                        "clipping, mains hum or IMU dropout)", {}, True),
        F.FOREARM_LIFT: ("The arm stays put: off-hinge rotation below {forearm_offplane_dps} °/s and extra "
                         "acceleration below {forearm_acc_g} g (each allowed for {forearm_min_s} s)",
                         {k: fc[k] for k in ("forearm_offplane_dps", "forearm_acc_g", "forearm_min_s")}, True),
        F.NO_MUSCLE: ("The muscle does the work: mean activation at least {min_activation_pct} % of the "
                      "reference contraction, switching on no later than {max_onset_lag_s} s after the movement starts",
                      {k: fc[k] for k in ("min_activation_pct", "max_onset_lag_s")}, True),
        F.OVER_FLEX: ("Not too far: peak below target + {over_flex_margin_deg}° and below personal range + "
                      "{rom_margin_deg}°", {k: fc[k] for k in ("over_flex_margin_deg", "rom_margin_deg")},
                      on(fc["over_flex_margin_deg"])),
        F.WRIST_TWIST: ("No twisting: roll stays within {roll_max_deg}° of the start pose",
                        {"roll_max_deg": fc["roll_max_deg"]}, on(fc["roll_max_deg"])),
        F.SIDE_BEND: ("No sideways bend: yaw stays within {yaw_max_deg}°", {"yaw_max_deg": fc["yaw_max_deg"]},
                      on(fc["yaw_max_deg"])),
        F.INCOMPLETE_ROM: ("Full range: peak reaches the target minus {target_tol_deg}°",
                           {"target_tol_deg": sm["target_tol_deg"]}, True),
        F.NOT_RETURNED: ("Back to the start: below {bottom_deg}° before the next lift",
                         {"bottom_deg": sm["bottom_deg"]}, True),
        F.TIMEOUT: ("Rep finished within {max_rep_s} s", {"max_rep_s": sm["max_rep_s"]}, True),
        F.EARLY_DOWN: ("Pause at the top: held still for at least {min_hold_s} s (within {hold_band_deg}° of the "
                       "peak, moving under {hold_omega_dps} °/s)",
                       {"min_hold_s": fc["min_hold_s"], "hold_band_deg": sm["hold_band_deg"],
                        "hold_omega_dps": sm["hold_omega_dps"]}, on(fc["min_hold_s"])),
        F.SWING_FAST: ("Controlled lift: at least {min_lift_s} s up and never faster than {max_lift_omega_dps} °/s",
                       {k: fc[k] for k in ("min_lift_s", "max_lift_omega_dps")}, on(fc["max_lift_omega_dps"])),
        F.DROP_FAST: ("Controlled lowering: at least {min_lower_s} s down and never faster than "
                      "{max_lower_omega_dps} °/s", {k: fc[k] for k in ("min_lower_s", "max_lower_omega_dps")},
                      on(fc["max_lower_omega_dps"])),
        F.JERKY: ("Smooth movement: spectral arc length (SPARC) above {sparc_min}", {"sparc_min": fc["sparc_min"]},
                  fc["sparc_min"] > -OFF),
    }
    out = []
    for code in fc["priority"]:
        if code in rows:
            rule, th, enabled = rows[code]
            out.append({"code": code, "tip": F.message_for(code, msgs), "rule": rule.format(**th),
                        "thresholds": th, "enabled": bool(enabled)})
    return out


def _num(v, nd=3):
    """JSON-safe number: NaN/inf -> None."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return round(float(v), nd) if math.isfinite(v) else None
    return v


def build_summary_json(summary: dict, reps: list, cfg: dict, user: str, exercise: str, emg_mode: str,
                       profile=None) -> dict:
    """Compact, machine-readable session summary, e.g. to hand to an AI model for coaching
    feedback. Numbers only, no raw signals; `user` is whatever name the app was given."""
    reps = [r for r in reps if not r.practice]
    pts, source, scorer, last_rel = fatigue_trend(reps, cfg, emg_mode)
    fc = cfg["fatigue"]
    final = pts[-1][1] if pts else NAN
    crit = criteria(cfg)
    counts = summary.get("fault_counts", {})
    ex = cfg["exercise"]
    return {
        "schema": "kinesis.session_summary/1",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "session": {
            "user": user, "exercise": exercise, "emg_mode": emg_mode,
            "target_deg": _num(profile.target_deg if profile else ex["target_deg"], 1),
            "personal_rom_deg": _num(profile.rom_deg, 1) if profile else None,
            "load_kg": _num(profile.load_kg if profile else ex["load_kg"], 2),
            "planned": {"sets": min(ex["sets"], ex["max_sets"]), "reps_per_set": ex["reps_per_set"],
                        "tempo_s": ex["tempo"]},
            "muscle_reference_calibrated": bool(profile and math.isfinite(profile.rvc)),
        },
        "totals": {
            "sets_done": summary.get("sets", 0), "attempts": summary.get("attempts", len(reps)),
            "good_reps": summary.get("valid", 0), "not_counted": summary.get("invalid", 0),
            "good_rate": _num(summary.get("valid", 0) / len(reps), 3) if reps else None,
            "avg_peak_angle_deg": _num(summary.get("avg_peak_angle", NAN), 1),
            "most_common_fault": summary.get("most_common_fault", "") or None,
        },
        "faults": [{"code": c["code"], "tip": c["tip"], "reps": counts.get(c["code"], 0)}
                   for c in crit if counts.get(c["code"], 0)],
        "criteria": crit,
        "fatigue": {
            "source": source or None,         # counted | demo (all attempts, few counted) | None
            "final_score": _num(final, 1), "zone": zone_for(final, fc) or None,
            "zones": {"green_below": fc["amber"], "red_above": fc["red"]},
            "baseline_reps": fc["baseline_reps"], "ewma_alpha": fc["ewma_alpha"],
            "weights": fc["weights_raw"] if emg_mode == "raw" else fc["weights_envelope"],
            "full_scale_pct": fc["full_scale_pct"],
            "baseline": {k: _num(v) for k, v in (scorer.baseline or {}).items()} if scorer else None,
            "last_rep_changes_pct": {k: _num(v, 1) for k, v in last_rel.items()},
            "trend": [{"rep": i, "score": _num(s, 1)} for i, s in pts],
        },
        "reps": [{
            "index": r.index, "set": r.set_no, "counted": r.valid, "faults": r.faults,
            "tip": None if r.valid else r.message,
            "peak_deg": _num(r.peak_theta, 1), "lift_s": _num(r.lift_s, 2), "hold_s": _num(r.hold_s, 2),
            "lower_s": _num(r.lower_s, 2), "peak_speed_up_dps": _num(r.peak_omega_lift, 0),
            "peak_speed_down_dps": _num(r.peak_omega_lower, 0), "muscle_pct_rvc": _num(r.mean_pct, 0),
            "muscle_onset_lag_s": _num(r.onset_lag_s, 3), "max_twist_deg": _num(r.max_roll_dev, 1),
            "max_side_deg": _num(r.max_yaw_dev, 1), "smoothness_sparc": _num(r.sparc, 2),
            "mdf_hz": _num(r.features.get("mdf", NAN), 1), "fatigue_score": _num(r.fatigue_score, 1),
        } for r in reps],
    }


def _fmt(v, nd=1, unit=""):
    return f"{v:.{nd}f}{unit}" if isinstance(v, (int, float)) and math.isfinite(v) else "–"


def fatigue_trend(reps: list, cfg: dict, emg_mode: str) -> tuple[list, str, FatigueScorer | None, dict]:
    """(points [(rep index, score)], source, scorer, last relative features).

    source: "counted" (the live score over counted reps), "demo" (re-scored over all attempts
    because too few were counted; without EMG features only the movement terms score) or ""
    (no attempts at all)."""
    fc = cfg["fatigue"]
    valid = [r for r in reps if r.valid and math.isfinite(r.fatigue_score)]
    pool, source = (valid, "counted") if len(valid) > fc["baseline_reps"] else (list(reps), "demo")
    if not pool:
        return [], "", None, {}
    scorer = FatigueScorer(fc, emg_mode, load_fatigue_model(fc, emg_mode, HOST_DIR))
    pts, last_rel = [], {}
    for r in sorted(pool, key=lambda r: r.index):
        score, _, _, rel = scorer.update(r)
        pts.append((r.index, score))
        if rel:
            last_rel = rel
    if source == "counted":
        pts = [(r.index, r.fatigue_score) for r in sorted(valid, key=lambda r: r.index)]
    return pts, source, scorer, last_rel


def load_raw_emg(session_dir, t_lo: float, t_hi: float, bins: int = 900):
    """Raw sEMG (ADC counts) from the session's emg.csv as per-bin (t, min, max), so every
    spike survives the downsampling. None if the file is missing."""
    p = Path(session_dir) / "emg.csv" if session_dir else None
    if p is None or not p.exists():
        return None
    import numpy as np
    try:
        d = np.loadtxt(p, delimiter=",", skiprows=1, ndmin=2)
    except ValueError:
        return None
    d = d[(d[:, 0] >= t_lo) & (d[:, 0] <= t_hi)]
    if len(d) < 2:
        return None
    edges = np.linspace(d[0, 0], d[-1, 0], bins + 1)
    idx = np.clip(np.searchsorted(edges, d[:, 0], side="right") - 1, 0, bins - 1)
    lo = np.full(bins, np.nan)
    hi = np.full(bins, np.nan)
    np.fmin.at(lo, idx, d[:, 1])
    np.fmax.at(hi, idx, d[:, 1])
    keep = np.isfinite(lo)
    return (edges[:-1] + edges[1:])[keep] / 2, lo[keep], hi[keep]


def _decimate(trace: list, n: int = 1500):
    step = max(1, len(trace) // n)
    return trace[::step]


def _series_svg(title: str, unit: str, t0: float, t1: float, reps: list, line=None, band=None,
                hlines=(), y_range=None, x_label=False) -> str:
    """One time-series panel: optional line [(t, y)], optional min/max band (t, lo, hi), rep
    spans shaded green (counted) / red (not counted) and dashed reference lines [(y, label)]."""
    w, h, pl, pr, pt_, pb = 880, 190 if x_label else 170, 52, 12, 22, 30 if x_label else 10
    ys = []
    if line:
        ys += [y for _, y in line if math.isfinite(y)]
    if band is not None:
        ys += list(band[1]) + list(band[2])
    if not ys:
        return ""
    lo, hi = y_range if y_range else (min(ys), max(ys))
    lo = min(lo, *(v for v, _ in hlines)) if hlines else lo
    hi = max(hi, *(v for v, _ in hlines)) if hlines else hi
    if hi - lo < 1e-9:
        hi = lo + 1
    span = max(t1 - t0, 1e-9)
    X = lambda t: pl + (t - t0) / span * (w - pl - pr)            # noqa: E731
    Y = lambda v: pt_ + (1 - (min(max(v, lo), hi) - lo) / (hi - lo)) * (h - pt_ - pb)   # noqa: E731
    parts = [f"<text x='{pl}' y='14' class='ttl'>{html.escape(title)}</text>"]
    for r in reps:
        if math.isfinite(r.t_start) and math.isfinite(r.t_end):
            parts.append(f"<rect x='{X(r.t_start):.1f}' y='{pt_}' width='{max(1.0, X(r.t_end) - X(r.t_start)):.1f}' "
                         f"height='{h - pt_ - pb}' class='{'span-ok' if r.valid else 'span-bad'}'>"
                         f"<title>Rep {r.index}: {'counted' if r.valid else html.escape(r.message)}</title></rect>")
    if band is not None:
        bt, blo, bhi = band
        pts = [f"{X(t):.1f},{Y(v):.1f}" for t, v in zip(bt, bhi)] + \
              [f"{X(t):.1f},{Y(v):.1f}" for t, v in zip(bt[::-1], blo[::-1])]
        parts.append(f"<polygon points='{' '.join(pts)}' class='band'/>")
    if line:
        seg, segs = [], []
        for t, y in line:
            if math.isfinite(y):
                seg.append(f"{X(t):.1f},{Y(y):.1f}")
            elif seg:
                segs.append(seg)
                seg = []
        segs.append(seg)
        parts += [f"<polyline points='{' '.join(s)}' class='sig'/>" for s in segs if len(s) > 1]
    for v, lab in hlines:
        parts.append(f"<line x1='{pl}' x2='{w - pr}' y1='{Y(v):.1f}' y2='{Y(v):.1f}' class='ref'/>"
                     f"<text x='{w - pr - 4}' y='{Y(v) - 4:.1f}' class='ax' text-anchor='end'>{html.escape(lab)}</text>")
    for v in (lo, (lo + hi) / 2, hi):
        parts.append(f"<text x='{pl - 6}' y='{Y(v) + 4:.1f}' class='ax' text-anchor='end'>{round(v) + 0:.0f}</text>")
    parts.append(f"<text x='14' y='{(h - pb + pt_) / 2:.1f}' class='ax' transform='rotate(-90 14 {(h - pb + pt_) / 2:.1f})' "
                 f"text-anchor='middle'>{html.escape(unit)}</text>")
    if x_label:
        # Round tick step (5, 10, 15, 30, 60 … s) giving about six labels.
        step = next((s for s in (5, 10, 15, 30, 60, 120, 300, 600) if span / s <= 7), 1200)
        k = 0
        while k * step <= span + 1e-9:
            parts.append(f"<text x='{X(t0 + k * step):.1f}' y='{h - 10}' class='ax' text-anchor='middle'>{k * step}</text>")
            k += 1
        parts.append(f"<text x='{w - pr}' y='{h - 10}' class='ax' text-anchor='end'>s</text>")
    return f"<svg viewBox='0 0 {w} {h}' class='chart' role='img' aria-label='{html.escape(title)}'>{''.join(parts)}</svg>"


def session_graphs(trace: list, reps: list, cfg: dict, target: float, session_dir=None) -> str:
    """Whole-session angle (IMU), muscle activation (sEMG envelope) and raw sEMG panels."""
    if not trace:
        return "<p class='muted'>No session signals were recorded (no set was started).</p>"
    t0, t1 = trace[0][0], trace[-1][0]
    d = _decimate(trace)
    reps = [r for r in reps if not r.practice]
    out = [_series_svg("Angle from the motion sensor (IMU)", "deg", t0, t1, reps,
                       line=[(t, th) for t, th, _ in d], hlines=[(target, f"target {target:.0f}°")]),
           _series_svg("Muscle activation (sEMG envelope)", "% RVC", t0, t1, reps,
                       line=[(t, e) for t, _, e in d],
                       hlines=[(cfg["faults"]["min_activation_pct"], f"min {cfg['faults']['min_activation_pct']}%")],
                       y_range=(0, max(120.0, max((e for _, _, e in d if math.isfinite(e)), default=0.0))),
                       x_label=session_dir is None)]
    raw = load_raw_emg(session_dir, t0, t1) if session_dir else None
    if raw is not None:
        out.append(_series_svg("Raw sEMG (ADC counts, 1 kHz, min–max per bin)", "ADC", t0, t1, reps, band=raw,
                               y_range=(0, cfg["emg"]["adc_max"]), x_label=True))
    legend = ("<p class='muted'><span class='key ok'></span> counted rep &nbsp; <span class='key bad'></span> "
              "not counted (hover a band for the tip) &nbsp; dashed = target / minimum activation</p>")
    return "".join(out) + legend


def _chart_svg(points: list, cfg: dict) -> str:
    if not points:
        return "<p class='muted'>No fatigue trend: no attempts were recorded in this session.</p>"
    w, h, pad = 640, 220, 34
    n = max(len(points) - 1, 1)
    xs = [pad + i * (w - 2 * pad) / n for i in range(len(points))]
    ys = [h - pad - max(0.0, min(100.0, s)) / 100 * (h - 2 * pad) for _, s in points]
    amber, red = cfg["fatigue"]["amber"], cfg["fatigue"]["red"]

    def y(v):
        return h - pad - v / 100 * (h - 2 * pad)
    bands = (f"<rect x='{pad}' y='{y(100)}' width='{w - 2 * pad}' height='{y(red) - y(100)}' class='b-red'/>"
             f"<rect x='{pad}' y='{y(red)}' width='{w - 2 * pad}' height='{y(amber) - y(red)}' class='b-amber'/>"
             f"<rect x='{pad}' y='{y(amber)}' width='{w - 2 * pad}' height='{y(0) - y(amber)}' class='b-green'/>")
    line = " ".join(f"{x:.1f},{yy:.1f}" for x, yy in zip(xs, ys))
    dots = "".join(f"<circle cx='{x:.1f}' cy='{yy:.1f}' r='4' class='dot'><title>Rep {i}: {s:.0f}</title></circle>"
                   for x, yy, (i, s) in zip(xs, ys, points))
    labels = "".join(f"<text x='{x:.1f}' y='{h - 12}' class='ax' text-anchor='middle'>{i}</text>"
                     for x, (i, _) in zip(xs, points))
    ticks = "".join(f"<text x='{pad - 6}' y='{y(v) + 4:.1f}' class='ax' text-anchor='end'>{v}</text>"
                    for v in (0, amber, red, 100))
    return (f"<svg viewBox='0 0 {w} {h}' class='chart' role='img' aria-label='Fatigue score per rep'>"
            f"{bands}<polyline points='{line}' class='line'/>{dots}{labels}{ticks}"
            f"<text x='{w / 2}' y='{h - 0}' class='ax' text-anchor='middle'>rep</text></svg>")


def _explain(cfg: dict, emg_mode: str, scorer: FatigueScorer | None, last_rel: dict, source: str) -> str:
    fc = cfg["fatigue"]
    raw = emg_mode == "raw"
    weights = fc["weights_raw"] if raw else fc["weights_envelope"]
    wsum = sum(weights.values()) or 1.0
    rule = str(fc.get("model", "rule")) == "rule"
    rows = []
    for k, wgt in weights.items():
        v = last_rel.get(k, NAN)
        scaled = min(1.0, max(0.0, v / fc["full_scale_pct"][k])) if math.isfinite(v) else NAN
        contrib = 100.0 * wgt * scaled / wsum if math.isfinite(scaled) else NAN
        rows.append(f"<tr><td>{TERM_NAMES.get(k, k)}</td><td>{wgt:g}</td><td>{fc['full_scale_pct'][k]:g}%</td>"
                    f"<td>{_fmt(v, 1, '%')}</td><td>{_fmt(scaled, 2)}</td><td>{_fmt(contrib, 1)}</td></tr>")
    base = scorer.baseline if scorer and scorer.baseline else {}
    base_txt = ", ".join(f"{k} {_fmt(base.get(k), 2)}" for k in ("mdf", "rms", "lift_s", "peak", "sparc")) or "not set"
    demo = ("<p class='note'><b>Demo estimate.</b> Fewer than "
            f"{fc['baseline_reps'] + 1} reps were counted, so this trend was re-scored over every attempt, "
            "including the ones that were not counted. Use it to show how the score works, not as a "
            "clinical result.</p>") if source == "demo" else ""
    model_txt = ("the built-in weighted rule below" if rule else
                 f"a trained model (<code>{html.escape(str(fc['model']))}</code>); the rule table below is "
                 "shown for reference")
    return f"""
<h2>How the fatigue score is calculated</h2>
{demo}
<ol>
<li><b>Same slice of every rep.</b> For each rep the app takes the muscle signal only while the
angle passes through {fc['window_deg'][0]}°–{fc['window_deg'][1]}° on the way up, so every rep
is compared over the same part of the movement.</li>
<li><b>Features of that slice.</b> {"From the raw EMG: median frequency (MDF), the spectral index FInsm5 and the effort level (RMS as % of the reference contraction, %RVC)." if raw else "This sensor only gives the envelope, so only the effort level (% of the reference contraction) is used; frequency features are off."}
Plus movement features: lift time, peak angle and smoothness (SPARC).</li>
<li><b>Baseline.</b> The first {fc['baseline_reps']} scored reps of the session are averaged into a
personal baseline and score 0. This session's baseline: {base_txt}.</li>
<li><b>Change from baseline.</b> Every later rep is turned into percentage changes in the
"more tired" direction: MDF falling, FInsm5 rising, effort rising, lifts getting slower, the peak
angle dropping, movement getting less smooth.</li>
<li><b>Weighted score.</b> Each change is divided by its full-scale value and clipped to 0–1,
multiplied by its weight, and the weighted sum is scaled to 0–100 (here: {model_txt}).
{"If effort rises <i>together with</i> MDF (the JASA “force increase” pattern), that is more effort rather than fatigue, so the effort term is set to 0 for that rep." if raw else ""}</li>
<li><b>Smoothing.</b> The score shown is an exponential moving average
(α = {fc['ewma_alpha']}) so one odd rep does not swing it.</li>
<li><b>Zones.</b> Green below {fc['amber']}, amber {fc['amber']}–{fc['red']}, red above {fc['red']}.</li>
</ol>
<h3>Last scored rep, term by term</h3>
<table><thead><tr><th>Term</th><th>Weight</th><th>Full scale</th><th>Change</th><th>Scaled 0–1</th>
<th>Points</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p class='muted'>Points add up to the raw score of that rep before smoothing. “–” = not measured
(for example a baseline rep, or no frequency features in envelope mode).</p>"""


def build_report(summary: dict, reps: list, cfg: dict, user: str, exercise: str, emg_mode: str,
                 trace: list | None = None, session_dir=None, target: float | None = None) -> str:
    reps = [r for r in reps if not r.practice]
    target = target if target is not None else cfg["exercise"]["target_deg"]
    graphs = session_graphs(trace or [], reps, cfg, target, session_dir)
    pts, source, scorer, last_rel = fatigue_trend(reps, cfg, emg_mode)
    final = pts[-1][1] if pts else NAN
    zone = zone_for(final, cfg["fatigue"])
    msgs = cfg["faults"].get("messages")
    fault = summary.get("most_common_fault", "")
    counts = summary.get("fault_counts", {})
    crit_rows = "".join(
        f"<tr><td>{c['code']}</td><td>{html.escape(c['tip'])}</td>"
        f"<td style='white-space:normal'>{html.escape(c['rule']) if c['enabled'] else '<i>Off for this exercise</i>'}</td>"
        f"<td>{counts.get(c['code'], 0)}</td></tr>" for c in criteria(cfg))
    fault_rows = "".join(f"<tr><td>{html.escape(F.message_for(k, msgs))}</td><td>{k}</td><td>{n}</td></tr>"
                         for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    rep_rows = "".join(
        f"<tr class='{'ok' if r.valid else 'bad'}'><td>{r.index}</td><td>{r.set_no}</td>"
        f"<td>{'Yes' if r.valid else 'No'}</td><td>{_fmt(r.peak_theta, 0, '°')}</td><td>{_fmt(r.lift_s, 1)}</td>"
        f"<td>{_fmt(r.hold_s, 1)}</td><td>{_fmt(r.lower_s, 1)}</td><td>{_fmt(r.mean_pct, 0)}</td>"
        f"<td>{_fmt(r.features.get('mdf', NAN), 1)}</td><td>{_fmt(r.fatigue_score, 0)}</td>"
        f"<td>{html.escape(r.message if not r.valid else 'Good')}</td></tr>" for r in reps)
    zone_word = {"green": "Fresh", "amber": "Getting tired", "red": "Tired, rest"}.get(zone, "Not enough data")
    src = {"counted": "from counted reps", "demo": "demo estimate (all attempts)", "": ""}[source]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Session report</title>
<style>
:root {{ --bg:#f7f7f5; --card:#fff; --text:#1d2327; --muted:#69717a; --line:#e3e5e8; --accent:#2f6fde;
  --good:#1f9d55; --bad:#d64545; --amber:#e0a100; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#15181b; --card:#1e2226; --text:#e8eaed; --muted:#9aa3ad;
  --line:#2e3439; --accent:#6c9bff; }} }}
body {{ margin:0; background:var(--bg); color:var(--text); font:16px/1.5 system-ui, "Segoe UI", sans-serif; }}
main {{ max-width:920px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ margin:0 0 4px; font-size:28px; }} h2 {{ margin:32px 0 8px; font-size:21px; }} h3 {{ font-size:17px; }}
.muted {{ color:var(--muted); font-size:14px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-top:16px; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
.tile b {{ display:block; font-size:26px; font-variant-numeric:tabular-nums; }}
.tile span {{ color:var(--muted); font-size:13px; text-transform:uppercase; letter-spacing:.04em; }}
.zone-green {{ color:var(--good); }} .zone-amber {{ color:var(--amber); }} .zone-red {{ color:var(--bad); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; font-variant-numeric:tabular-nums; }}
th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }}
tr.bad td:first-child {{ border-left:3px solid var(--bad); }} tr.ok td:first-child {{ border-left:3px solid var(--good); }}
.note {{ background:color-mix(in srgb, var(--amber) 14%, var(--card)); border-left:4px solid var(--amber);
  padding:10px 12px; border-radius:6px; }}
.chart {{ width:100%; height:auto; }} .line {{ fill:none; stroke:var(--accent); stroke-width:2.5; }}
.dot {{ fill:var(--accent); }} .ax {{ fill:var(--muted); font-size:12px; }}
.b-green {{ fill:var(--good); opacity:.08; }} .b-amber {{ fill:var(--amber); opacity:.12; }} .b-red {{ fill:var(--bad); opacity:.12; }}
ol li {{ margin-bottom:6px; }}
.ttl {{ fill:var(--text); font-size:13px; font-weight:600; }} .sig {{ fill:none; stroke:var(--accent); stroke-width:1.3; }}
.band {{ fill:#b97a1c; opacity:.55; }} .ref {{ stroke:var(--muted); stroke-dasharray:5 4; stroke-width:1; }}
.span-ok {{ fill:var(--good); opacity:.13; }} .span-bad {{ fill:var(--bad); opacity:.15; }}
.key {{ display:inline-block; width:12px; height:12px; border-radius:2px; vertical-align:-1px; }}
.key.ok {{ background:color-mix(in srgb, var(--good) 35%, transparent); }}
.key.bad {{ background:color-mix(in srgb, var(--bad) 35%, transparent); }}
</style></head><body><main>
<h1>Session report · {html.escape(exercise)}</h1>
<div class="muted">{html.escape(user)} · {now} · EMG mode: {emg_mode}</div>
<div class="tiles">
<div class="tile"><span>Good reps</span><b>{summary.get('valid', 0)}</b></div>
<div class="tile"><span>Not counted</span><b>{summary.get('invalid', 0)}</b></div>
<div class="tile"><span>Sets</span><b>{summary.get('sets', 0)}</b></div>
<div class="tile"><span>Average angle</span><b>{_fmt(summary.get('avg_peak_angle', NAN), 0, '°')}</b></div>
<div class="tile"><span>Fatigue</span><b class="zone-{zone}">{_fmt(final, 0)}</b><span>{zone_word}{' · ' + src if src else ''}</span></div>
</div>
<h2>Whole session</h2>
<div class="card">{graphs}</div>
<h2>Fatigue trend</h2>
<div class="card">{_chart_svg(pts, cfg)}</div>
{_explain(cfg, emg_mode, scorer, last_rel, source)}
<h2>What makes a rep count</h2>
<p class="muted">A rep counts only if it passes every check that is on. A failed rep shows the tip of the
first failed check in this order.</p>
<div class="card"><table><thead><tr><th>Check</th><th>Tip shown</th><th>Rule (thresholds in force)</th><th>Failed</th></tr></thead><tbody>{crit_rows}</tbody></table></div>
<h2>Tips from this session</h2>
<div class="card">{"<table><thead><tr><th>Tip</th><th>Code</th><th>Reps</th></tr></thead><tbody>" + fault_rows + "</tbody></table>" if fault_rows else "<p>No faults, great work.</p>"}
{f"<p class='muted'>Most common: {html.escape(F.message_for(fault, msgs))}</p>" if fault else ""}</div>
<h2>Every attempt</h2>
<div class="card">{"<table><thead><tr><th>#</th><th>Set</th><th>Counted</th><th>Peak</th><th>Lift s</th><th>Hold s</th><th>Lower s</th><th>%RVC</th><th>MDF Hz</th><th>Fatigue</th><th>Result</th></tr></thead><tbody>" + rep_rows + "</tbody></table>" if rep_rows else "<p>No attempts were recorded in this session.</p>"}</div>
</main></body></html>"""


def write_report(path: str | Path, summary: dict, reps: list, cfg: dict, user: str, exercise: str,
                 emg_mode: str, profile=None, trace: list | None = None, session_dir=None) -> Path:
    """Writes the HTML report and, next to it, the same summary as JSON (`<name>.json`).
    trace: [(t, theta, env_pct)] for the whole-session graphs; session_dir adds raw sEMG."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    target = profile.target_deg if profile is not None else None
    p.write_text(build_report(summary, reps, cfg, user, exercise, emg_mode, trace, session_dir, target),
                 encoding="utf-8")
    data = build_summary_json(summary, reps, cfg, user, exercise, emg_mode, profile)
    if trace:
        t0 = trace[0][0]
        data["signals"] = {     # whole session, decimated: enough to see the shape of every rep
            "rate_hz": None, "t_s": [], "angle_deg": [], "muscle_pct_rvc": []}
        d = _decimate(trace, 1200)
        if len(d) > 1:
            data["signals"]["rate_hz"] = _num((len(d) - 1) / max(d[-1][0] - d[0][0], 1e-9), 2)
        for t, th, e in d:
            data["signals"]["t_s"].append(_num(t - t0, 2))
            data["signals"]["angle_deg"].append(_num(th, 1))
            data["signals"]["muscle_pct_rvc"].append(_num(e, 1))
    p.with_suffix(".json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return p
