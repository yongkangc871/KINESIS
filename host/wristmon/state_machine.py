"""Rep state machine and fault detector.

IDLE -> READY (at start, still) -> LIFTING -> TOP_HOLD -> LOWERING -> BOTTOM (settling)
-> evaluate -> READY. A rep is judged once the limb has settled back at the start, so the
count, glow and beep come at the real end of the rep, not as it passes the bottom line.

Angles relative to the start position (theta - start_angle) drive the start/bottom
thresholds; the target and safety limits use absolute flexion theta. All angle thresholds
have hysteresis (lift_start_deg > bottom_deg, top exit below target - tol - hyst).
Each finished attempt is judged from its buffered frames by `evaluate_rep`.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum

import numpy as np
from scipy.ndimage import uniform_filter1d

from . import faults as F
from .conditioning import wrap180
from .datatypes import Frame, RepRecord
from .features import sparc

NAN = float("nan")
HISTORY_S = 3.0


class State(str, Enum):
    IDLE = "IDLE"
    READY = "READY"
    LIFTING = "LIFTING"
    TOP_HOLD = "TOP_HOLD"
    LOWERING = "LOWERING"
    BOTTOM = "BOTTOM"


ACTIVE_STATES = (State.LIFTING, State.TOP_HOLD, State.LOWERING)


@dataclass
class RepContext:
    t_onset: float
    yaw_ref: float = 0.0
    not_returned: bool = False
    timeout: bool = False


@dataclass
class RepLimits:
    target_deg: float = 45.0
    rom_deg: float = NAN
    start_angle: float = 0.0
    check_muscle: bool = True        # needs a calibrated RVC
    forearm_imu: bool = False


def _longest_run_idx(mask: np.ndarray):
    """(start, end) indices of the longest run of True, or (None, None)."""
    best = (None, None)
    best_len = 0
    start = None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        if start is not None and (not m or i == len(mask) - 1):
            end = i if m else i - 1
            if end - start + 1 > best_len:
                best, best_len = (start, end), end - start + 1
            start = None
    return best


def _longest_run_s(mask: np.ndarray, t: np.ndarray) -> float:
    if not mask.any():
        return 0.0
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 0.0
    best = 0.0
    start = None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        if start is not None and (not m or i == len(mask) - 1):
            end = i if m else i - 1
            best = max(best, t[end] - t[start] + dt)
            start = None
    return best


def evaluate_rep(frames: list[Frame], ctx: RepContext, lim: RepLimits, sm: dict, fc: dict,
                 window_deg=(10.0, 35.0)) -> RepRecord:
    """Judges one attempt. Pure: depends only on its arguments."""
    t = np.array([f.t for f in frames])
    th = np.array([f.theta for f in frames])
    om = np.array([f.omega for f in frames])
    n = len(frames)
    target = lim.target_deg
    i_pk = int(np.argmax(th))
    peak = float(th[i_pk])
    reach_thr = target - sm["target_tol_deg"]
    reached = peak >= reach_thr

    # Top hold = longest run near the peak where the wrist is actually still. Velocity comes
    # from theta smoothed over hold_smooth_s, so tremor does not break up a real pause while a
    # slow stop-and-go reversal (which is only still for an instant) does not count as one.
    fs = 1.0 / float(np.median(np.diff(t))) if n > 1 else 200.0
    k = max(1, int(round(sm["hold_smooth_s"] * fs)))
    th_s = uniform_filter1d(th, k, mode="nearest") if n > 1 else th
    w_s = np.gradient(th_s, t) if n > 1 else np.zeros(n)
    still = (th >= peak - sm["hold_band_deg"]) & (np.abs(w_s) < sm["hold_omega_dps"])
    if sm.get("hold_rule") == "above_target":
        # Muscle-driven exercises: the hold is the time the effort stays at the target (the
        # envelope ripples too much for "still near the peak").
        still = th >= reach_thr - sm["top_hyst_deg"]
    s, e = _longest_run_idx(still)
    if s is None:
        s = e = i_pk
    hold_s = float(t[e] - t[s]) if still.any() else 0.0
    t_reach = float(t[int(np.argmax(th >= reach_thr))]) if reached else float(t[i_pk])
    lift_s = t_reach - ctx.t_onset
    lower_s = float(t[-1] - t[e])
    lift_m = t <= t[s]
    lower_m = t >= t[e]
    peak_w_lift = float(np.max(om[lift_m])) if lift_m.any() else 0.0
    peak_w_lower = float(max(0.0, np.max(-om[lower_m]))) if lower_m.any() else 0.0

    # Muscle: mean activation over lifting + holding, and onset lag vs movement onset.
    act_m = (t >= ctx.t_onset) & (t <= t[e])
    pct = np.array([f.env_pct for f in frames])[act_m]
    mean_pct = float(np.nanmean(pct)) if pct.size else NAN
    peak_pct = float(np.nanmax(pct)) if pct.size else NAN
    lag = NAN
    for f in frames:
        if f.t < ctx.t_onset:
            continue
        if f.t > t[e]:
            break
        if f.emg_active:
            # Clamped at -1 s: "already active well before the movement".
            lag = max(-1.0, f.emg_onset_t - ctx.t_onset)
            break

    roll = np.array([f.roll_dev for f in frames])
    yaw = np.array([f.yaw_dev for f in frames])
    if fc.get("yaw_reference", "rep_start") == "rep_start":
        yaw = wrap180(yaw - ctx.yaw_ref)
    max_roll = float(np.max(np.abs(roll)))
    max_yaw = float(np.max(np.abs(yaw)))

    sp_lift = sparc(om[lift_m], fs) if lift_m.sum() > 8 else NAN
    sp_lower = sparc(-om[lower_m], fs) if lower_m.sum() > 8 else NAN
    sp = float(np.nanmin([sp_lift, sp_lower])) if not (math.isnan(sp_lift) and math.isnan(sp_lower)) else NAN

    codes = []
    if any((not f.signal_ok) or (not f.imu_ok) for f in frames):
        codes.append(F.SIGNAL_POOR)
    if not reached:
        codes.append(F.INCOMPLETE_ROM)
    if (reached and hold_s < fc["min_hold_s"]) or not reached:
        codes.append(F.EARLY_DOWN)
    if lim.check_muscle:
        late = math.isnan(lag) or lag > fc["max_onset_lag_s"]
        weak = math.isnan(mean_pct) or mean_pct < fc["min_activation_pct"]
        if late or weak:
            codes.append(F.NO_MUSCLE)
    offplane = np.array([f.offplane for f in frames])
    acc = np.array([f.acc_resid for f in frames])
    lifted = (_longest_run_s(offplane > fc["forearm_offplane_dps"], t) >= fc["forearm_min_s"]
              or _longest_run_s(acc > fc["forearm_acc_g"], t) >= fc["forearm_min_s"])
    if lim.forearm_imu:
        fa = np.array([f.forearm_dev for f in frames])
        if np.any(np.abs(fa[np.isfinite(fa)]) > fc["forearm_imu_deg"]):
            lifted = True
    if lifted:
        codes.append(F.FOREARM_LIFT)
    if not ctx.timeout and ((not ctx.not_returned and lower_s < fc["min_lower_s"])
                            or peak_w_lower > fc["max_lower_omega_dps"]):
        codes.append(F.DROP_FAST)
    if (reached and lift_s < fc["min_lift_s"]) or peak_w_lift > fc["max_lift_omega_dps"]:
        codes.append(F.SWING_FAST)
    if max_roll > fc["roll_max_deg"]:
        codes.append(F.WRIST_TWIST)
    if max_yaw > fc["yaw_max_deg"]:
        codes.append(F.SIDE_BEND)
    safe_max = target + fc["over_flex_margin_deg"]
    if math.isfinite(lim.rom_deg):
        safe_max = min(safe_max, lim.rom_deg + fc["rom_margin_deg"])
    safe_max = max(safe_max, target + sm["target_tol_deg"] + 3.0)
    if peak > safe_max:
        codes.append(F.OVER_FLEX)
    if ctx.not_returned:
        codes.append(F.NOT_RETURNED)
    if not math.isnan(sp) and sp < fc["sparc_min"]:
        codes.append(F.JERKY)
    if ctx.timeout:
        codes.append(F.TIMEOUT)

    # Fixed angle window on the way up, for fair rep-to-rep fatigue comparison.
    window = (NAN, NAN)
    up = th[: s + 1]
    lo, hi = window_deg
    if np.any(up >= hi):
        ia = int(np.argmax(up >= lo))
        ib = int(np.argmax(up >= hi))
        if ib > ia:
            window = (float(t[ia]), float(t[ib]))

    prim = F.primary_fault(codes, fc["priority"])
    return RepRecord(
        t_start=ctx.t_onset, t_end=float(t[-1]), valid=not codes, faults=codes, primary_fault=prim,
        message=F.message_for(prim, fc.get("messages")) if prim else "Good",
        peak_theta=peak, target_deg=target, lift_s=lift_s, hold_s=hold_s, lower_s=lower_s,
        peak_omega_lift=peak_w_lift, peak_omega_lower=peak_w_lower, onset_lag_s=lag,
        mean_pct=mean_pct, peak_pct=peak_pct, max_roll_dev=max_roll, max_yaw_dev=max_yaw,
        sparc=sp, window=window)


class RepStateMachine:
    def __init__(self, sm_cfg: dict, fault_cfg: dict, fatigue_cfg: dict, limits: RepLimits | None = None):
        self.sm = sm_cfg
        self.fc = fault_cfg
        self.window_deg = tuple(fatigue_cfg["window_deg"])
        self.lim = limits or RepLimits()
        self.reset()

    def configure(self, limits: RepLimits):
        self.lim = limits

    def reset(self):
        self.state = State.IDLE
        self.hist: deque[Frame] = deque()
        self.rep: list[Frame] = []
        self.ctx: RepContext | None = None
        self.peak = -math.inf
        self.trough = math.inf
        self.trough_t = NAN
        self.still_since = None
        self.bottom_since = None
        self.bottom_entered = NAN
        self.pending = False          # rep back at the bottom, judged once the limb has settled

    BOTTOM_MAX_S = 1.5                # judge anyway if the limb rests just above the bottom line

    def update(self, f: Frame) -> RepRecord | None:
        sm = self.sm
        self.hist.append(f)
        while self.hist and self.hist[0].t < f.t - HISTORY_S:
            self.hist.popleft()
        rel = f.theta - self.lim.start_angle
        target = self.lim.target_deg
        st = self.state
        rec = None

        if st in ACTIVE_STATES:
            self.rep.append(f)
            self.peak = max(self.peak, f.theta)
            if f.t - self.ctx.t_onset > sm["max_rep_s"]:
                self.ctx.timeout = True
                rec = self._finish(self.rep)
                self._enter(State.IDLE)
                return rec

        if st == State.IDLE:
            if abs(rel) < sm["ready_deg"] and abs(f.omega) < sm["still_omega_dps"]:
                if self.still_since is None:
                    self.still_since = f.t
                if f.t - self.still_since >= sm["ready_still_s"]:
                    self._enter(State.READY)
            else:
                self.still_since = None
                # A lift begun from the start position before the still time was up (e.g.
                # right after the counter was reset for practice or a new set) still counts.
                if rel > sm["lift_start_deg"] and any(
                        abs(h.theta - self.lim.start_angle) < sm["ready_deg"]
                        for h in self.hist if h.t >= f.t - 1.0):
                    self._begin()
        elif st == State.READY:
            if rel > sm["lift_start_deg"]:
                self._begin()
        elif st == State.LIFTING:
            if f.theta >= target - sm["target_tol_deg"]:
                self._enter(State.TOP_HOLD)
            elif f.theta < self.peak - sm["reversal_deg"]:
                self._start_lowering(f)
        elif st == State.TOP_HOLD:
            if f.theta < target - sm["target_tol_deg"] - sm["top_hyst_deg"]:
                self._start_lowering(f)
        elif st == State.LOWERING:
            if f.theta < self.trough:
                self.trough, self.trough_t = f.theta, f.t
            if rel < sm["bottom_deg"]:
                # Back down, but the movement is not over until the limb has settled at the
                # start: the rep is judged (counted, beeped) then, at the true end of the rep.
                self.pending = True
                self._enter(State.BOTTOM)
                self.bottom_since = self.bottom_entered = f.t
            elif f.theta > self.trough + sm["reversal_deg"]:
                # Lifting again without getting back down: judge the old rep up to the trough.
                self.ctx.not_returned = True
                rec = self._finish([x for x in self.rep if x.t <= self.trough_t])
                self._begin(onset=self.trough_t)
        elif st == State.BOTTOM:
            if self.pending:
                self.rep.append(f)
            if rel > sm["lift_start_deg"]:
                rec = self._finish_pending()          # the next lift began before settling
                self._begin()
            elif rel < sm["bottom_deg"]:
                if f.t - self.bottom_since >= sm["bottom_settle_s"]:
                    rec = self._finish_pending()
                    self._enter(State.READY)
            else:
                self.bottom_since = f.t
                if self.pending and f.t - self.bottom_entered >= self.BOTTOM_MAX_S:
                    rec = self._finish_pending()
        return rec

    def _finish_pending(self) -> RepRecord | None:
        if not self.pending:
            return None
        self.pending = False
        return self._finish(self.rep)

    # ------------------------------------------------------------ helpers

    def _enter(self, s: State):
        self.state = s
        if s == State.IDLE:
            self.still_since = None

    def _start_lowering(self, f: Frame):
        self.trough, self.trough_t = f.theta, f.t
        self._enter(State.LOWERING)

    def _find_onset(self) -> float:
        """Movement onset = start of the current run of omega above omega_onset_dps."""
        thr = self.sm["omega_onset_dps"]
        hist = list(self.hist)
        t_on = hist[-1].t
        for h in reversed(hist):
            if h.omega <= thr:
                break
            t_on = h.t
        return t_on

    def _begin(self, onset: float | None = None):
        if onset is None:
            onset = self._find_onset()
        frames = [h for h in self.hist if h.t >= onset] or [self.hist[-1]]
        self.rep = frames
        self.peak = max(x.theta for x in frames)
        self.ctx = RepContext(t_onset=frames[0].t, yaw_ref=frames[0].yaw_dev)
        self._enter(State.LIFTING)

    def _finish(self, frames: list[Frame]) -> RepRecord | None:
        if not frames:
            return None
        peak = max(x.theta for x in frames)
        if not self.ctx.timeout and peak - self.lim.start_angle < self.sm["min_attempt_deg"]:
            return None   # a twitch, not an attempt
        return evaluate_rep(frames, self.ctx, self.lim, self.sm, self.fc, self.window_deg)
