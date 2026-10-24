"""Calibration manager and per-user profile.

Each wizard step captures RawFrames (plus raw ADC for the sensor check) and is analysed by
a pure function here. Results carry pass/fail, a plain-language retry message and the
values that get written into the Profile.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import product
from pathlib import Path

import numpy as np

from .conditioning import circular_mean_deg, detect_emg_mode, gravity_in_sensor, wrap180, GYRO_AXES
from .datatypes import RawFrame

NAN = float("nan")

FULL_STEPS = ["sensor_check", "rest", "zero", "top", "rvc", "practice"]
QUICK_STEPS = ["sensor_check", "rest", "zero", "practice"]   # practice = warm-up every session


def emg_driven(cfg: dict) -> bool:
    """Exercises counted from the muscle signal instead of an angle (exercise.signal: emg):
    the sensor segment does not move (quad sets) or is not the moving one."""
    return cfg["exercise"].get("signal") == "emg"


def steps_for(cfg: dict, steps: list[str]) -> list[str]:
    """Wizard steps for this exercise: a muscle-driven one takes its reference from the
    strongest squeeze (the top step), so it has no separate half-way hold."""
    return [s for s in steps if not (s == "rvc" and emg_driven(cfg))]

# Retry messages that name the body part; an exercise overrides them through cfg["texts"].
TEXTS = {
    "sensor": "hand sensor",
    "rest_still": "Please keep your hand still and relaxed.",
    "zero_still": "Please hold your hand still, in line with your arm.",
    "top_more": "Curl your wrist up a little more, then hold.",
    "rvc_angle": "Hold your hand halfway up (about {want:.0f}°).",
    "rvc_small": "Muscle signal too small. Press the pads firmly and hold the weight.",
}


@dataclass
class Profile:
    user: str
    emg_mode: str = "raw"
    rest_mean: float = NAN       # envelope units
    rest_sd: float = NAN
    rvc: float = NAN             # mean envelope during the reference contraction
    pitch0: float = 0.0
    roll0: float = 180.0
    yaw0: float = 0.0
    fa_pitch0: float = 0.0
    flex_sign: int = 1           # theta = flex_sign * (pitch - pitch0) + start_angle
    omega_sign: int = 1          # omega = omega_sign * gyro[hinge]
    gravity_signs: list | None = None   # accelerometer model; None = magnitude-only check
    rom_deg: float = NAN
    target_deg: float = 45.0
    start_angle: float = 0.0
    load_kg: float = 5.0
    tempo: dict = field(default_factory=dict)
    practice_activation_pct: float = NAN
    full_calibrated: bool = False
    last_full_calibration: str = ""
    last_quick_calibration: str = ""

    @property
    def ready(self) -> bool:
        return self.full_calibrated and math.isfinite(self.rvc)

    @property
    def emg_offset(self) -> float:
        """Envelope value meaning 0 %RVC (envelope-mode modules have a resting DC level)."""
        return self.rest_mean if self.emg_mode == "envelope" and math.isfinite(self.rest_mean) else 0.0

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps({k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in d.items()},
                          indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        known = {k: (NAN if v is None and isinstance(cls.__dataclass_fields__[k].default, float) else v)
                 for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, directory: str | Path) -> Path:
        p = Path(directory) / f"{safe_name(self.user)}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def safe_name(user: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in user.strip()) or "user"


def list_profiles(directory: str | Path) -> list[str]:
    d = Path(directory)
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


@dataclass
class StepResult:
    step: str
    passed: bool
    message: str
    values: dict = field(default_factory=dict)


def _arr(frames: list[RawFrame], attr: str) -> np.ndarray:
    return np.array([getattr(f, attr) for f in frames], float)


def _gyro(frames, axis: int) -> np.ndarray:
    return np.array([f.gyro[axis] for f in frames], float)


def _still(frames, cal: dict) -> tuple[bool, float, float]:
    pitch = _arr(frames, "pitch")
    gmag = np.array([math.sqrt(sum(g * g for g in f.gyro)) for f in frames])
    sd = float(np.std(pitch))
    g = float(np.mean(gmag))
    return (sd < cal["still_theta_sd_deg"] and g < cal["still_gyro_dps"]), sd, g


def _theta_raw(frames, profile: Profile, forearm: bool) -> np.ndarray:
    """Pitch change from the zero pose, before the flexion sign is known."""
    d = wrap180(_arr(frames, "pitch") - profile.pitch0)
    if forearm:
        d = d - wrap180(_arr(frames, "fa_pitch") - profile.fa_pitch0)
    return d


class CalibrationManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cal = cfg["calibration"]
        self.texts = {**TEXTS, **(cfg.get("texts") or {})}

    # Step 1 --------------------------------------------------------------
    def sensor_check(self, duration: float, emg_samples: int, imu_samples: int, loss: float,
                     adc: np.ndarray, emg_cfg_mode: str, device_hint: str = "unknown") -> StepResult:
        cal, cfg = self.cal, self.cfg
        emg_rate = emg_samples / duration if duration > 0 else 0
        imu_rate = imu_samples / duration if duration > 0 else 0
        detected, evidence = detect_emg_mode(adc, cfg["emg"]["fs_hz"], cfg["emg"]["adc_max"],
                                             **cfg["emg"]["auto_detect"])
        if emg_cfg_mode in ("raw", "envelope"):
            mode = emg_cfg_mode
        elif device_hint in ("raw", "envelope"):
            mode = device_hint
        else:
            mode = detected if detected != "unknown" else "raw"
        vals = {"emg_rate": round(emg_rate, 1), "imu_rate": round(imu_rate, 1), "loss": round(loss, 4),
                "emg_mode": mode, "detected_mode": detected, **evidence}
        need = cal["min_rate_fraction"]
        if emg_rate < need * cfg["emg"]["fs_hz"]:
            return StepResult("sensor_check", False, "The muscle sensor is not sending data. Check the cable.", vals)
        if imu_rate < need * cfg["imu"]["rate_hz"]:
            return StepResult("sensor_check", False,
                              f"The {self.texts['sensor']} is not sending data. Check the cable.", vals)
        if loss > cal["max_packet_loss"]:
            return StepResult("sensor_check", False, "The connection is unstable. Move closer or re-plug the USB.", vals)
        return StepResult("sensor_check", True, "Sensors are working", vals)

    # Step 2 --------------------------------------------------------------
    def rest(self, frames: list[RawFrame]) -> StepResult:
        if len(frames) < 10:
            return StepResult("rest", False, "No data. Please try again.")
        env = _arr(frames, "env")
        still, sd, g = _still(frames, self.cal)
        ok_frac = float(np.mean([f.emg_ok for f in frames]))
        vals = {"rest_mean": float(np.mean(env)), "rest_sd": float(np.std(env)),
                "theta_sd": round(sd, 2), "gyro_mean": round(g, 2), "emg_ok_fraction": round(ok_frac, 2)}
        if ok_frac < 0.8:
            return StepResult("rest", False, "Muscle sensor signal is poor. Press the pads firmly on clean skin.", vals)
        if not still:
            return StepResult("rest", False, self.texts["rest_still"], vals)
        return StepResult("rest", True, "Resting level recorded", vals)

    # Step 3 --------------------------------------------------------------
    def zero(self, frames: list[RawFrame]) -> StepResult:
        if len(frames) < 10:
            return StepResult("zero", False, "No data. Please try again.")
        still, sd, g = _still(frames, self.cal)
        start_angle = -self.cal["extension_deg"] if self.cal["start_position"] == "extension" else 0.0
        vals = {"pitch0": circular_mean_deg(_arr(frames, "pitch")),
                "roll0": circular_mean_deg(_arr(frames, "roll")),
                "yaw0": circular_mean_deg(_arr(frames, "yaw")),
                "start_angle": start_angle,
                "acc_zero": np.mean([f.acc for f in frames], axis=0).tolist(),
                "euler_zero": [circular_mean_deg(_arr(frames, "roll")), circular_mean_deg(_arr(frames, "pitch"))]}
        fa = _arr(frames, "fa_pitch")
        if np.all(np.isfinite(fa)):
            vals["fa_pitch0"] = circular_mean_deg(fa)
        vals.update(theta_sd=round(sd, 2), gyro_mean=round(g, 2))
        if not still:
            return StepResult("zero", False, self.texts["zero_still"], vals)
        return StepResult("zero", True, "Start position recorded", vals)

    # Step 4 --------------------------------------------------------------
    def top(self, frames: list[RawFrame], profile: Profile, zero_vals: dict | None = None) -> StepResult:
        cal = self.cal
        if len(frames) < 20:
            return StepResult("top", False, "No data. Please try again.")
        if emg_driven(self.cfg):
            return self._top_emg(frames, profile, zero_vals)
        forearm = self.cfg["imu"]["forearm_imu"]
        t = _arr(frames, "t")
        hold = t >= t[-1] - cal["top_hold_s"]
        d = _theta_raw(frames, profile, forearm)
        d_hold = float(np.mean(d[hold]))
        flex_sign = 1 if d_hold >= 0 else -1
        rom = abs(d_hold) + profile.start_angle
        # The gyro hinge sign comes from how pitch and gyro move together during the curl.
        axis = GYRO_AXES[self.cfg["imu"]["hinge_axis"]]
        g = _gyro(frames, axis)
        dp = np.gradient(_arr(frames, "pitch"), t)
        moving = np.abs(dp) > 5
        omega_sign = profile.omega_sign
        if moving.sum() > 5:
            corr = float(np.sum(dp[moving] * g[moving]))
            if corr != 0:
                omega_sign = flex_sign * (1 if corr > 0 else -1)
        still, sd, gm = _still([f for f, h in zip(frames, hold) if h], cal)
        target = min(self.cfg["exercise"]["target_deg"], cal["rom_fraction"] * rom)
        vals = {"flex_sign": flex_sign, "omega_sign": omega_sign, "rom_deg": round(rom, 1),
                "target_deg": round(target, 1), "theta_sd": round(sd, 2)}
        gs = fit_gravity_signs(frames, zero_vals)
        vals["gravity_signs"] = gs
        if rom < cal["min_rom_deg"]:
            return StepResult("top", False, self.texts["top_more"], vals)
        if not still:
            return StepResult("top", False, "Hold still at the top for the count.", vals)
        return StepResult("top", True, f"Your range is {rom:.0f}°. Target set to {target:.0f}°.", vals)

    def _top_emg(self, frames: list[RawFrame], profile: Profile, zero_vals: dict | None) -> StepResult:
        """Muscle-driven exercise: the strongest squeeze held at the end becomes 100 %, and
        reps aim for exercise.target_deg percent of it."""
        cal = self.cal
        t = _arr(frames, "t")
        hold = t >= t[-1] - cal["top_hold_s"]
        ref = float(np.mean(_arr(frames, "env")[hold]))
        target = float(self.cfg["exercise"]["target_deg"])
        vals = {"flex_sign": 1, "omega_sign": profile.omega_sign, "rom_deg": 100.0, "target_deg": target,
                "rvc": ref, "gravity_signs": fit_gravity_signs(frames, zero_vals)}
        if math.isfinite(profile.rest_mean) and ref < profile.rest_mean + cal["min_rvc_sd"] * max(profile.rest_sd, 1e-9):
            return StepResult("top", False, self.texts["rvc_small"], vals)
        if not np.mean([f.emg_ok for f in frames]) > 0.8:
            return StepResult("top", False, "Muscle sensor signal is poor. Press the sensor firmly.", vals)
        return StepResult("top", True, f"Strongest squeeze recorded. Aim for {target:.0f}% of it.", vals)

    # Step 5 --------------------------------------------------------------
    def rvc(self, frames: list[RawFrame], profile: Profile) -> StepResult:
        cal = self.cal
        if len(frames) < 20:
            return StepResult("rvc", False, "No data. Please try again.")
        forearm = self.cfg["imu"]["forearm_imu"]
        theta = profile.flex_sign * _theta_raw(frames, profile, forearm) + profile.start_angle
        env = _arr(frames, "env")
        want = profile.target_deg / 2
        mean_theta = float(np.mean(theta))
        rvc = float(np.mean(env))
        vals = {"rvc": rvc, "theta_mean": round(mean_theta, 1)}
        if abs(mean_theta - want) > cal["rvc_angle_tol_deg"]:
            return StepResult("rvc", False, self.texts["rvc_angle"].format(want=want), vals)
        if math.isfinite(profile.rest_mean) and rvc < profile.rest_mean + cal["min_rvc_sd"] * max(profile.rest_sd, 1e-9):
            return StepResult("rvc", False, self.texts["rvc_small"], vals)
        if not np.mean([f.emg_ok for f in frames]) > 0.8:
            return StepResult("rvc", False, "Muscle sensor signal is poor. Press the pads firmly.", vals)
        return StepResult("rvc", True, "Muscle reference recorded", vals)

    # Step 6 --------------------------------------------------------------
    def practice(self, reps: list) -> StepResult:
        if not reps:
            return StepResult("practice", False, "No practice reps detected. Follow the moving guide.")
        tempo = {"lift_s": float(np.nanmean([r.lift_s for r in reps])),
                 "hold_s": float(np.nanmean([r.hold_s for r in reps])),
                 "lower_s": float(np.nanmean([r.lower_s for r in reps]))}
        act = float(np.nanmean([r.mean_pct for r in reps]))
        vals = {"tempo": {k: round(v, 2) for k, v in tempo.items()}, "practice_activation_pct": round(act, 1),
                "practice_valid": sum(r.valid for r in reps)}
        return StepResult("practice", True, "Practice done. Well done!", vals)


def fit_gravity_signs(frames: list[RawFrame], zero_vals: dict | None) -> list | None:
    """Picks the accelerometer sign convention that best explains still poses.

    Uses the zero pose and the top hold. Returns None (magnitude-only forearm check) if no
    convention fits within 0.1 g, which protects against mounting surprises.
    """
    poses = []
    if zero_vals and "acc_zero" in zero_vals:
        poses.append((zero_vals["euler_zero"][0], zero_vals["euler_zero"][1], np.array(zero_vals["acc_zero"])))
    tail = frames[-max(10, len(frames) // 4):]
    poses.append((circular_mean_deg([f.roll for f in tail]), circular_mean_deg([f.pitch_raw for f in tail]),
                  np.mean([f.acc for f in tail], axis=0)))
    best, best_err = None, math.inf
    for signs in product((1, -1), repeat=3):
        err = max(float(np.linalg.norm(gravity_in_sensor(r, p, signs) - a)) for r, p, a in poses)
        if err < best_err:
            best, best_err = list(signs), err
    return best if best_err < 0.1 else None


def apply_result(profile: Profile, res: StepResult):
    """Writes a passed step's values into the profile."""
    if not res.passed:
        return
    v = res.values
    if res.step == "sensor_check":
        profile.emg_mode = v["emg_mode"]
    elif res.step == "rest":
        profile.rest_mean, profile.rest_sd = v["rest_mean"], v["rest_sd"]
    elif res.step == "zero":
        profile.pitch0, profile.roll0, profile.yaw0 = v["pitch0"], v["roll0"], v["yaw0"]
        profile.start_angle = v["start_angle"]
        if "fa_pitch0" in v:
            profile.fa_pitch0 = v["fa_pitch0"]
        profile.last_quick_calibration = datetime.now().isoformat(timespec="seconds")
    elif res.step == "top":
        profile.flex_sign, profile.omega_sign = v["flex_sign"], v["omega_sign"]
        profile.rom_deg, profile.target_deg = v["rom_deg"], v["target_deg"]
        profile.gravity_signs = v["gravity_signs"]
        if "rvc" in v:           # muscle-driven exercise: the reference comes from this step
            profile.rvc = v["rvc"]
    elif res.step == "rvc":
        profile.rvc = v["rvc"]
    elif res.step == "practice":
        profile.tempo = v["tempo"]
        profile.practice_activation_pct = v["practice_activation_pct"]
        profile.full_calibrated = True
        profile.last_full_calibration = datetime.now().isoformat(timespec="seconds")
