"""Replay a recorded session through the full pipeline, without hardware.

    python replay.py sessions/sim/20261023_101500
    python replay.py <session> --config my_thresholds.yaml   # re-tune and compare
    python replay.py <session> --recalibrate                 # also recompute calibration from the data
    python replay.py <session> --gui                         # watch it in the GUI in real time
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from wristmon.config import load_config
from wristmon.headless import rep_table, replay_session


def recorded_reps(session: Path) -> list[dict]:
    p = session / "reps.csv"
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session")
    ap.add_argument("--config", help="config to replay with (default: the session's own snapshot)")
    ap.add_argument("--recalibrate", action="store_true")
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0)
    args = ap.parse_args(argv)
    session = Path(args.session)

    if args.gui:
        from wristmon.gui.app import main as gui_main
        extra = ["--config", args.config] if args.config else []
        return gui_main(["--replay", str(session), "--speed", str(args.speed), "--windowed", *extra])

    cfg = load_config(args.config or (session / "config.yaml"))
    res = replay_session(session, cfg, recalibrate=args.recalibrate)
    reps = res.counted
    print(rep_table(reps))
    faults = Counter(r.primary_fault for r in reps if not r.valid)
    print(f"\nvalid {res.valid_count} / attempts {len(reps)}")
    if faults:
        print("rejections:", ", ".join(f"{k} x{v}" for k, v in faults.most_common()))

    rec = recorded_reps(session)
    if rec:
        live = [(r["valid"], r["faults"]) for r in rec if r["practice"] != "True"]
        now = [(str(r.valid), "|".join(r.faults)) for r in reps]
        same = live == now
        print(f"matches recorded reps.csv: {'yes' if same else 'NO'} ({len(live)} recorded, {len(now)} replayed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
