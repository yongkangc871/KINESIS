"""Signal conditioning: sEMG filtering/envelope/onset/quality and IMU low-pass.

Everything here is pure and streaming (state carried between calls) so live data and
replayed files go through identical code. Offline (zero-phase) variants are provided for
analysis tools.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import signal

NAN = float("nan")


# ---------------------------------------------------------------- filter design

def design_emg_sos(fs: float, band=(20.0, 450.0), order: int = 4, notch_hz: float = 50.0,
                   harmonics: bool = True, q: float = 30.0) -> np.ndarray:
    """Butterworth band-pass plus mains notch(es), as one SOS cascade."""
    hi = min(band[1], 0.95 * fs / 2)
    sections = [signal.butter(order, [band[0], hi], btype="bandpass", fs=fs, output="sos")]
    if notch_hz:
        f = notch_hz
        while f < fs / 2 - 1:
            b, a = signal.iirnotch(f, q, fs=fs)
            sections.append(signal.tf2sos(b, a))
            if not harmonics:
                break
            f += notch_hz
    return np.vstack(sections)


def filter_emg_offline(x: np.ndarray, fs: float, **kw) -> np.ndarray:
    """Zero-phase version for offline analysis (sosfiltfilt)."""
    sos = design_emg_sos(fs, **kw)
    x = np.asarray(x, float)
    return signal.sosfiltfilt(sos, x - np.mean(x))


class StreamingSOS:
    """Causal SOS filter that keeps its state between blocks.

    The state is initialised to the steady state of the first input value, so a DC offset
    at start-up does not ring through the band-pass.
    """

    def __init__(self, sos: np.ndarray):
        self.sos = np.asarray(sos, float)
        self.zi = None

    def process(self, x) -> np.ndarray:
        x = np.atleast_1d(np.asarray(x, float))
        if self.zi is None:
            self.zi = signal.sosfilt_zi(self.sos) * x[0]
        y, self.zi = signal.sosfilt(self.sos, x, zi=self.zi)
        return y

    def reset(self):
        self.zi = None


class ScalarLowPass:
    """2nd-order Butterworth low-pass for one-sample-at-a-time streams (IMU)."""

    def __init__(self, fc: float, fs: float):
        sos = signal.butter(2, min(fc, 0.45 * fs), fs=fs, output="sos")
        self.b = sos[0, :3]
        self.a = sos[0, 3:]
        self.z1 = self.z2 = None

    def step(self, x: float) -> float:
        b0, b1, b2 = self.b
        _, a1, a2 = self.a
        if self.z1 is None:
            # Steady state for constant input x (direct form II transposed).
            self.z2 = (b2 - a2 * 1.0) * x
            self.z1 = (b1 - a1 * 1.0) * x + self.z2
        y = b0 * x + self.z1
        self.z1 = b1 * x - a1 * y + self.z2
        self.z2 = b2 * x - a2 * y
        return y


class SlidingRMS:
    """RMS over a sliding window of n samples, evaluated at every sample."""

    def __init__(self, n: int):
        self.n = max(1, int(n))
        self.tail = np.zeros(self.n - 1)
        self.count = 0

    def process(self, x: np.ndarray) -> np.ndarray:
        sq = np.square(x)
        buf = np.concatenate([self.tail, sq])
        cs = np.concatenate([[0.0], np.cumsum(buf)])
        n = self.n
        sums = cs[n:] - cs[:-n]
        # During warm-up divide by the number of real samples seen so far.
        idx = self.count + np.arange(1, len(x) + 1)
        denom = np.minimum(idx, n)
        self.count += len(x)
        self.tail = buf[len(buf) - (n - 1):] if n > 1 else np.zeros(0)
        return np.sqrt(np.maximum(sums, 0.0) / denom)


# ---------------------------------------------------------------- EMG

class OnsetDetector:
    """Muscle onset/offset: envelope above threshold for >= min_samples (and below for offset).

    onset_t is back-dated to the first sample of the run that triggered the onset.
    """

    def __init__(self, min_samples: int):
        self.min_samples = max(1, int(min_samples))
        self.threshold = math.inf
        self.active = False
        self.run = 0
        self.run_start = NAN
        self.onset_t = NAN

    def set_threshold(self, thr: float):
        self.threshold = thr if math.isfinite(thr) else math.inf

    def process(self, t: np.ndarray, env: np.ndarray):
        active = np.zeros(len(env), bool)
        onset = np.full(len(env), NAN)
        thr = self.threshold
        for i in range(len(env)):
            above = env[i] > thr
            if above != self.active:
                if self.run == 0:
                    self.run_start = t[i]
                self.run += 1
                if self.run >= self.min_samples:
                    self.active = above
                    self.run = 0
                    self.onset_t = self.run_start if above else NAN
            else:
                self.run = 0
            active[i] = self.active
            onset[i] = self.onset_t
        return active, onset


class EmgConditioner:
    """Raw mode: DC removal, band-pass + notch, 100 ms RMS envelope.
    Envelope mode: the module already outputs an envelope; low-pass smoothing only."""

    def __init__(self, emg_cfg: dict, mode: str):
        self.mode = mode
        self.fs = float(emg_cfg["fs_hz"])
        fs = self.fs
        self.dc_alpha = 1.0 / max(1.0, emg_cfg.get("dc_tau_s", 1.0) * fs)
        self.dc = None
        if mode == "raw":
            self.filt = StreamingSOS(design_emg_sos(
                fs, tuple(emg_cfg["bandpass_hz"]), emg_cfg["bandpass_order"],
                emg_cfg["notch_hz"], emg_cfg["notch_harmonics"], emg_cfg["notch_q"]))
            self.rms = SlidingRMS(round(emg_cfg["rms_window_s"] * fs))
        else:
            self.filt = StreamingSOS(signal.butter(2, emg_cfg["envelope_smooth_hz"], fs=fs, output="sos"))
            self.rms = None
        self.onset = OnsetDetector(round(emg_cfg["onset_min_s"] * fs))
        self.k_sd = emg_cfg["onset_k_sd"]

    def set_rest(self, rest_mean: float, rest_sd: float):
        self.onset.set_threshold(rest_mean + self.k_sd * rest_sd)

    def process(self, t: np.ndarray, x: np.ndarray):
        """Returns (filtered, envelope, active, onset_t) arrays aligned with t."""
        x = np.asarray(x, float)
        if self.mode == "raw":
            if self.dc is None:
                self.dc = float(x[0])
            # Running-mean DC removal (one-pole) ahead of the band-pass.
            dc = np.empty_like(x)
            d, a = self.dc, self.dc_alpha
            for i, v in enumerate(x):
                d += a * (v - d)
                dc[i] = d
            self.dc = d
            filtered = self.filt.process(x - dc)
            env = self.rms.process(filtered)
        else:
            env = self.filt.process(x)
            filtered = env
        active, onset = self.onset.process(t, env)
        return filtered, env, active, onset


def detect_emg_mode(adc: np.ndarray, fs: float, adc_max: float, hf_ratio_min=0.4,
                    envelope_mean_max=0.2) -> tuple[str, dict]:
    """Guess whether the module outputs raw EMG or an envelope.

    Raw EMG sits on a mid-rail bias and has most of its AC power above 20 Hz.
    An envelope sits near 0 V at rest and is slow. Returns (mode, evidence).
    """
    x = np.asarray(adc, float)
    if len(x) < fs * 0.5:
        return "unknown", {"reason": "not enough data"}
    mean_frac = float(np.mean(x) / adc_max)
    ac = x - np.mean(x)
    f, p = signal.welch(ac, fs=fs, nperseg=min(len(ac), 512))
    total = float(np.sum(p[(f > 0.5) & (f < 450)]))
    hf = float(np.sum(p[(f >= 20) & (f < 450)]))
    ratio = hf / total if total > 0 else 0.0
    evidence = {"mean_fraction": round(mean_frac, 3), "hf_ratio": round(ratio, 3)}
    if mean_frac < envelope_mean_max:
        return "envelope", evidence
    return ("raw" if ratio >= hf_ratio_min else "envelope"), evidence


class EmgQuality:
    """Continuous signal-quality checks. Any failure sets SIGNAL_POOR for hold_s."""

    def __init__(self, qcfg: dict, fs: float, mode: str, adc_max: float, mains_hz: float = 50.0):
        self.cfg = qcfg
        self.fs = fs
        self.mode = mode
        self.adc_max = adc_max
        self.mains_hz = mains_hz
        self.n_win = int(qcfg["window_s"] * fs)
        self.eval_every = max(1, int(qcfg["eval_every_s"] * fs))
        self.buf = np.zeros(0)
        self.env_buf = np.zeros(0)
        self.since_eval = 0
        self.last_sample = None
        self.bad_until = -math.inf
        self.flags: set[str] = set()
        self.window_flags: set[str] = set()
        self.rest_context = False
        self.rest_level = NAN   # calibrated rest envelope level

    def set_rest_level(self, level: float):
        self.rest_level = level

    def process(self, t_end: float, raw: np.ndarray, env: np.ndarray, gap: bool = False) -> bool:
        cfg = self.cfg
        now_flags = set()
        if gap:
            now_flags.add("dropout")
        first = raw[0] if self.last_sample is None else self.last_sample
        jumps = np.abs(np.diff(np.concatenate([[first], raw])))
        if jumps.size and jumps.max() > cfg["jump_adc"]:
            now_flags.add("jump")
        self.last_sample = raw[-1]

        self.buf = np.concatenate([self.buf, raw])[-self.n_win:]
        self.env_buf = np.concatenate([self.env_buf, env])[-self.n_win:]
        self.since_eval += len(raw)
        if self.since_eval >= self.eval_every and len(self.buf) >= self.n_win:
            self.since_eval = 0
            self.window_flags = self._window_checks()
        now_flags |= self.window_flags

        if now_flags:
            self.bad_until = t_end + cfg["hold_s"]
        self.flags = now_flags if now_flags else (self.flags if t_end < self.bad_until else set())
        return self.ok(t_end)

    def ok(self, t: float) -> bool:
        return t >= self.bad_until

    def _window_checks(self) -> set:
        cfg = self.cfg
        x = self.buf
        flags = set()
        if np.std(x) < cfg["flatline_std_adc"]:
            flags.add("flatline")
        m = cfg["clip_margin_adc"]
        if np.mean((x <= m) | (x >= self.adc_max - m)) > cfg["clip_fraction"]:
            flags.add("clipping")
        if self.mode == "raw":
            ac = x - np.mean(x)
            spec = np.abs(np.fft.rfft(ac * np.hanning(len(ac)))) ** 2
            f = np.fft.rfftfreq(len(ac), 1 / self.fs)
            band = (f >= 20) & (f <= 450)
            total = spec[band].sum()
            mains = np.zeros_like(f, bool)
            h = self.mains_hz
            while h < self.fs / 2:
                mains |= np.abs(f - h) <= 1.5
                h += self.mains_hz
            ratio = spec[mains & band].sum() / total if total > 0 else 0.0
            mains_rms = math.sqrt(ratio * np.var(ac))
            if ratio > cfg["mains_ratio_max"] and mains_rms > cfg["mains_min_rms_adc"]:
                flags.add("mains")
        if self.rest_context and math.isfinite(self.rest_level) and self.rest_level > 0:
            if np.median(self.env_buf) > self.rest_level * cfg["rest_noise_k"]:
                flags.add("rest_noise")
        return flags


# ---------------------------------------------------------------- IMU

def wrap180(a):
    """Wraps degrees into [-180, 180); works on floats and arrays."""
    return (a + 180.0) % 360.0 - 180.0


def circular_mean_deg(a) -> float:
    r = np.radians(np.asarray(a, float))
    return float(np.degrees(math.atan2(np.mean(np.sin(r)), np.mean(np.cos(r)))))


class ImuConditioner:
    """Per-IMU low-pass of pitch (theta source) and dropout detection."""

    def __init__(self, imu_cfg: dict):
        self.cfg = imu_cfg
        self.lp = {}
        self.last_t = {}

    def process(self, s):
        """Returns (pitch_filtered, gap_before) for an ImuSample."""
        lp = self.lp.get(s.imu_id)
        if lp is None:
            lp = self.lp[s.imu_id] = ScalarLowPass(self.cfg["theta_lowpass_hz"], self.cfg["rate_hz"])
        last = self.last_t.get(s.imu_id)
        gap = last is not None and (s.t - last) > self.cfg["gap_s"]
        self.last_t[s.imu_id] = s.t
        return lp.step(s.pitch), gap


GYRO_AXES = {"x": 0, "y": 1, "z": 2}


def gravity_in_sensor(roll_deg: float, pitch_deg: float, signs=(1, 1, 1)) -> np.ndarray:
    """Gravity reaction (what a still accelerometer reads, in g) for ZYX Euler angles."""
    r, p = math.radians(roll_deg), math.radians(pitch_deg)
    return np.array([-math.sin(p) * signs[0],
                     math.sin(r) * math.cos(p) * signs[1],
                     math.cos(r) * math.cos(p) * signs[2]])


class TimeRing:
    """Time-stamped multi-channel buffer keeping the last `seconds` of data."""

    def __init__(self, seconds: float, rate: float, channels: int = 1):
        self.cap = int(seconds * rate)
        self.t = np.zeros(2 * self.cap)
        self.v = np.zeros((2 * self.cap, channels))
        self.n = 0

    def extend(self, t: np.ndarray, values: np.ndarray):
        values = np.asarray(values, float).reshape(len(t), -1)
        k = len(t)
        if self.n + k > len(self.t):
            keep = self.cap - k if k < self.cap else 0
            self.t[:keep] = self.t[self.n - keep:self.n]
            self.v[:keep] = self.v[self.n - keep:self.n]
            self.n = keep
        self.t[self.n:self.n + k] = t
        self.v[self.n:self.n + k] = values
        self.n += k

    def at(self, t: float):
        """Values of the last sample at or before t (or the first sample)."""
        if self.n == 0:
            return None
        i = int(np.searchsorted(self.t[:self.n], t, side="right")) - 1
        return self.v[max(i, 0)]

    def slice(self, t0: float, t1: float):
        tt = self.t[:self.n]
        a = int(np.searchsorted(tt, t0, side="left"))
        b = int(np.searchsorted(tt, t1, side="right"))
        return tt[a:b], self.v[a:b]

    @property
    def latest_t(self) -> float:
        return float(self.t[self.n - 1]) if self.n else NAN
