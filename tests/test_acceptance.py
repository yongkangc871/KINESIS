"""End-to-end: virtual patient -> binary protocol -> pipeline -> logs -> replay.

Mirrors the acceptance demo in the spec, without hardware.
"""
import time

import pytest

from wristmon import faults as F
from wristmon.calibration import QUICK_STEPS
from wristmon.headless import replay_session, run_simulated_session
from wristmon.simulator import EXPECTED_FAULT, REP_KINDS
from wristmon.sources import SimSource
from wristmon.pipeline import Pipeline

FAULT_KINDS = [k for k in REP_KINDS if k not in ("good", "electrode_off")]


@pytest.fixture(scope="module")
def acceptance(long_set_cfg, tmp_path_factory):
    kinds = ["good"] * 10 + FAULT_KINDS
    res = run_simulated_session(long_set_cfg, kinds, "raw", seed=0,
                                log_root=tmp_path_factory.mktemp("sessions"))
    return kinds, res


def test_calibration_wizard_completes(acceptance):
    _, res = acceptance
    assert all(r.passed for r in res.calibration.values())
    assert set(res.calibration) == {"sensor_check", "rest", "zero", "top", "rvc", "practice"}
    p = res.pipeline.profile
    assert p.ready and p.emg_mode == "raw" and 44 <= p.target_deg <= 45 and p.tempo


def test_ten_correct_reps_count_ten_with_single_beeps(acceptance):
    kinds, res = acceptance
    reps = res.counted
    assert len(reps) == len(kinds)
    assert [r.valid for r in reps[:10]] == [True] * 10
    assert res.valid_count == 10
    # 3 practice + 10 counted reps -> 13 single beeps before the first rejection.
    assert res.beeps[:13] == ["ok"] * 13 and res.beeps[13] == "bad"


def test_passive_lift_is_rejected_with_muscle_message(acceptance):
    kinds, res = acceptance
    r = res.counted[kinds.index("passive")]
    assert not r.valid and r.primary_fault == F.NO_MUSCLE and r.message == "Use your wrist muscle"


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_each_fault_rejected_with_correct_message(acceptance, kind):
    kinds, res = acceptance
    r = res.counted[kinds.index(kind)]
    expected = EXPECTED_FAULT[kind]
    assert not r.valid
    assert r.primary_fault == expected, r.faults
    assert r.message == F.MESSAGES[expected]


def test_invalid_reps_get_triple_beep(acceptance):
    kinds, res = acceptance
    assert res.beeps.count("bad") == len(FAULT_KINDS)
    sent = [c for _, c in res.pipeline.feedback.played]
    assert sent.count("bad") == len(FAULT_KINDS)


def test_fatigue_rises_over_valid_reps(acceptance):
    _, res = acceptance
    scores = [r.fatigue_score for r in res.counted if r.valid]
    assert scores[0] == 0.0 and scores[-1] > scores[3]
    assert all(r.features.get("mdf", 0) > 0 for r in res.counted if r.valid)


def test_replay_is_deterministic_and_matches_live(acceptance, long_set_cfg):
    _, res = acceptance
    a = replay_session(res.session_dir, long_set_cfg)
    b = replay_session(res.session_dir, long_set_cfg)
    key = lambda rr: [(r.valid, tuple(r.faults), round(r.peak_theta, 6)) for r in rr.counted]
    assert key(a) == key(b)
    assert [(r.valid, r.faults) for r in a.counted] == [(r.valid, r.faults) for r in res.counted]


def test_replay_recalibrate_reproduces_profile(acceptance, long_set_cfg):
    _, res = acceptance
    a = replay_session(res.session_dir, long_set_cfg, recalibrate=True)
    assert a.pipeline.profile.target_deg == pytest.approx(res.pipeline.profile.target_deg, abs=0.5)
    assert a.valid_count == res.valid_count


def test_envelope_only_mode(long_set_cfg):
    kinds = ["good"] * 5 + ["passive", "drop_fast"]
    res = run_simulated_session(long_set_cfg, kinds, "envelope", seed=1)
    assert res.pipeline.emg_mode == "envelope"          # auto-detected from the signal
    assert [r.valid for r in res.counted] == [True] * 5 + [False, False]
    assert res.counted[5].primary_fault == F.NO_MUSCLE
    assert all(r.features.get("mdf") != r.features.get("mdf") for r in res.counted)   # MDF disabled (nan)


def test_sustained_signal_loss_pauses_session(long_set_cfg):
    res = run_simulated_session(long_set_cfg, ["good", "electrode_off", "good"], "raw", seed=2)
    assert ("paused", "signal") in res.events
    assert res.pipeline.session.state == "paused"


def test_quick_recalibration_keeps_full_profile(long_set_cfg, acceptance):
    _, res = acceptance
    prof = res.pipeline.profile
    rvc, target = prof.rvc, prof.target_deg
    res2 = run_simulated_session(long_set_cfg, ["good"] * 2, "raw", seed=3, profile=prof, steps=QUICK_STEPS)
    assert res2.pipeline.profile.rvc == rvc and res2.pipeline.profile.target_deg == target
    assert res2.valid_count == 2


def test_latency_rep_end_to_beep(long_set_cfg):
    """Beep command must go out < 150 ms after the rep ends (device time and wall time)."""
    src = SimSource(seed=4, realtime=False)
    res = run_simulated_session(long_set_cfg, ["good", "drop_fast"], "raw", seed=4)
    pipe = res.pipeline
    for r, (t_beep, _) in zip(res.counted, pipe.feedback.played[3:]):
        assert 0 <= t_beep - r.t_end < 0.15
    # Wall-clock cost of processing a packet, including the rep evaluation frame.
    p2 = Pipeline(long_set_cfg, pipe.profile)
    p2.session_action("start_set")
    src.hint("exercise")
    src.queue_reps(["good"])
    src.open()
    worst = 0.0
    while src.patient.t < 12:
        for pkt in src.read():
            t0 = time.perf_counter()
            p2.handle(pkt)
            worst = max(worst, time.perf_counter() - t0)
    assert worst < 0.05


def test_pipeline_runs_faster_than_real_time(long_set_cfg, acceptance):
    """Throughput headroom: no packet backlog (loss) at 1 kHz EMG + 200 Hz IMU."""
    _, res = acceptance
    src = SimSource(seed=5, realtime=False)
    pipe = Pipeline(long_set_cfg, res.pipeline.profile)
    src.open()
    t0 = time.perf_counter()
    while src.patient.t < 11:
        for pkt in src.read():
            pipe.handle(pkt)
    elapsed = time.perf_counter() - t0
    assert elapsed < 10 * 0.5
    assert src.link_stats.lost == 0
