"""Train a linear fatigue model from logged sessions (healthy volunteers, sets to near-failure).

Label for each valid rep = rep_index / total_reps within its set. Features are the same
baseline-relative changes the rule model uses (fatigue.REL_FEATURES), so the trained model
drops straight into config.yaml:

    python train_fatigue.py sessions/vol01 sessions/vol02 --out models/fatigue.json
    # config.yaml ->  fatigue: {model: "json:models/fatigue.json"}

Only use data from healthy volunteers who agreed to exercise to near-failure.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

from wristmon.config import load_config
from wristmon.datatypes import RepRecord
from wristmon.fatigue import REL_FEATURES, FatigueScorer


def load_reps(session: Path) -> list[RepRecord]:
    reps = []
    with open(session / "reps.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["valid"] != "True":
                continue
            num = lambda k: float(row[k]) if row.get(k) not in (None, "") else math.nan
            r = RepRecord(index=int(row["index"]), set_no=int(row["set_no"]), valid=True,
                          lift_s=num("lift_s"), peak_theta=num("peak_theta"), sparc=num("sparc"))
            r.features = {k: num(k) for k in RepRecord.FEATURE_FIELDS}
            reps.append(r)
    return reps


def dataset(sessions: list[Path], cfg: dict):
    X, y = [], []
    for s in sessions:
        reps = load_reps(s)
        mode = json.loads((s / "meta.json").read_text()).get("emg_mode", "raw") if (s / "meta.json").exists() else "raw"
        for set_no in sorted({r.set_no for r in reps}):
            set_reps = [r for r in reps if r.set_no == set_no]
            if len(set_reps) <= cfg["fatigue"]["baseline_reps"]:
                continue
            scorer = FatigueScorer(cfg["fatigue"], mode)
            total = len(set_reps)
            for i, r in enumerate(set_reps, start=1):
                v = scorer.rep_vector(r)
                if scorer.baseline is None:
                    scorer.update(r)
                    continue
                rel = scorer.relative(v)
                X.append([rel[k] if math.isfinite(rel[k]) else 0.0 for k in REL_FEATURES])
                y.append(i / total)
    return np.array(X), np.array(y)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessions", nargs="+", help="session folders or folders containing them")
    ap.add_argument("--out", default="models/fatigue.json")
    ap.add_argument("--ridge", type=float, default=1.0, help="L2 regularisation")
    args = ap.parse_args(argv)
    cfg = load_config()
    dirs = []
    for s in map(Path, args.sessions):
        dirs += [p.parent for p in s.rglob("reps.csv")]
    X, y = dataset(dirs, cfg)
    if len(y) < 10:
        print(f"only {len(y)} training reps found; need sets to near-failure from several volunteers")
        return 1
    A = np.hstack([X, np.ones((len(X), 1))])
    reg = args.ridge * np.eye(A.shape[1])
    reg[-1, -1] = 0
    w = np.linalg.solve(A.T @ A + reg, A.T @ y)
    pred = A @ w
    r2 = 1 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"features": REL_FEATURES, "weights": w[:-1].tolist(), "bias": float(w[-1]),
                               "logistic": False, "scale": 100, "n_reps": int(len(y)), "r2_train": float(r2)},
                              indent=2))
    print(f"trained on {len(y)} reps from {len(dirs)} sessions, train R^2 = {r2:.2f} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
