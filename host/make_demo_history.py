"""Builds a SIMULATED patient history for the specialist dashboard demo (no hardware).

    python make_demo_history.py                 # -> <publish.dir>/DEMO_sim/se<date>_<time>/ x 6
    python make_demo_history.py --patient DEMO_b --days 0 4 9 15

Each session goes through the real pipeline (calibration, rep judging, fatigue, report) with
the virtual patient, and is published exactly like the GUI does after a summary. The session
folders are dated in the past so the dashboard can show a rehab timeframe. Sessions 1-5 get
steadily better; the last one declines (fast fatigue, assisted lifts, compensation) so the
progress report and risk flags have something to find. Delete the patient folder to remove it.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from wristmon import exercises as EX
from wristmon import headless as H
from wristmon.config import load_config
from wristmon.publish import publish_root, publish_session
from wristmon.report import write_report

# (rep kinds, reps until fully fatigued) per session: improving, then a bad day.
PLAN = [
    (["good"] * 5 + ["early_down"] * 3 + ["swing_fast"] * 2 + ["forearm_lift"] * 2, 200),
    (["good"] * 7 + ["early_down"] * 2 + ["swing_fast"] + ["forearm_lift"] * 2, 200),
    (["good"] * 9 + ["early_down"] * 2 + ["drop_fast"], 200),
    (["good"] * 10 + ["early_down"] + ["drop_fast"], 200),
    (["good"] * 11 + ["early_down"], 200),
    # Bad day: the faults are spread through the set and fatigue builds within it.
    (["good", "incomplete", "good", "passive", "good", "forearm_lift", "good", "passive",
      "incomplete", "good", "passive", "forearm_lift", "incomplete", "early_down"], 14),
]
# Arm-sized movement for the virtual patient (its defaults are a wrist curl).
PATIENT = {"bicep_curl": {"target_deg": 90.0, "rom_deg": 104.0}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patient", default="DEMO_sim")
    ap.add_argument("--exercise", default="bicep_curl", choices=sorted(EX.EXERCISES))
    ap.add_argument("--days", type=int, nargs="+", default=[0, 3, 7, 10, 14, 17],
                    help="rehab day of each session (the last one is 2 days ago)")
    args = ap.parse_args(argv)

    cfg = EX.exercise_config(load_config(), args.exercise)
    cfg["exercise"].update(sets=1, reps_per_set=12, max_attempts_per_set=30)   # one 12-rep set
    root = publish_root(load_config())
    if root is None:
        raise SystemExit("Publishing is off (publish.enabled in config.yaml).")
    plan = PLAN[-len(args.days):] if len(args.days) <= len(PLAN) else PLAN + [PLAN[-2]] * (len(args.days) - len(PLAN))
    end = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=2)
    start = end - timedelta(days=args.days[-1])
    title = EX.get(args.exercise).title
    with tempfile.TemporaryDirectory() as tmp:
        for i, (day, (kinds, fatigue_reps)) in enumerate(zip(args.days, plan)):
            trace = []
            original = H._attach

            def attach(pipe, res):
                original(pipe, res)
                pipe.on_frame = lambda f, st: trace.append((f.t, f.theta, f.env_pct)) if pipe.session.set_no else None

            H._attach = attach
            try:
                res = H.run_simulated_session(cfg, kinds, seed=100 + i, log_root=tmp, user=args.patient,
                                              fatigue_reps=fatigue_reps, **PATIENT.get(args.exercise, {}))
            finally:
                H._attach = original
            pipe = res.pipeline
            pipe.session.finish()
            pipe.close()
            stamp = (start + timedelta(days=day, minutes=7 * i)).strftime("%Y%m%d_%H%M%S")
            sdir = res.session_dir.parent / stamp
            shutil.move(str(res.session_dir), sdir)
            summary = pipe.session.summary()
            report = write_report(sdir / "report.html", summary, list(pipe.session.reps), cfg, args.patient,
                                  title, pipe.emg_mode, pipe.profile, trace, sdir)
            dest = publish_session(root, args.patient, report, sdir, title, args.exercise, summary)
            print(f"day {day:3d}: {summary['valid']:2d}/{summary['attempts']:2d} good -> {dest}")


if __name__ == "__main__":
    main()
