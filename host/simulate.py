"""Generate a recorded session from the virtual patient (no hardware needed).

    python simulate.py --scenario acceptance --out sessions
    python simulate.py --kinds good good passive drop_fast --emg-mode envelope

The session folder can then be replayed with replay.py or opened in the GUI with --replay.
"""
from __future__ import annotations

import argparse
import sys

from wristmon.config import load_config, resolve_dir
from wristmon.headless import rep_table, run_simulated_session
from wristmon.simulator import REP_KINDS

SCENARIOS = {
    "good10": ["good"] * 10,
    "acceptance": ["good"] * 10 + ["passive"]
                  + [k for k in REP_KINDS if k not in ("good", "passive", "electrode_off")],
    "fatigue": ["good"] * 30,
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="acceptance")
    ap.add_argument("--kinds", nargs="+", choices=sorted(REP_KINDS), help="explicit rep list (overrides --scenario)")
    ap.add_argument("--emg-mode", choices=["raw", "envelope"], default="raw")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--config")
    ap.add_argument("--out", help="session root folder (default: logging.dir from config)")
    ap.add_argument("--user", default="sim")
    args = ap.parse_args(argv)

    kinds = args.kinds or SCENARIOS[args.scenario]
    # One long set so every scripted rep lands in the same set.
    cfg = load_config(args.config, {"exercise": {"reps_per_set": 1000, "max_attempts_per_set": 1000,
                                                 "max_reps_per_session": 1000}})
    out = args.out or resolve_dir(cfg, "dir")
    res = run_simulated_session(cfg, kinds, args.emg_mode, args.seed, out, args.user)
    print(rep_table(res.counted))
    print(f"\nvalid {res.valid_count} / attempts {len(res.counted)}; beeps: {res.beeps}")
    print(f"session saved to {res.session_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
