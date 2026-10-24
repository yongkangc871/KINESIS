"""Hand (with / without a weight) and leg exercises run through the same pipeline."""
import pytest

from wristmon import exercises as EX
from wristmon.calibration import CalibrationManager, Profile
from wristmon.config import deep_merge
from wristmon.headless import run_simulated_session

LONG_SET = {"exercise": {"reps_per_set": 1000, "max_attempts_per_set": 1000, "max_reps_per_session": 1000}}


def exercise_cfg(base, key):
    return deep_merge(EX.exercise_config(base, key), LONG_SET)


@pytest.mark.parametrize("key", list(EX.EXERCISES))
def test_full_calibration_and_good_reps(cfg, key):
    c = exercise_cfg(cfg, key)
    target = c["exercise"]["target_deg"]
    res = run_simulated_session(c, ["good"] * 3, seed=4, target_deg=target, rom_deg=target + 7,
                                tempo=c["exercise"]["tempo"])
    assert all(r.passed for r in res.calibration.values()), {s: r.message for s, r in res.calibration.items()}
    assert res.pipeline.profile.target_deg == pytest.approx(target, abs=1)
    assert [r.valid for r in res.counted] == [True, True, True], [r.faults for r in res.counted]


@pytest.mark.parametrize("key", ["knee_extension", "knee_lift"])
def test_leg_faults_use_leg_wording(cfg, key):
    c = exercise_cfg(cfg, key)
    target = c["exercise"]["target_deg"]
    res = run_simulated_session(c, ["drop_fast", "passive"], seed=5, target_deg=target, rom_deg=target + 7)
    msgs = c["faults"]["messages"]
    assert [r.primary_fault for r in res.counted] == ["DROP_FAST", "NO_MUSCLE"]
    assert [r.message for r in res.counted] == [msgs["DROP_FAST"], msgs["NO_MUSCLE"]]


@pytest.mark.parametrize("key", ["quad_set", "seated_knee_extension"])
def test_muscle_driven_hold_must_last(cfg, key):
    """Counted from the thigh muscle: a squeeze let go too soon is not counted."""
    c = exercise_cfg(cfg, key)
    target = c["exercise"]["target_deg"]
    res = run_simulated_session(c, ["good", "early_down"], seed=5, target_deg=target, rom_deg=target + 7,
                                tempo=c["exercise"]["tempo"])
    assert [r.primary_fault for r in res.counted] == ["", "EARLY_DOWN"], [r.faults for r in res.counted]
    assert res.counted[0].hold_s >= c["faults"]["min_hold_s"]


def test_routines_are_counted_in_reps(cfg):
    legs = EX.ROUTINES["legs"]
    assert len(legs.keys) == 4
    assert EX.ROUTINES["arms"].keys[0] == "bicep_curl" and EX.session_reps("bicep_curl")[0] == 20
    for key in EX.ROUTINES["arms"].keys + legs.keys:
        count, sets, per_set = EX.session_reps(key)
        c = EX.exercise_config(cfg, key)["exercise"]
        assert count == min(c["sets"], c["max_sets"]) * c["reps_per_set"] <= c["max_reps_per_session"]
    assert [EX.EXERCISES[k].limb for k in legs.keys] == ["legs"] * 4
    assert not [e for e in EX.EXERCISES.values() if e.limb == "legs" and e.key not in legs.keys]


def test_wrist_with_weight_is_the_unchanged_base_config(cfg):
    c = EX.exercise_config(cfg, "wrist_weight")
    c["exercise"].pop("key")
    assert c == cfg


def test_calibration_messages_follow_the_exercise(cfg):
    leg = CalibrationManager(EX.exercise_config(cfg, "knee_extension"))
    hand = CalibrationManager(cfg)
    assert "leg" in leg.texts["sensor"] and "hand" in hand.texts["sensor"]
    assert "knee" in leg.texts["top_more"] and "wrist" in hand.texts["top_more"]


def test_profiles_are_kept_per_exercise(tmp_path):
    p = Profile(user="Ann Lee", target_deg=60.0)
    EX.save_profile(tmp_path, p, "knee_extension")
    EX.save_profile(tmp_path, Profile(user="Ann Lee"), "wrist_weight")
    assert sorted(f.name for f in tmp_path.iterdir()) == ["Ann_Lee.json", "Ann_Lee@knee_extension.json"]
    assert EX.list_users(tmp_path) == ["Ann_Lee"]
    assert EX.load_profile(tmp_path, "Ann Lee", "knee_extension", 0.0).target_deg == 60.0
    assert not EX.load_profile(tmp_path, "Ann Lee", "knee_lift", 0.0).ready
