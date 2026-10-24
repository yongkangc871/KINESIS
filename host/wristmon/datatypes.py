"""Plain data records passed between pipeline stages."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import numpy as np

NAN = float("nan")


@dataclass
class EmgBlock:
    t0: float               # s, device clock (first sample)
    dt: float               # s between samples
    samples: np.ndarray     # ADC counts
    seq: int = -1

    @property
    def times(self) -> np.ndarray:
        return self.t0 + self.dt * np.arange(len(self.samples))


@dataclass
class ImuSample:
    t: float
    imu_id: int             # 0 = hand, 1 = forearm
    roll: float             # deg, fused Euler (JY901S)
    pitch: float
    yaw: float
    gx: float               # deg/s
    gy: float
    gz: float
    ax: float               # g
    ay: float
    az: float
    seq: int = -1


@dataclass
class DeviceStatus:
    t: float
    battery_mv: int
    flags: int
    emg_hint: int           # 0 unknown, 1 raw, 2 envelope
    fw_version: int
    seq: int = -1


@dataclass
class RawFrame:
    """One hand-IMU sample with the EMG envelope at the same instant, before calibration."""
    t: float
    pitch: float            # low-passed
    pitch_raw: float
    roll: float
    yaw: float
    gyro: tuple             # (gx, gy, gz)
    acc: tuple              # (ax, ay, az)
    env: float              # EMG envelope, conditioner units (RMS counts or envelope counts)
    emg_ok: bool
    imu_ok: bool
    fa_pitch: float = NAN   # forearm IMU pitch, if fitted


@dataclass
class Frame:
    """Calibrated sample the rep state machine runs on."""
    t: float
    theta: float            # wrist flexion, deg (+ = flexion)
    omega: float            # deg/s about the hinge (+ = flexing)
    offplane: float         # deg/s, magnitude of the other two gyro axes
    roll_dev: float         # deg from calibrated roll (pronation/supination)
    yaw_dev: float          # deg from calibrated yaw (radial/ulnar deviation)
    acc_resid: float        # g, acceleration not explained by gravity + wrist hinge motion
    env_pct: float          # EMG activation, %RVC
    emg_active: bool        # onset detector state
    emg_onset_t: float      # start time of the current active run (nan if inactive)
    signal_ok: bool         # EMG quality ok
    imu_ok: bool            # no IMU dropout before this sample
    forearm_dev: float = NAN

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RepRecord:
    index: int = 0                  # 1-based attempt number within the session
    set_no: int = 0
    practice: bool = False
    t_start: float = NAN            # movement onset
    t_end: float = NAN
    valid: bool = False
    faults: list = field(default_factory=list)
    primary_fault: str = ""
    message: str = ""
    peak_theta: float = NAN
    target_deg: float = NAN
    lift_s: float = NAN
    hold_s: float = NAN
    lower_s: float = NAN
    peak_omega_lift: float = NAN
    peak_omega_lower: float = NAN
    onset_lag_s: float = NAN
    mean_pct: float = NAN
    peak_pct: float = NAN
    max_roll_dev: float = NAN
    max_yaw_dev: float = NAN
    sparc: float = NAN
    window: tuple = (NAN, NAN)      # times of the fatigue feature window (10-35 deg while lifting)
    features: dict = field(default_factory=dict)
    fatigue_score: float = NAN
    fatigue_zone: str = ""
    jasa: str = ""

    CSV_FIELDS = [
        "index", "set_no", "practice", "t_start", "t_end", "valid", "faults", "primary_fault",
        "peak_theta", "target_deg", "lift_s", "hold_s", "lower_s", "peak_omega_lift",
        "peak_omega_lower", "onset_lag_s", "mean_pct", "peak_pct", "max_roll_dev",
        "max_yaw_dev", "sparc", "fatigue_score", "fatigue_zone", "jasa",
    ]
    FEATURE_FIELDS = ["mdf", "mnf", "finsm5", "rms_pct", "arv_pct"]

    def csv_row(self) -> dict:
        row = {}
        for k in self.CSV_FIELDS:
            v = getattr(self, k)
            if k == "faults":
                v = "|".join(v)
            elif isinstance(v, float):
                v = "" if math.isnan(v) else round(v, 5)
            row[k] = v
        for k in self.FEATURE_FIELDS:
            v = self.features.get(k, NAN)
            row[k] = "" if v is None or (isinstance(v, float) and math.isnan(v)) else v
        return row
