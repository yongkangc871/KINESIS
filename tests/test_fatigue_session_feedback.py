import json
import math

import pytest

from wristmon.calibration import Profile, apply_result, StepResult
from wristmon.datatypes import RepRecord
from wristmon.fatigue import (FatigueScorer, LinearFatigueModel, RuleFatigueModel, jasa_label,
                              load_fatigue_model, zone_for)
from wristmon.feedback import FeedbackController
from wristmon.session import SessionManager


def rep(i, mdf=100.0, finsm5=1.0, rms=100.0, lift=1.5, peak=48.0, sparc=-1.6, valid=True):
    r = RepRecord(index=i, valid=valid, lift_s=lift, peak_theta=peak, sparc=sparc)
    r.features = {"mdf": mdf, "mnf": mdf * 1.1, "finsm5": finsm5, "rms_pct": rms}
    return r


# ------------------------------------------------------------------ fatigue

def test_baseline_then_score_rises_with_fatigue(cfg):
    s = FatigueScorer(cfg["fatigue"], "raw")
    for i in range(3):
        assert s.update(rep(i))[0] == 0.0
    scores = [s.update(rep(3 + k, mdf=100 - 2 * k, finsm5=1 + 0.1 * k, rms=100 + 3 * k, lift=1.5 + 0.05 * k))[0]
              for k in range(12)]
    assert all(b >= a - 1e-9 for a, b in zip(scores, scores[1:]))
    assert scores[-1] > 70 and zone_for(scores[-1], cfg["fatigue"]) == "red"


def test_rule_weights_and_clipping(cfg):
    m = RuleFatigueModel(cfg["fatigue"], "raw")
    full = cfg["fatigue"]["full_scale_pct"]
    assert m.predict({"mdf_drop": full["mdf_drop"]}) == pytest.approx(50.0)     # weight 0.5 of total 1.0
    assert m.predict({"mdf_drop": 999, "finsm5_rise": 999, "rms_rise": 999,
                      "lift_time_rise": 999}) == pytest.approx(100.0)
    assert m.predict({"mdf_drop": -50}) == 0.0


def test_ewma_smoothing(cfg):
    s = FatigueScorer(cfg["fatigue"], "raw")
    for i in range(3):
        s.update(rep(i))
    sc, *_ = s.update(rep(3, mdf=80, finsm5=2, rms=140, lift=2.25))   # raw score 100
    assert sc == pytest.approx(30.0)                                  # alpha 0.3 from 0


def test_jasa_effort_increase_does_not_count_rms(cfg):
    s = FatigueScorer(cfg["fatigue"], "raw")
    for i in range(3):
        s.update(rep(i))
    _, _, jasa, _ = s.update(rep(3, mdf=110, rms=150))
    assert jasa == "force increase" and s.ewma == 0.0
    assert jasa_label(20, -20, 5) == "fatigue" and jasa_label(-20, 20, 5) == "recovery"


def test_envelope_mode_uses_tempo_angle_smoothness(cfg):
    s = FatigueScorer(cfg["fatigue"], "envelope")
    for i in range(3):
        s.update(rep(i, mdf=math.nan, finsm5=math.nan))
    sc, *_ = s.update(rep(3, mdf=math.nan, finsm5=math.nan, rms=140, lift=2.25, peak=40, sparc=-2.1))
    assert sc > 25


def test_swappable_json_model(tmp_path, cfg):
    p = tmp_path / "model.json"
    p.write_text(json.dumps({"features": ["mdf_drop"], "weights": [0.02], "bias": 0.0, "scale": 100}))
    fcfg = dict(cfg["fatigue"], model=f"json:{p}")
    m = load_fatigue_model(fcfg, "raw")
    assert isinstance(m, LinearFatigueModel) and m.predict({"mdf_drop": 25}) == pytest.approx(50.0)
    s = FatigueScorer(cfg["fatigue"], "raw", model=m)
    for i in range(3):
        s.update(rep(i))
    assert s.update(rep(3, mdf=75))[0] == pytest.approx(0.3 * 50.0)


# ------------------------------------------------------------------ session / safety

def test_set_completion_rest_and_caps(cfg):
    ex = dict(cfg["exercise"], reps_per_set=2, sets=2)
    s = SessionManager(ex)
    assert s.start_set()
    assert s.record(rep(0)) == ["counted"]
    assert s.record(rep(0, valid=False)) == ["rejected"]
    assert s.record(rep(0)) == ["counted", "set_complete"] and s.state == "rest"
    assert s.start_set() and s.set_no == 2
    s.record(rep(0))
    assert "session_complete" in s.record(rep(0)) and s.state == "finished"
    assert not s.start_set()
    summ = s.summary()
    assert summ["valid"] == 4 and summ["invalid"] == 1


def test_attempt_cap_ends_set(cfg):
    ex = dict(cfg["exercise"], max_attempts_per_set=3)
    s = SessionManager(ex)
    s.start_set()
    for _ in range(2):
        s.record(rep(0, valid=False))
    assert "set_complete" in s.record(rep(0, valid=False))


def test_force_rest_and_pause_resume(cfg):
    s = SessionManager(cfg["exercise"])
    s.start_set()
    assert s.force_rest() == ["forced_rest"] and s.rest_reason == "fatigue"
    s.start_set()
    s.pause("signal")
    assert s.state == "paused" and not s.counting
    assert s.record(rep(0)) == []          # nothing is counted while paused
    s.resume()
    assert s.state == "exercise"


# ------------------------------------------------------------------ feedback

def test_beeps_never_overlap_and_are_immediate_when_free(cfg):
    sent = []
    fb = FeedbackController(cfg["feedback"], sent.append)
    fb.request("bad", 10.0)
    assert sent == ["BEEP_BAD"]                    # immediate: no added latency
    fb.request("ok", 10.1)                          # still playing the double beep
    assert sent == ["BEEP_BAD"]
    fb.tick(10.3)
    assert sent == ["BEEP_BAD"]
    fb.tick(10.6)
    assert sent == ["BEEP_BAD", "BEEP_OK"]
    (t1, k1), (t2, k2) = fb.played
    assert t2 >= t1 + cfg["feedback"]["pattern_s"][k1] + cfg["feedback"]["beep_gap_s"]


def test_buzzer_can_be_disabled(cfg):
    sent, heard = [], []
    fb = FeedbackController(dict(cfg["feedback"], buzzer=False), sent.append)
    fb.listeners.append(heard.append)
    fb.request("ok", 0.0)
    assert sent == [] and heard == ["ok"]


# ------------------------------------------------------------------ profile

def test_profile_roundtrip(tmp_path):
    p = Profile(user="Mdm Tan", rvc=123.4, gravity_signs=[1, -1, 1], tempo={"lift_s": 2.0})
    path = p.save(tmp_path)
    q = Profile.load(path)
    assert q.to_json() == p.to_json() and q.rvc == 123.4 and q.user == "Mdm Tan"
    assert math.isnan(q.rom_deg) and q.gravity_signs == [1, -1, 1]


def test_failed_step_does_not_change_profile():
    p = Profile(user="a", rvc=50.0)
    apply_result(p, StepResult("rvc", False, "no", {"rvc": 999.0}))
    assert p.rvc == 50.0
