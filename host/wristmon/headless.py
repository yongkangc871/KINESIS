"""Headless drivers: run a scripted simulated session, or replay a recorded one.

Used by the CLI tools (simulate.py, replay.py) and by the tests. No GUI involved.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .calibration import FULL_STEPS, Profile, StepResult, emg_driven, steps_for
from .logger import SessionLogger
from .pipeline import Pipeline
from .sources import ReplayEvent, ReplaySource, SimSource

SETTLE_S = {"sensor_check": 0.5, "rest": 2.0, "zero": 2.0, "top": 0.0, "rvc": 2.0}


@dataclass
class RunResult:
    pipeline: Pipeline
    reps: list = field(default_factory=list)
    calibration: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    beeps: list = field(default_factory=list)
    session_dir: Path | None = None

    @property
    def counted(self):
        return [r for r in self.reps if not r.practice]

    @property
    def valid_count(self) -> int:
        return sum(r.valid for r in self.counted)


def _attach(pipe: Pipeline, res: RunResult):
    pipe.on_rep = res.reps.append
    pipe.on_event = lambda name, payload: res.events.append((name, payload))
    pipe.on_calibration = lambda step, r: res.calibration.__setitem__(step, r)
    pipe.feedback.listeners.append(lambda kind: res.beeps.append(kind))


def _pump(src, pipe: Pipeline, seconds: float, until=None):
    t_end = src.patient.t + seconds
    while src.patient.t < t_end:
        for pkt in src.read():
            pipe.handle(pkt)
        if until is not None and until():
            return True
    return False


def calibrate(src: SimSource, pipe: Pipeline, res: RunResult, steps=FULL_STEPS) -> bool:
    cal = pipe.cfg["calibration"]
    durations = {"sensor_check": cal["sensor_check_s"], "rest": cal["rest_s"], "zero": cal["zero_s"],
                 "top": cal["top_move_s"] + cal["top_hold_s"], "rvc": cal["rvc_s"]}
    for step in steps_for(pipe.cfg, steps):
        src.hint(step)
        if step == "practice":
            pipe.session_action("practice")
            src.queue_reps(["good"] * cal["practice_reps"])
            if not _pump(src, pipe, 60, lambda: "practice" in res.calibration):
                return False
            continue
        _pump(src, pipe, SETTLE_S.get(step, 1.0))
        pipe.start_capture(step, durations[step])
        _pump(src, pipe, durations[step] + 2, lambda s=step: s in res.calibration)
        r: StepResult = res.calibration.get(step)
        if r is None or not r.passed:
            return False
    return True


def run_simulated_session(cfg: dict, kinds: list[str], emg_mode: str = "raw", seed: int = 0,
                          log_root: str | Path | None = None, user: str = "sim",
                          profile: Profile | None = None, steps=FULL_STEPS, **patient_kw) -> RunResult:
    """Full flow: calibration wizard steps -> practice -> one exercise set doing `kinds`."""
    src = SimSource(seed=seed, emg_mode=emg_mode, realtime=False,
                    forearm_imu=cfg["imu"]["forearm_imu"], imu_fs=cfg["imu"]["rate_hz"], **patient_kw)
    src.patient.isometric = emg_driven(cfg)
    logger = SessionLogger(log_root, user, cfg) if log_root else None
    pipe = Pipeline(cfg, profile or Profile(user=user), logger=logger, send_command=src.write)
    pipe.link_stats = src.link_stats
    res = RunResult(pipe, session_dir=logger.dir if logger else None)
    _attach(pipe, res)
    src.open()
    if not calibrate(src, pipe, res, steps):
        failed = [s for s, r in res.calibration.items() if not r.passed]
        raise RuntimeError(f"simulated calibration failed: {failed or 'timeout'} "
                           f"{[(s, r.message, r.values) for s, r in res.calibration.items() if not r.passed]}")
    pipe.session_action("start_set")
    src.hint("exercise")
    src.queue_reps(kinds)
    # Run until the patient has started the last queued rep, then until it has been judged.
    while src.patient.queue and pipe.session.state in ("exercise", "paused"):
        _pump(src, pipe, 1.0)
    end = max((s.t1 for s in src.patient.segments), default=src.patient.t)
    _pump(src, pipe, end - src.patient.t + 2.0)
    pipe.close()
    return res


def replay_session(session_dir: str | Path, cfg: dict, recalibrate: bool = False) -> RunResult:
    """Re-runs a logged session through a fresh pipeline (no hardware, as fast as possible)."""
    d = Path(session_dir)
    cal = d / "calibration.json"
    profile = Profile.load(cal) if cal.exists() and not recalibrate else Profile(user="replay")
    pipe = Pipeline(cfg, profile)
    res = RunResult(pipe, session_dir=d)
    _attach(pipe, res)
    src = ReplaySource(d)
    src.open()
    while not src.finished:
        for item in src.read():
            if isinstance(item, ReplayEvent):
                apply_event(pipe, item, recalibrate)
            else:
                pipe.handle(item)
    return res


def apply_event(pipe: Pipeline, ev: ReplayEvent, recalibrate: bool = False):
    if ev.event == "session":
        action, _, reason = ev.arg.partition(":")
        if action == "pause":
            pipe.pause(reason)
        else:
            pipe.session_action(action)
    elif ev.event == "capture" and recalibrate:
        step, _, dur = ev.arg.partition(":")
        pipe.start_capture(step, float(dur))
    elif ev.event == "load_kg":
        try:
            pipe.profile.load_kg = float(ev.arg)
        except ValueError:
            pass


def rep_table(reps) -> str:
    lines = [f"{'#':>3} {'set':>3} {'ok':>3} {'peak':>6} {'lift':>5} {'hold':>5} {'lower':>5} "
             f"{'%RVC':>6} {'lag':>6} {'sparc':>6} {'fatig':>6}  faults"]
    for r in reps:
        def f(v, w=5, p=1):
            return f"{v:{w}.{p}f}" if isinstance(v, float) and math.isfinite(v) else " " * (w - 1) + "-"
        lines.append(f"{r.index:>3} {r.set_no:>3} {'Y' if r.valid else 'n':>3} {f(r.peak_theta, 6)} "
                     f"{f(r.lift_s, 5, 2)} {f(r.hold_s, 5, 2)} {f(r.lower_s, 5, 2)} {f(r.mean_pct, 6, 0)} "
                     f"{f(r.onset_lag_s, 6, 3)} {f(r.sparc, 6, 2)} {f(r.fatigue_score, 6, 0)}  "
                     f"{','.join(r.faults)}")
    return "\n".join(lines)
