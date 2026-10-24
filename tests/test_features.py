import numpy as np
from scipy import signal

from wristmon.features import amplitude_features, sparc, spectral_features

FS = 1000


def band_noise(lo, hi, n=4000, seed=0):
    sos = signal.butter(4, [lo, hi], btype="bandpass", fs=FS, output="sos")
    return signal.sosfilt(sos, np.random.default_rng(seed).standard_normal(n + 500))[500:]


def test_mdf_mnf_of_sinusoid():
    t = np.arange(2000) / FS
    f = spectral_features(np.sin(2 * np.pi * 120 * t), FS)
    assert abs(f["mdf"] - 120) < 4 and abs(f["mnf"] - 120) < 6


def test_mdf_of_known_band_spectrum():
    # Symmetric flat band 60-140 Hz -> median frequency ~100 Hz.
    f = spectral_features(band_noise(60, 140), FS)
    assert abs(f["mdf"] - 100) < 8


def test_mdf_two_tone_power_split():
    # Equal-power tones at 80 and 200 Hz: median lies between them; MNF ~ 140.
    t = np.arange(4000) / FS
    x = np.sin(2 * np.pi * 80 * t) + np.sin(2 * np.pi * 200 * t)
    f = spectral_features(x, FS)
    assert 80 <= f["mdf"] <= 200 and abs(f["mnf"] - 140) < 10


def test_fatigue_shift_lowers_mdf_and_raises_finsm5():
    fresh = spectral_features(band_noise(50, 200), FS)
    tired = spectral_features(band_noise(35, 140), FS)
    assert tired["mdf"] < fresh["mdf"] * 0.85
    assert tired["finsm5"] > fresh["finsm5"] * 1.5


def test_amplitude_in_rvc():
    x = 50 * np.sign(np.sin(np.arange(1000)))   # square wave amplitude 50
    a = amplitude_features(x, 100 / 100.0)      # RVC = 100 counts
    assert abs(a["rms_pct"] - 50) < 1 and abs(a["arv_pct"] - 50) < 1


def test_sparc_orders_smoothness():
    fs = 200
    tau = np.linspace(0, 1, 400)
    smooth = 30 * tau ** 2 * (1 - tau) ** 2           # minimum-jerk speed
    jerky = smooth * (1 + 0.6 * np.sin(2 * np.pi * 6 * tau * 2))
    s1, s2 = sparc(smooth, fs), sparc(np.abs(jerky), fs)
    assert -1.8 < s1 < -1.3
    assert s2 < s1 - 0.5
