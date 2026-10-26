"""Pack and send a patient's recorded sessions to the specialist dashboard.

    python publish_sessions.py yk              # every session in sessions/yk/
    python publish_sessions.py yk --dry-run    # show what would be sent

The app already sends each session when its summary appears; this sends the ones recorded
before that existed (or re-sends after a problem). For each session folder:

  1. skip it when nothing was recorded (no sensor samples);
  2. reuse its report.html/report.json, or build them by replaying the raw recording through
     the pipeline with that session's own config.yaml (so the reps are judged exactly as the
     patient saw them); an existing report is never overwritten;
  3. publish it with the same publish_session() the app calls after a summary:
     <publish.dir>/<patient>/se<session log>/  (manifest + report + raw recording).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from wristmon import exercises as EX
from wristmon import headless as H
from wristmon.calibration import safe_name
from wristmon.config import load_config, resolve_dir
from wristmon.publish import publish_root, publish_session
from wristmon.report import write_report


def _rows(path: Path) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


def _summary_from_report(report: dict) -> dict:
    t = report.get("totals", {})
    return {"sets": t.get("sets_done"), "valid": t.get("good_reps"), "invalid": t.get("not_counted"),
            "attempts": t.get("attempts")}


def build_report(session_dir: Path, patient: str) -> tuple[Path, dict, str, str]:
    """Replays the recording and writes report.html/.json into the session folder."""
    cfg = load_config(session_dir / "config.yaml")
    key = cfg["exercise"].get("key") or EX.DEFAULT
    title = EX.get(key).title
    trace = []
    original = H._attach

    def attach(pipe, res):
        original(pipe, res)
        pipe.on_frame = lambda f, st: trace.append((f.t, f.theta, f.env_pct)) if pipe.session.set_no else None

    H._attach = attach
    try:
        res = H.replay_session(session_dir, cfg)
    finally:
        H._attach = original
    pipe = res.pipeline
    summary = pipe.session.summary()
    report = write_report(session_dir / "report.html", summary, list(pipe.session.reps), cfg, patient, title,
                          pipe.emg_mode, pipe.profile, trace, session_dir)
    return report, summary, title, key


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patient")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    root = publish_root(cfg)
    if root is None:
        raise SystemExit("Publishing is off: set publish.enabled and publish.dir in config.yaml.")
    sessions = resolve_dir(cfg, "dir") / safe_name(args.patient)
    if not sessions.is_dir():
        raise SystemExit(f"No sessions for {args.patient!r} in {sessions.parent}")
    print(f"Sending {args.patient}'s sessions\n  from {sessions}\n  to   {root / safe_name(args.patient)}\n")
    sent = skipped = failed = 0
    for d in sorted(p for p in sessions.iterdir() if p.is_dir()):
        samples = _rows(d / "emg.csv") + _rows(d / "imu.csv")
        if samples == 0:
            print(f"  {d.name}  skip: nothing was recorded")
            skipped += 1
            continue
        if args.dry_run:
            print(f"  {d.name}  would send ({'existing report' if (d / 'report.json').exists() else 'build report'})")
            continue
        try:
            if (d / "report.json").exists() and (d / "report.html").exists():
                report = d / "report.html"
                data = json.loads(report.with_suffix(".json").read_text(encoding="utf-8"))
                summary = _summary_from_report(data)
                title = data.get("session", {}).get("exercise") or "Unknown exercise"
                key = load_config(d / "config.yaml")["exercise"].get("key") or EX.DEFAULT
                how = "existing report"
            else:
                report, summary, title, key = build_report(d, args.patient)
                how = "report built from the recording"
            dest = publish_session(root, args.patient, report, d, title, key, summary)
        except Exception as e:    # one broken recording must not stop the rest
            print(f"  {d.name}  FAILED: {type(e).__name__}: {e}")
            failed += 1
            continue
        sent += 1
        print(f"  {d.name}  sent  {summary.get('valid') or 0:>2}/{summary.get('attempts') or 0:<2} good reps  "
              f"{title:<14} ({how}) -> {dest.name}")
    print(f"\n{sent} sent, {skipped} skipped (empty), {failed} failed.")


if __name__ == "__main__":
    main()
