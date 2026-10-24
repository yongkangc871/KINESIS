"""Feature extraction: EMG spectral/amplitude features and movement smoothness (SPARC)."""
from __future__ import annotations

import math

import numpy as np
from scipy import signal

NAN = float("nan")


def welch_psd(x: np.ndarray, fs: float, nperseg: int = 256):
    x = np.asarray(x, float)
    nper = min(nperseg, len(x))
    return signal.welch(x - np.mean(x), fs=fs, nperseg=nper, noverlap=nper // 2, window="hann")


def spectral_features(x: np.ndarray, fs: float, band=(20.0, 450.0), nperseg: int = 256) -> dict:
    """MDF, MNF and Dimitrov FInsm5 (M-1 / M5) from a Welch PSD restricted to `band`."""
    if len(x) < 64:
        return {"mdf": NAN, "mnf": NAN, "finsm5": NAN}
    f, p = welch_psd(x, fs, nperseg)
    m = (f >= band[0]) & (f <= band[1])
    f, p = f[m], p[m]
    total = p.sum()
    if total <= 0:
        return {"mdf": NAN, "mnf": NAN, "finsm5": NAN}
    cum = np.cumsum(p)
    k = int(np.searchsorted(cum, total / 2))
    # Linear interpolation inside the bin where half the power is reached.
    if 0 < k < len(f):
        frac = (total / 2 - cum[k - 1]) / p[k]
        mdf = f[k - 1] + frac * (f[k] - f[k - 1])
    else:
        mdf = f[min(k, len(f) - 1)]
    mnf = float(np.sum(f * p) / total)
    finsm5 = float(np.sum(p / f) / np.sum(p * f ** 5))
    return {"mdf": float(mdf), "mnf": mnf, "finsm5": finsm5}


def amplitude_features(x: np.ndarray, scale_pct: float) -> dict:
    """RMS and ARV of a filtered EMG segment, in %RVC (scale_pct = 100 / RVC)."""
    x = np.asarray(x, float)
    if len(x) == 0:
        return {"rms_pct": NAN, "arv_pct": NAN}
    return {"rms_pct": float(np.sqrt(np.mean(x ** 2)) * scale_pct),
            "arv_pct": float(np.mean(np.abs(x)) * scale_pct)}


def sparc(speed: np.ndarray, fs: float, padlevel: int = 4, fc: float = 10.0, amp_th: float = 0.05) -> float:
    """Spectral arc length (Balasubramanian et al. 2015). Closer to 0 = smoother; ~-1.4 for a
    minimum-jerk profile."""
    v = np.abs(np.asarray(speed, float))
    if len(v) < 8 or not np.any(v > 0):
        return NAN
    nfft = int(2 ** (math.ceil(math.log2(len(v))) + padlevel))
    f = np.arange(0, fs, fs / nfft)
    mag = np.abs(np.fft.fft(v, nfft))
    mag = mag / mag.max()
    sel = f <= fc
    f_sel, m_sel = f[sel], mag[sel]
    above = np.nonzero(m_sel >= amp_th)[0]
    if len(above) == 0:
        return NAN
    end = above[-1] + 1
    f_sel, m_sel = f_sel[:end], m_sel[:end]
    if len(f_sel) < 2:
        return NAN
    df = np.diff(f_sel) / (f_sel[-1] - f_sel[0])
    dm = np.diff(m_sel)
    return float(-np.sum(np.sqrt(df ** 2 + dm ** 2)))


def rep_emg_features(segment: np.ndarray, fs: float, rvc: float, mode: str, fatigue_cfg: dict,
                     offset: float = 0.0) -> dict:
    """Per-rep EMG features from the fixed lifting-angle window.

    Raw mode: `segment` is band-passed EMG. Envelope mode: `segment` is the envelope and only
    the amplitude is meaningful (MDF/MNF/FInsm5 are disabled).
    """
    if rvc is None or not math.isfinite(rvc) or rvc - offset <= 0 or len(segment) == 0:
        return {}
    if mode == "raw":
        feats = spectral_features(segment, fs, tuple(fatigue_cfg["band_hz"]), fatigue_cfg["welch_nperseg"])
        feats.update(amplitude_features(segment, 100.0 / rvc))
        return feats
    level = (np.asarray(segment, float) - offset) * 100.0 / (rvc - offset)
    return {"mdf": NAN, "mnf": NAN, "finsm5": NAN,
            "rms_pct": float(np.mean(level)), "arv_pct": float(np.mean(np.abs(level)))}
