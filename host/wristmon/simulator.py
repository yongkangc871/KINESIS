"""Virtual patient: synthesises JY901S + sEMG streams for demos, tuning and tests.

It follows the calibration instructions (`set_behaviour`) and performs queued rep kinds,
including every fault, with progressive fatigue (EMG spectrum compresses, amplitude rises,
lifts slow down). Output goes through the real binary protocol, so the parser is exercised too.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from scipy import signal

from .conditioning import gravity_in_sensor

EMG_FS = 1000
IMU_FS = 200
BLOCK = 10      # EMG samples per packet (10 ms)

# Rep kinds the patient can perform, as overrides of the nominal good rep.
REP_KINDS: dict[str, dict] = {
    "good": {},
    "incomplete": {"peak": 30.0},
    "early_down": {"lift_s": 1.5, "hold_s": 0.0},
    "passive": {"activation": "passive"},
    "late_onset": {"activation": "late"},
    "forearm_lift": {"forearm": True},
    "drop_fast": {"lower_s": 0.6},
    "swing_fast": {"lift_s": 0.45},
    "twist": {"roll": 30.0},
    "side_bend": {"yaw": 22.0},
    "over_flex": {"peak": 78.0},
    "not_returned": {"bottom": 25.0, "pause_s": 0.0},
    "jerky": {"tremor": 2.0},
    "signal_poor": {"emg_jumps": "brief"},
    "timeout": {"hold_s": 16.0},
    "electrode_off": {"emg_jumps": "sustained"},   # long contact loss -> session pauses
}

# Which fault each kind is expected to produce first (used by tests and the acceptance demo).
EXPECTED_FAULT = {
    "incomplete": "INCOMPLETE_ROM", "early_down": "EARLY_DOWN", "passive": "NO_MUSCLE",
    "late_onset": "NO_MUSCLE", "forearm_lift": "FOREARM_LIFT", "drop_fast": "DROP_FAST",
    "swing_fast": "SWING_FAST", "twist": "WRIST_TWIST", "side_bend": "SIDE_BEND",
    "over_flex": "OVER_FLEX", "not_returned": "NOT_RETURNED", "jerky": "JERKY",
    "signal_poor": "SIGNAL_POOR", "timeout": "TIMEOUT",
}

BEHAVIOURS = ("rest", "hold_start", "hold_top", "hold_half", "idle", "reps")


def min_jerk(tau):
    tau = np.clip(tau, 0.0, 1.0)
    return tau ** 3 * (10 - 15 * tau + 6 * tau ** 2)


@dataclass
class Segment:
    t0: float
    t1: float
    th0: float
    th1: float
    phase: str          # lift | hold | lower | pause | move
    rep: "RepSpan | None" = None


@dataclass
class RepSpan:
    kind: str
    t0: float
    t1: float
    params: dict = field(default_factory=dict)
    lift_end: float = 0.0
    lower_start: float = 0.0


class VirtualPatient:
    def __init__(self, seed: int = 0, emg_mode: str = "raw", target_deg: float = 45.0,
                 rom_deg: float = 52.0, fatigue_reps: int = 40, forearm_imu: bool = False,
                 tempo: dict | None = None):
        self.rng = np.random.default_rng(seed)
        self.emg_mode = emg_mode
        self.target = target_deg
        self.rom = rom_deg
        self.tempo = tempo                # exercise tempo for good reps; None = the wrist-curl default
        # Muscle-driven exercise: the planned "angle" becomes muscle effort (rom = strongest
        # squeeze) and the sensor itself stays still.
        self.isometric = False
        self.fatigue_reps = fatigue_reps
        self.forearm_imu = forearm_imu
        self.t = 1.0                      # device clock, s
        self.segments: deque[Segment] = deque()
        self.behaviour = "idle"
        self.queue: deque[str] = deque()
        self.auto_kind: str | None = None  # keep doing this kind when the queue is empty
        self.reps_done = 0
        self.performed: list[str] = []
        self.pitch0, self.roll0, self.yaw0 = 3.0, 177.0, 40.0
        self.yaw_drift_dps = 0.003
        self._shaper_cache = {}
        self._shaper = None
        self._shaper_key = None
        self._zi = None
        self._last_theta = 0.0
        self._env_lp = 0.0
        self._append_hold(0.0, 0.5)

    # ------------------------------------------------------------ control
    def set_behaviour(self, name: str):
        if name not in BEHAVIOURS:
            raise ValueError(name)
        if name == self.behaviour:
            return
        self.behaviour = name
        # Drop anything planned and move smoothly to the new pose.
        th = self.theta_at(self.t)
        self.segments = deque(s for s in self.segments if s.t0 <= self.t)
        if self.segments:
            cur = self.segments[-1]
            if cur.rep is None:
                cur.t1 = self.t
                cur.th1 = th
        self.segments.append(Segment(self.t, self.t + 1.5, th, self._pose(name), "move"))
        if name == "reps":
            self._append_hold(0.0, 1.0)

    def queue_reps(self, kinds):
        for k in kinds:
            if k not in REP_KINDS:
                raise ValueError(k)
        if self.behaviour == "reps" and not self.queue and not self.auto_kind and self.segments:
            # Already waiting in the start pose: settle a moment first, like a person who
            # has just been told to begin (the rep counter also needs a still start).
            self._append_hold(self.segments[-1].th1, 1.2)
        self.queue.extend(kinds)

    def _pose(self, name: str) -> float:
        return {"rest": 0.0, "hold_start": 0.0, "idle": 0.0, "reps": 0.0,
                "hold_top": self.rom, "hold_half": self.target / 2}[name]

    @property
    def holding_weight(self) -> bool:
        return self.behaviour != "rest"

    @property
    def fatigue(self) -> float:
        return min(1.0, self.reps_done / max(1, self.fatigue_reps))

    # ------------------------------------------------------------ schedule
    def _append_hold(self, th, dur, phase="pause"):
        t0 = self.segments[-1].t1 if self.segments else self.t
        self.segments.append(Segment(t0, t0 + dur, th, th, phase))

    def _extend(self):
        last = self.segments[-1]
        if self.behaviour == "reps" and (self.queue or self.auto_kind):
            kind = self.queue.popleft() if self.queue else self.auto_kind
            self._append_rep(kind, last.th1)
        else:
            self._append_hold(last.th1, 0.5, "hold" if self.behaviour == "hold_top" else "pause")

    def _append_rep(self, kind: str, th_start: float):
        p = {"lift_s": 2.0, "hold_s": 1.0, "lower_s": 3.2, "pause_s": 1.2,
             "peak": self.target + 3.0, "bottom": 0.0}
        if self.tempo:
            p.update({k: self.tempo[k] for k in ("lift_s", "hold_s", "lower_s", "pause_s") if k in self.tempo})
        p.update(REP_KINDS[kind])
        jitter = lambda: 1.0 + self.rng.uniform(-0.05, 0.05)
        slow = 1.0 + 0.2 * self.fatigue
        lift = p["lift_s"] * jitter() * slow
        lower = p["lower_s"] * jitter()
        peak = p["peak"] + self.rng.uniform(-1.0, 1.0)
        t0 = self.segments[-1].t1
        span = RepSpan(kind, t0, 0.0, p)
        segs = [Segment(t0, t0 + lift, th_start, peak, "lift", span)]
        t = t0 + lift
        span.lift_end = t
        if p["hold_s"] > 0:
            segs.append(Segment(t, t + p["hold_s"], peak, peak, "hold", span))
            t += p["hold_s"]
        span.lower_start = t
        segs.append(Segment(t, t + lower, peak, p["bottom"], "lower", span))
        t += lower
        span.t1 = t
        if p["pause_s"] > 0:
            segs.append(Segment(t, t + p["pause_s"], p["bottom"], p["bottom"], "pause"))
        self.segments.extend(segs)
        self.performed.append(kind)
        if kind not in ("passive", "timeout"):
            self.reps_done += 1

    def _segment(self, t: float) -> Segment:
        while self.segments[-1].t1 <= t:
            self._extend()
        while len(self.segments) > 1 and self.segments[0].t1 < t - 5.0:
            self.segments.popleft()
        for s in self.segments:
            if s.t0 <= t < s.t1:
                return s
        return self.segments[-1]

    # ------------------------------------------------------------ kinematics
    def theta_at(self, t: float) -> float:
        s = self._segment(t)
        dur = s.t1 - s.t0
        th = s.th0 + (s.th1 - s.th0) * float(min_jerk((t - s.t0) / dur)) if dur > 0 else s.th1
        r = s.rep
        if r is not None:
            if r.params.get("tremor") and s.phase in ("lift", "lower"):
                th += r.params["tremor"] * math.sin(2 * math.pi * 3.0 * (t - s.t0))
            if r.params.get("forearm"):
                th += 5.0 * self._bump(t, r.t0 + 0.5, 0.8)
        return th

    @staticmethod
    def _bump(t, t0, dur):
        if t < t0 or t > t0 + dur:
            return 0.0
        return math.sin(math.pi * (t - t0) / dur) ** 2

    def _rep_ramp(self, r: RepSpan, t: float) -> float:
        """0 -> 1 during the lift, 1 during the hold, 1 -> 0 during the lower."""
        if t < r.t0 or t > r.t1:
            return 0.0
        if t < r.lift_end:
            return float(min_jerk((t - r.t0) / (r.lift_end - r.t0)))
        if t < r.lower_start:
            return 1.0
        return 1.0 - float(min_jerk((t - r.lower_start) / (r.t1 - r.lower_start)))

    def _offsets(self, t: float):
        """(roll_off, yaw_off, forearm_lift_deg, acc_z_extra, gx_extra) at t."""
        s = self._segment(t)
        r = s.rep
        roll = yaw = fa = accz = gx = 0.0
        if r is not None:
            ramp = self._rep_ramp(r, t)
            roll = r.params.get("roll", 0.0) * ramp
            yaw = r.params.get("yaw", 0.0) * ramp
            if r.params.get("forearm"):
                b = self._bump(t, r.t0 + 0.5, 0.8)
                fa = 12.0 * b
                accz = 0.35 * math.sin(2 * math.pi * (t - r.t0 - 0.5) / 0.8) if b > 0 else 0.0
                gx = 75.0 * b
        return roll, yaw, fa, accz, gx

    # ------------------------------------------------------------ EMG
    def _activation(self, t: float, th: float) -> float:
        """Flexor activation in RVC units (1.0 = holding the weight at half the target)."""
        if not self.holding_weight:
            return 0.0
        s = self._segment(t)
        if self.isometric:
            if s.rep is not None and s.rep.params.get("activation") == "passive":
                return 0.04
            return 0.03 + max(0.0, th) / max(self.rom, 1.0)
        a = math.cos(math.radians(th)) / math.cos(math.radians(self.target / 2))
        if s.phase == "lift":
            a += 0.25
        elif s.phase == "lower":
            a *= 0.75
        r = s.rep
        if r is not None:
            mode = r.params.get("activation")
            if mode == "passive":
                return 0.04
            if mode == "late" and t < r.t0 + 0.5:
                return 0.0
        elif s.phase == "pause" and self.performed and self.queue and REP_KINDS[self.queue[0]].get("activation") == "late":
            return 0.0   # hand supported before a late-onset rep
        return max(0.0, a) * (1.0 + 0.5 * self.fatigue)

    def _shaped_noise(self, n: int) -> np.ndarray:
        scale = round(1.0 - 0.3 * self.fatigue, 2)
        if scale != self._shaper_key:
            if scale not in self._shaper_cache:
                sos = signal.butter(2, [40 * scale, 180 * scale], btype="bandpass", fs=EMG_FS, output="sos")
                gain = np.std(signal.sosfilt(sos, np.random.default_rng(1).standard_normal(20000))[2000:])
                self._shaper_cache[scale] = (sos, gain)
            self._shaper, gain = self._shaper_cache[scale]
            self._gain = gain
            self._shaper_key = scale
            self._zi = np.zeros((self._shaper.shape[0], 2))
        y, self._zi = signal.sosfilt(self._shaper, self.rng.standard_normal(n), zi=self._zi)
        return y / self._gain

    def _emg_block(self, times: np.ndarray) -> np.ndarray:
        acts = np.array([self._activation(t, self.theta_at(t)) for t in times])
        if self.emg_mode == "raw":
            x = 2048 + acts * 150.0 * self._shaped_noise(len(times))
            x += 5.0 * self.rng.standard_normal(len(times))
            x += 3.0 * np.sin(2 * math.pi * 50.0 * times)
        else:
            out = np.empty(len(times))
            for i, a in enumerate(acts):
                self._env_lp += 0.05 * (a - self._env_lp)
                out[i] = self._env_lp
            x = 150.0 + out * 900.0 * (1 + 0.03 * self.rng.standard_normal(len(times)))
            x += 3.0 * self.rng.standard_normal(len(times))
        s = self._segment(times[0])
        jumps = s.rep.params.get("emg_jumps") if s.rep is not None else None
        if jumps:
            # Dry electrode briefly lifting off: 100 ms steps every 400 ms.
            el = times[0] - s.rep.t0
            window = 0.8 if jumps == "brief" else 1e9
            if el < window and int(el * 10) % 4 == 0:
                x += 1200.0
        return np.clip(np.round(x), 0, 4095)

    # ------------------------------------------------------------ IMU
    def _imu_sample(self, t: float):
        h = 1e-3
        if self.isometric:
            th = dth = 0.0
        else:
            th = self.theta_at(t)
            dth = (self.theta_at(t + h) - self.theta_at(t - h)) / (2 * h)
        roll_off, yaw_off, fa, accz, gx_extra = self._offsets(t)
        r2, y2, _, _, _ = self._offsets(t + h)
        roll = self.roll0 + roll_off
        pitch = self.pitch0 - th
        yaw = self.yaw0 + yaw_off + self.yaw_drift_dps * t
        n = self.rng.standard_normal(9)
        gx = (r2 - roll_off) / h + gx_extra + 0.4 * n[0]
        gy = dth + 0.4 * n[1]
        gz = -(y2 - yaw_off) / h + 0.4 * n[2]
        acc = gravity_in_sensor(roll, pitch) + 0.01 * n[3:6]
        acc[2] += accz
        hand = (roll + 0.05 * n[6], pitch + 0.05 * n[7], yaw + 0.05 * n[8], gx, gy, gz, *acc)
        fore = None
        if self.forearm_imu:
            fore = (178.0, 1.0 - fa, self.yaw0, gx_extra, 0.0, 0.0, *gravity_in_sensor(178.0, 1.0 - fa))
        return hand, fore

    # ------------------------------------------------------------ output
    def step(self):
        """Advances 10 ms. Returns (emg_t0, emg_samples, [(t, imu_id, values), ...])."""
        times = self.t + np.arange(BLOCK) / EMG_FS
        emg = self._emg_block(times)
        imu = []
        for k in range(BLOCK * IMU_FS // EMG_FS):
            ti = self.t + k / IMU_FS
            hand, fore = self._imu_sample(ti)
            imu.append((ti, 0, hand))
            if fore is not None:
                imu.append((ti, 1, fore))
        t0 = self.t
        self.t += BLOCK / EMG_FS
        return t0, emg, imu
