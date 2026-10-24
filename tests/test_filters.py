import numpy as np
from scipy import signal

from wristmon.conditioning import (EmgConditioner, EmgQuality, OnsetDetector, ScalarLowPass, SlidingRMS,
                                   StreamingSOS, design_emg_sos, detect_emg_mode, filter_emg_offline)

FS = 1000


def gain_db(sos, f):
    _, h = signal.sosfreqz(sos, worN=[f], fs=FS)
    return 20 * np.log10(abs(h[0]))


def test_bandpass_and_notch_response():
    sos = design_emg_sos(FS)
    assert gain_db(sos, 75) > -3             # passband between notches
    assert gain_db(sos, 5) < -30             # motion artefact band
    for f in (50, 100, 150, 250):
        assert gain_db(sos, f) < -30         # mains and harmonics


def test_streaming_matches_one_shot():
    x = np.random.default_rng(0).standard_normal(5000)
    sos = design_emg_sos(FS)
    ref, _ = signal.sosfilt(sos, x, zi=signal.sosfilt_zi(sos) * x[0])
    f = StreamingSOS(sos)
    out = np.concatenate([f.process(x[i:i + 10]) for i in range(0, len(x), 10)])
    assert np.allclose(out, ref)


def test_zero_phase_offline_has_no_lag():
    t = np.arange(4000) / FS
    x = np.sin(2 * np.pi * 75 * t)     # between mains harmonics
    y = filter_emg_offline(x, FS)
    mid = slice(1000, 3000)
    assert np.corrcoef(x[mid], y[mid])[0, 1] > 0.99


def test_sliding_rms_matches_direct():
    x = np.random.default_rng(1).standard_normal(1000)
    r = SlidingRMS(100)
    out = np.concatenate([r.process(x[i:i + 7]) for i in range(0, 1000, 7)])
    i = 500
    assert abs(out[i] - np.sqrt(np.mean(x[i - 99:i + 1] ** 2))) < 1e-9


def test_scalar_lowpass_starts_settled_and_attenuates():
    lp = ScalarLowPass(10, 200)
    assert abs(lp.step(5.0) - 5.0) < 1e-12
    t = np.arange(400) / 200
    y = [lp.step(5.0 + np.sin(2 * np.pi * 40 * ti)) for ti in t]
    assert np.ptp(y[200:]) < 0.3


def test_onset_needs_min_duration_and_backdates():
    d = OnsetDetector(50)
    d.set_threshold(1.0)
    t = np.arange(300) / FS
    env = np.zeros(300)
    env[50:80] = 2.0        # 30 ms blip: ignored
    env[150:300] = 2.0      # sustained: onset at t=0.150
    active, onset = d.process(t, env)
    assert not active[:199].any()
    assert active[199] and abs(onset[250] - 0.150) < 1e-9


def test_conditioner_envelope_tracks_amplitude(cfg):
    c = EmgConditioner(cfg["emg"], "raw")
    rng = np.random.default_rng(2)
    x = 2048 + np.concatenate([5 * rng.standard_normal(2000), 200 * rng.standard_normal(2000)])
    t = np.arange(len(x)) / FS
    _, env, _, _ = c.process(t, x)
    assert env[1500] < 15 and 120 < env[3500] < 260


def test_mode_detection():
    rng = np.random.default_rng(3)
    raw = 2048 + 50 * rng.standard_normal(3000)
    t = np.arange(3000) / FS
    env = 150 + 400 * (1 + np.sin(2 * np.pi * 0.5 * t)) + 2 * rng.standard_normal(3000)
    assert detect_emg_mode(raw, FS, 4095)[0] == "raw"
    assert detect_emg_mode(env, FS, 4095)[0] == "envelope"


def _quality_flags(cfg, x, rest_context=False, rest_level=float("nan")):
    q = EmgQuality(cfg["emg"]["quality"], FS, "raw", 4095)
    q.rest_context = rest_context
    q.set_rest_level(rest_level)
    env = np.abs(x - np.mean(x))
    seen = set()
    for i in range(0, len(x), 10):
        q.process((i + 10) / FS, x[i:i + 10], env[i:i + 10])
        seen |= q.flags
    return seen, q


def test_quality_checks(cfg):
    rng = np.random.default_rng(4)
    t = np.arange(2000) / FS
    good = 2048 + 30 * rng.standard_normal(2000)
    assert _quality_flags(cfg, good)[0] == set()
    assert "flatline" in _quality_flags(cfg, np.full(2000, 2048.0))[0]
    clip = good.copy()
    clip[::20] = 4095
    assert "clipping" in _quality_flags(cfg, clip)[0]
    mains = 2048 + 200 * np.sin(2 * np.pi * 50 * t) + 5 * rng.standard_normal(2000)
    assert "mains" in _quality_flags(cfg, mains)[0]
    jump = good.copy()
    jump[1000:] += 1500
    assert "jump" in _quality_flags(cfg, jump)[0]
    assert "rest_noise" in _quality_flags(cfg, good, rest_context=True, rest_level=3.0)[0]
    flags, q = _quality_flags(cfg, jump)
    assert not q.ok(1.1) and q.ok(3.5)    # SIGNAL_POOR is held, then clears
