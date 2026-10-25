"""Processing pipeline: packets in -> frames, judged reps, calibration results and events out.

No Qt here. The live GUI runs this in a worker thread; replay and tests call it directly.
Everything is clocked on device time (`self.now`), so a replayed session reproduces the
same decisions. Control calls (session actions, calibration captures) are logged as
events with their device time so replay can re-apply them at the same point.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .calibration import CalibrationManager, Profile, StepResult, apply_result, emg_driven
from .conditioning import (GYRO_AXES, EmgConditioner, EmgQuality, ImuConditioner, TimeRing,
                           gravity_in_sensor, hinge_angle, wrap180)
from .config import HOST_DIR
from .datatypes import DeviceStatus, EmgBlock, Frame, ImuSample, RawFrame, RepRecord
from .fatigue import FatigueScorer, load_fatigue_model, zone_for
from .feedback import FeedbackController
from .features import rep_emg_features
from .protocol import EMG_HINTS
from .session import EXERCISE, PRACTICE, REST, SessionManager
from .state_machine import RepLimits, RepStateMachine, State

NAN = float("nan")
G = 9.81


@dataclass
class Capture:
    step: str
    t_start: float
    t_end: float
    frames: list = field(default_factory=list)
    adc: list = field(default_factory=list)
    emg0: int = 0
    imu0: int = 0
    link0: object = None


class Pipeline:
    def __init__(self, cfg: dict, profile: Profile | None = None, logger=None,
                 send_command: Callable[[str], None] | None = None, fatigue_model=None):
        self.cfg = cfg
        self.logger = logger
        self.profile = profile or Profile(user="guest")
        self.feedback = FeedbackController(cfg["feedback"], send_command)
        self.session = SessionManager(cfg["exercise"])
        self.calman = CalibrationManager(cfg)
        self.imu = ImuConditioner(cfg["imu"])
        self.sm = RepStateMachine(cfg["state_machine"], cfg["faults"], cfg["fatigue"])
        self._fatigue_model = fatigue_model
        self.hinge = GYRO_AXES[cfg["imu"]["hinge_axis"]]
        self.forearm_imu = bool(cfg["imu"]["forearm_imu"])

        # Outputs (set by the owner).
        self.on_frame: Callable[[Frame, str], None] | None = None
        self.on_rep: Callable[[RepRecord], None] | None = None
        self.on_event: Callable[[str, object], None] | None = None
        self.on_calibration: Callable[[str, StepResult], None] | None = None

        self.now = 0.0
        self.link_stats = None          # set by the source owner (live serial)
        self.device_hint = "unknown"
        self.battery_mv = 0
        self.counts = {"emg": 0, "imu": 0}
        self.capture: Capture | None = None
        self.last_zero_vals: dict | None = None
        self.fa_pitch = NAN
        self._emg_last_t = None
        self._lost_seen = 0
        self._poor_since = None
        self._omega_hist: deque = deque(maxlen=5)
        # Muscle-driven exercises: theta = smoothed %-of-strongest-squeeze, omega its rate.
        self.emg_driven = emg_driven(cfg)
        self._ed_t: float | None = None
        self._ed_lp = 0.0
        self._ed_w = 0.0

        mode = cfg["emg"]["mode"]
        if mode not in ("raw", "envelope"):
            mode = self.profile.emg_mode if math.isfinite(self.profile.rest_mean) else "raw"
        self.emg_mode = mode
        self._build_emg()
        self.apply_profile(self.profile)

    # ------------------------------------------------------------ setup

    def _build_emg(self):
        e = self.cfg["emg"]
        self.emgc = EmgConditioner(e, self.emg_mode)
        self.quality = EmgQuality(e["quality"], e["fs_hz"], self.emg_mode, e["adc_max"], e["notch_hz"] or 50)
        # Channels: filtered EMG, envelope, onset-active flag, onset time, quality ok.
        self.ring = TimeRing(30.0, e["fs_hz"], 5)
        self.sync_delay = e.get("sync_delay_s", 0.02)
        model = self._fatigue_model or load_fatigue_model(self.cfg["fatigue"], self.emg_mode, HOST_DIR)
        self.fatigue = FatigueScorer(self.cfg["fatigue"], self.emg_mode, model)

    def set_emg_mode(self, mode: str):
        if mode != self.emg_mode:
            self.emg_mode = mode
            self._build_emg()
            self.apply_profile(self.profile)
            self._emit("emg_mode", mode)

    @property
    def _grav0(self) -> np.ndarray:
        """Gravity in the sensor frame at the zero pose (the zero step updates the profile in place)."""
        key = (self.profile.roll0, self.profile.pitch0)
        if getattr(self, "_grav0_key", None) != key:
            self._grav0_key, self._grav0_val = key, gravity_in_sensor(*key)
        return self._grav0_val

    def apply_profile(self, p: Profile):
        self.profile = p
        if math.isfinite(p.rest_mean):
            self.emgc.set_rest(p.rest_mean, p.rest_sd)
            self.quality.set_rest_level(p.rest_mean)
        # An exercise can switch the muscle check off (the sensor is not on the working muscle,
        # or the movement should be relaxed, like pendulum swings).
        check_muscle = (math.isfinite(p.rvc) and self.cfg["faults"].get("check_muscle", True)
                        and not emg_driven(self.cfg))     # there the movement is the muscle signal
        self.sm.configure(RepLimits(target_deg=p.target_deg, rom_deg=p.rom_deg, start_angle=p.start_angle,
                                    check_muscle=check_muscle, forearm_imu=self.forearm_imu))

    # ------------------------------------------------------------ input

    def handle(self, pkt):
        if isinstance(pkt, EmgBlock):
            self._on_emg(pkt)
        elif isinstance(pkt, ImuSample):
            self._on_imu(pkt)
        elif isinstance(pkt, DeviceStatus):
            self.battery_mv = pkt.battery_mv
            self.device_hint = EMG_HINTS.get(pkt.emg_hint, "unknown")
        self.feedback.tick(self.now)

    def _on_emg(self, b: EmgBlock):
        t = b.times
        gap = self._emg_last_t is not None and (t[0] - self._emg_last_t) > 2.5 * b.dt
        self._emg_last_t = t[-1]
        filtered, env, active, onset = self.emgc.process(t, b.samples)
        self.quality.rest_context = (self.session.state == REST
                                     or (self.capture is not None and self.capture.step == "rest"))
        ok = self.quality.process(t[-1], b.samples, env, gap)
        self.ring.extend(t, np.column_stack([filtered, env, active, onset, np.full(len(t), float(ok))]))
        self.now = max(self.now, float(t[-1]))
        self.counts["emg"] += len(t)
        if self.logger:
            self.logger.log_emg(t, b.samples)
        if self.capture is not None and t[-1] >= self.capture.t_start:
            self.capture.adc.append(b.samples)

    def _on_imu(self, s: ImuSample):
        if self.logger:
            self.logger.log_imu(s)
        self.now = max(self.now, s.t)
        pitch_f, gap, grav_f = self.imu.process(s)
        if s.imu_id == 1:
            self.fa_pitch = pitch_f
            return
        self.counts["imu"] += 1
        if self.link_stats is not None and self.link_stats.lost != self._lost_seen:
            self._lost_seen = self.link_stats.lost
            gap = True
        # EMG is read a fixed delay behind the IMU sample so the value does not depend on
        # whether this IMU packet arrived before or after the EMG block covering it.
        v = self.ring.at(s.t - self.sync_delay)
        if v is not None:
            env, active, onset, emg_ok = float(v[1]), bool(v[2]), float(v[3]), bool(v[4])
        else:
            env, active, onset, emg_ok = NAN, False, NAN, False
        raw = RawFrame(t=s.t, pitch=pitch_f, pitch_raw=s.pitch, roll=s.roll, yaw=s.yaw,
                       gyro=(s.gx, s.gy, s.gz), acc=(s.ax, s.ay, s.az), env=env,
                       emg_ok=emg_ok, imu_ok=not gap,
                       fa_pitch=self.fa_pitch if self.forearm_imu else NAN, grav=grav_f)
        frame = self.make_frame(raw, active, onset)

        if self.capture is not None:
            self._capture_frame(raw)

        if self.session.counting:
            rec = self.sm.update(frame)
            if rec is not None:
                self._on_rep_done(rec)
            self._check_signal_safety(frame)
        if self.on_frame:
            self.on_frame(frame, self.sm.state.value)

    def make_frame(self, raw: RawFrame, active: bool, onset: float) -> Frame:
        p = self.profile
        g = raw.gyro
        fa_dev = NAN
        if p.hinge is not None:
            # Rotation about the calibrated joint axis: independent of how the sensor is worn.
            h = np.asarray(p.hinge, float)
            d = hinge_angle(raw.grav, self._grav0, h)
            w = np.asarray(g, float)
            omega = float(np.dot(w, h))
            offplane = float(np.linalg.norm(w - omega * h))
        else:
            d = wrap180(raw.pitch - p.pitch0)
            if self.forearm_imu and math.isfinite(raw.fa_pitch):
                # Assumes the forearm IMU is mounted with the same axis orientation as the hand IMU.
                fa_dev = wrap180(raw.fa_pitch - p.fa_pitch0)
                d -= fa_dev
            omega = p.omega_sign * g[self.hinge]
            offplane = math.hypot(*[g[i] for i in range(3) if i != self.hinge])
        theta = p.flex_sign * d + p.start_angle
        acc_resid = self._acc_residual(raw, omega)
        if math.isfinite(p.rvc) and p.rvc > p.emg_offset:
            env_pct = (raw.env - p.emg_offset) / (p.rvc - p.emg_offset) * 100.0
        else:
            env_pct = NAN
        if self.emg_driven:
            theta, omega = self._emg_theta(raw.t, env_pct)
        return Frame(t=raw.t, theta=theta, omega=omega, offplane=offplane,
                     roll_dev=wrap180(raw.roll - p.roll0), yaw_dev=wrap180(raw.yaw - p.yaw0),
                     acc_resid=acc_resid, env_pct=env_pct, emg_active=active,
                     emg_onset_t=onset, signal_ok=raw.emg_ok, imu_ok=raw.imu_ok,
                     forearm_dev=fa_dev)

    def _emg_theta(self, t: float, env_pct: float) -> tuple[float, float]:
        """Muscle activation as the rep signal: low-passed (tau 0.15 s) so envelope ripple does
        not look like movement, and its rate of change smoothed again (tau 0.1 s)."""
        x = env_pct if math.isfinite(env_pct) else 0.0
        dt = min(0.05, max(1e-4, t - self._ed_t)) if self._ed_t is not None else 0.005
        self._ed_t = t
        prev = self._ed_lp
        self._ed_lp += dt / (0.15 + dt) * (x - self._ed_lp)
        self._ed_w += dt / (0.1 + dt) * ((self._ed_lp - prev) / dt - self._ed_w)
        return self._ed_lp, self._ed_w

    def _acc_residual(self, raw: RawFrame, omega: float) -> float:
        """Acceleration (g) not explained by gravity and rotation about the wrist hinge."""
        a = np.asarray(raw.acc, float)
        self._omega_hist.append((raw.t, math.radians(omega)))
        signs = self.profile.gravity_signs
        if not signs:
            return abs(float(np.linalg.norm(a)) - 1.0)
        lin = float(np.linalg.norm(a - gravity_in_sensor(raw.roll, raw.pitch_raw, signs)))
        (t0, w0), (t1, w1) = self._omega_hist[0], self._omega_hist[-1]
        alpha = (w1 - w0) / (t1 - t0) if t1 > t0 else 0.0
        r = self.cfg["imu"]["hand_lever_m"]
        expected = r * math.hypot(alpha, w1 * w1) / G
        return max(0.0, lin - expected)

    # ------------------------------------------------------------ reps

    def _on_rep_done(self, rec: RepRecord):
        rec.target_deg = self.profile.target_deg
        events = self.session.record(rec)
        if "practice_rep" in events:
            self.feedback.on_rep(rec, self.now)
            self._emit_rep(rec)
            if len(self.session.practice_reps) >= self.cfg["calibration"]["practice_reps"]:
                res = self.calman.practice(self.session.practice_reps)
                apply_result(self.profile, res)
                self.session.state = "setup"
                self.sm.reset()
                self._calibration_done("practice", res)
            return
        if not events:
            return
        # Feedback first: it is the latency-critical path.
        self.feedback.on_rep(rec, self.now)
        rec.features = self._rep_features(rec)
        if rec.valid:
            score, zone, jasa, _ = self.fatigue.update(rec)
            rec.fatigue_score, rec.fatigue_zone, rec.jasa = score, zone, jasa
        else:
            rec.fatigue_score = self.fatigue.ewma
            rec.fatigue_zone = zone_for(self.fatigue.ewma, self.cfg["fatigue"])
        if self.logger:
            self.logger.log_rep(rec)
        self._emit_rep(rec)
        if rec.valid and rec.fatigue_zone == "red":
            self.feedback.on_fatigue_red(self.now)
            events += self.session.force_rest()
        for ev in events:
            if ev not in ("counted", "rejected"):
                self._log_event("session_auto", ev)
                self._emit(ev, self.session.summary() if ev == "session_complete" else None)
        if not self.session.counting:
            self.sm.reset()

    def _rep_features(self, rec: RepRecord) -> dict:
        t0, t1 = rec.window
        if not (math.isfinite(t0) and math.isfinite(t1)):
            return {}
        _, v = self.ring.slice(t0, t1)
        if len(v) == 0:
            return {}
        seg = v[:, 0] if self.emg_mode == "raw" else v[:, 1]
        return rep_emg_features(seg, self.cfg["emg"]["fs_hz"], self.profile.rvc, self.emg_mode,
                                self.cfg["fatigue"], self.profile.emg_offset)

    def _emit_rep(self, rec):
        if self.on_rep:
            self.on_rep(rec)

    def _check_signal_safety(self, f: Frame):
        """Pause on sustained SIGNAL_POOR instead of miscounting."""
        if f.signal_ok and f.imu_ok:
            self._poor_since = None
            return
        if self._poor_since is None:
            self._poor_since = f.t
        elif f.t - self._poor_since >= self.cfg["safety"]["signal_poor_pause_s"]:
            self._poor_since = None
            self.pause("signal")

    # ------------------------------------------------------------ control (logged for replay)

    def session_action(self, action: str, arg=""):
        self._log_event("session", action)
        s = self.session
        if action == "practice":
            s.start_practice()
        elif action == "setup":
            s.setup()
        elif action == "start_set":
            if not s.start_set():
                self._emit("session_complete", s.summary())
        elif action == "rest":
            s.rest(arg or "user")
        elif action == "resume":
            s.resume()
        elif action == "finish":
            s.finish()
            self._emit("session_complete", s.summary())
        self.sm.reset()
        self._poor_since = None
        self._emit("session_state", {"state": s.state, "set_no": s.set_no})

    def new_session(self):
        """Fresh counters, fatigue baseline and state machine for the next user/session."""
        self.session = SessionManager(self.cfg["exercise"])
        self.fatigue.reset()
        self.sm.reset()
        self.feedback.clear()

    def pause(self, reason: str):
        self._log_event("session", "pause", reason)
        self.session.pause(reason)
        self.sm.reset()
        self.feedback.clear()
        self._emit("paused", reason)

    def set_load(self, load_kg: float):
        self._log_event("load_kg", load_kg)
        self.profile.load_kg = load_kg
        if self.logger:
            self.logger.save_calibration(self.profile)

    def start_capture(self, step: str, duration: float):
        self._log_event("capture", step, duration)
        link0 = self.link_stats.snapshot() if self.link_stats is not None else None
        self.capture = Capture(step, self.now, self.now + duration, emg0=self.counts["emg"],
                               imu0=self.counts["imu"], link0=link0)

    def cancel_capture(self):
        self.capture = None

    def _capture_frame(self, raw: RawFrame):
        c = self.capture
        if raw.t >= c.t_start:
            c.frames.append(raw)
        if raw.t >= c.t_end:
            self.capture = None
            self._finish_capture(c)

    def _finish_capture(self, c: Capture):
        p = self.profile
        if c.step == "sensor_check":
            dur = c.t_end - c.t_start
            loss = 0.0
            if c.link0 is not None:
                lost = self.link_stats.lost - c.link0.lost
                rec = self.link_stats.received - c.link0.received
                loss = lost / (lost + rec) if lost + rec else 0.0
            adc = np.concatenate(c.adc) if c.adc else np.zeros(0)
            res = self.calman.sensor_check(dur, self.counts["emg"] - c.emg0, self.counts["imu"] - c.imu0,
                                           loss, adc, self.cfg["emg"]["mode"], self.device_hint)
            if res.passed:
                apply_result(p, res)
                self.set_emg_mode(res.values["emg_mode"])
        elif c.step == "rest":
            res = self.calman.rest(c.frames)
        elif c.step == "zero":
            res = self.calman.zero(c.frames)
            if res.passed:
                self.last_zero_vals = res.values
        elif c.step == "top":
            res = self.calman.top(c.frames, p, self.last_zero_vals)
        elif c.step == "rvc":
            res = self.calman.rvc(c.frames, p)
        else:
            res = StepResult(c.step, False, f"unknown step {c.step}")
        if c.step != "sensor_check":
            apply_result(p, res)
        self._calibration_done(c.step, res)

    def _calibration_done(self, step: str, res: StepResult):
        self.apply_profile(self.profile)
        if self.logger:
            self.logger.save_calibration(self.profile)
            self.logger.log_event(self.now, "calibration", {"step": step, "passed": res.passed})
        if self.on_calibration:
            self.on_calibration(step, res)

    # ------------------------------------------------------------ helpers

    def _log_event(self, event: str, arg="", extra=None):
        if self.logger:
            self.logger.log_event(self.now, event, arg if extra is None else f"{arg}:{extra}")

    def _emit(self, name: str, payload=None):
        if self.on_event:
            self.on_event(name, payload)

    def status(self) -> dict:
        p = self.profile
        return {
            "emg_mode": self.emg_mode,
            "signal_ok": self.quality.ok(self.now),
            "quality_flags": sorted(self.quality.flags),
            "battery_mv": self.battery_mv,
            "calibrated": p.ready,
            "session_state": self.session.state,
            "sm_state": self.sm.state.value,
            "set_no": self.session.set_no,
            "valid_in_set": self.session.valid_in_set,
            "attempts_in_set": self.session.attempts_in_set,
            "invalid_in_set": self.session.invalid_in_set,
            "fatigue": self.fatigue.ewma,
        }

    def close(self):
        if self.logger:
            self.logger.close()
