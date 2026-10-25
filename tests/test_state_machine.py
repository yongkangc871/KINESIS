"""State machine on scripted angle + EMG traces, one test per fault code."""
import numpy as np
import pytest

from wristmon import faults as F
from wristmon.datatypes import Frame
from wristmon.state_machine import RepLimits, RepStateMachine, State

FS = 200


def min_jerk(tau):
    tau = np.clip(tau, 0, 1)
    return tau ** 3 * (10 - 15 * tau + 6 * tau ** 2)


def trace(segments, start=0.0, settle=1.0):
    """segments: [(duration, theta_end)], min-jerk between points. Returns t, theta."""
    t_list, th_list = [np.arange(0, settle, 1 / FS)], [np.full(int(settle * FS), start)]
    t0, th0 = settle, start
    for dur, th1 in segments:
        tt = np.arange(0, dur, 1 / FS)
        t_list.append(t0 + tt)
        th_list.append(th0 + (th1 - th0) * min_jerk(tt / dur))
        t0, th0 = t0 + dur, th1
    t_list.append(t0 + np.arange(0, 1.5, 1 / FS))
    th_list.append(np.full(int(1.5 * FS), th0))
    t = np.concatenate(t_list)
    return t, np.concatenate(th_list)


GOOD = [(2.0, 48), (1.0, 48), (3.2, 0)]


def run(segments=GOOD, start=0.0, mods=None, limits=None, cfg_over=None, cfg=None, theta_add=None, settle=1.0):
    """mods: dict field -> function(t, theta) -> value array."""
    t, th = trace(segments, start, settle)
    if theta_add is not None:
        th = th + theta_add(t, th)
    om = np.gradient(th, t)
    mods = mods or {}
    n = len(t)
    cols = {
        "offplane": np.zeros(n), "roll_dev": np.zeros(n), "yaw_dev": np.zeros(n), "acc_resid": np.zeros(n),
        "env_pct": np.full(n, 90.0), "emg_active": np.ones(n, bool), "emg_onset_t": np.full(n, -5.0),
        "signal_ok": np.ones(n, bool), "imu_ok": np.ones(n, bool), "forearm_dev": np.full(n, np.nan),
    }
    for k, fn in mods.items():
        cols[k] = fn(t, th)
    sm_cfg, fc, fat = dict(cfg["state_machine"]), dict(cfg["faults"]), cfg["fatigue"]
    for k, v in (cfg_over or {}).items():
        (sm_cfg if k in sm_cfg else fc)[k] = v
    sm = RepStateMachine(sm_cfg, fc, fat, limits or RepLimits(target_deg=45, rom_deg=55))
    reps, states = [], []
    for i in range(n):
        f = Frame(t=float(t[i]), theta=float(th[i]), omega=float(om[i]),
                  **{k: (v[i].item() if hasattr(v[i], "item") else v[i]) for k, v in cols.items()})
        r = sm.update(f)
        states.append(sm.state)
        if r is not None:
            reps.append(r)
    return reps, states


def single(reps):
    assert len(reps) == 1, [r.faults for r in reps]
    return reps[0]


def window(t0, t1, value, default=0.0):
    return lambda t, th: np.where((t >= t0) & (t < t1), value, default)


def test_good_rep_counts(cfg):
    reps, states = run(cfg=cfg)
    r = single(reps)
    assert r.valid and r.faults == [] and r.message == "Good"
    assert 47 < r.peak_theta < 49 and r.hold_s > 0.9
    assert {State.READY, State.LIFTING, State.TOP_HOLD, State.LOWERING, State.BOTTOM} <= set(states)
    assert np.isfinite(r.window[0]) and r.window[1] > r.window[0]


def test_rep_started_right_after_reset_still_counts(cfg):
    """Practice / a new set resets the counter; a lift begun from the start position before
    the 0.3 s still time is up must not be lost (it left practice stuck at 2 of 3)."""
    r = single(run(cfg=cfg, settle=0.1)[0])
    assert r.valid, r.faults


def test_incomplete_rom(cfg):
    r = single(run([(2.0, 30), (1.0, 30), (3.0, 0)], cfg=cfg)[0])
    assert r.primary_fault == F.INCOMPLETE_ROM and r.message == "Lift a little higher"


def test_early_down(cfg):
    r = single(run([(1.5, 48), (3.2, 0)], cfg=cfg)[0])
    assert r.faults == [F.EARLY_DOWN] and r.message == "Pause at the top"


def test_no_muscle_when_angle_reached_without_emg(cfg):
    mods = {"env_pct": lambda t, th: np.full(len(t), 3.0),
            "emg_active": lambda t, th: np.zeros(len(t), bool),
            "emg_onset_t": lambda t, th: np.full(len(t), np.nan)}
    r = single(run(mods=mods, cfg=cfg)[0])
    assert r.primary_fault == F.NO_MUSCLE and r.message == "Use your wrist muscle" and not r.valid


def test_no_muscle_when_onset_too_late(cfg):
    # Movement starts at t=1.0; EMG only switches on at 1.6 s.
    mods = {"emg_active": lambda t, th: t >= 1.6,
            "emg_onset_t": lambda t, th: np.where(t >= 1.6, 1.6, np.nan)}
    r = single(run(mods=mods, cfg=cfg)[0])
    assert r.primary_fault == F.NO_MUSCLE and r.onset_lag_s > 0.1


def test_forearm_lift_from_offplane_gyro(cfg):
    r = single(run(mods={"offplane": window(1.8, 2.1, 90.0)}, cfg=cfg)[0])
    assert r.primary_fault == F.FOREARM_LIFT and r.message == "Keep your arm on the table"


def test_forearm_lift_from_accel_transient(cfg):
    r = single(run(mods={"acc_resid": window(1.8, 2.0, 0.3)}, cfg=cfg)[0])
    assert r.primary_fault == F.FOREARM_LIFT


def test_forearm_lift_from_second_imu(cfg):
    lim = RepLimits(target_deg=45, rom_deg=55, forearm_imu=True)
    r = single(run(mods={"forearm_dev": window(1.5, 3.0, 14.0, 0.0)}, limits=lim, cfg=cfg)[0])
    assert r.primary_fault == F.FOREARM_LIFT


def test_short_accel_spike_is_ignored(cfg):
    r = single(run(mods={"acc_resid": window(1.8, 1.815, 0.5)}, cfg=cfg)[0])
    assert r.valid


def test_drop_fast(cfg):
    r = single(run([(2.0, 48), (1.0, 48), (0.6, 0)], cfg=cfg)[0])
    assert r.primary_fault == F.DROP_FAST and r.message == "Lower slowly"


def test_swing_fast(cfg):
    r = single(run([(0.45, 48), (1.0, 48), (3.2, 0)], cfg=cfg)[0])
    assert r.primary_fault == F.SWING_FAST and r.message == "Lift smoothly"


def test_wrist_twist(cfg):
    r = single(run(mods={"roll_dev": window(3.0, 4.0, 25.0)}, cfg=cfg)[0])
    assert r.primary_fault == F.WRIST_TWIST and r.message == "Keep your palm facing up"


def test_side_bend(cfg):
    r = single(run(mods={"yaw_dev": window(3.0, 4.0, 18.0)}, cfg=cfg)[0])
    assert r.primary_fault == F.SIDE_BEND and r.message == "Keep your hand straight"


def test_yaw_drift_relative_to_rep_start_is_ignored(cfg):
    # 30 deg of slow heading drift since calibration, but no deviation within the rep.
    r = single(run(mods={"yaw_dev": lambda t, th: np.full(len(t), 30.0)}, cfg=cfg)[0])
    assert r.valid


def test_over_flex(cfg):
    r = single(run([(2.0, 75), (1.0, 75), (3.5, 0)], cfg=cfg)[0])
    assert r.primary_fault == F.OVER_FLEX and r.message == "Not so far"


def test_not_returned(cfg):
    reps, _ = run([(2.0, 48), (1.0, 48), (2.5, 25), (2.0, 48), (1.0, 48), (3.2, 0)], cfg=cfg)
    assert len(reps) == 2
    assert reps[0].primary_fault == F.NOT_RETURNED and reps[0].message == "Lower all the way down"
    assert reps[1].valid   # the next rep, started from the trough, is judged on its own


def test_jerky(cfg):
    tremor = lambda t, th: np.where((t > 1.0) & (t < 3.0) | (t > 4.0) & (t < 7.2),
                                    2.0 * np.sin(2 * np.pi * 3.0 * t), 0.0)
    r = single(run(theta_add=tremor, cfg=cfg)[0])
    assert r.primary_fault == F.JERKY and r.message == "Move steadily"


def test_signal_poor_emg(cfg):
    r = single(run(mods={"signal_ok": lambda t, th: ~((t > 2.0) & (t < 2.2))}, cfg=cfg)[0])
    assert r.primary_fault == F.SIGNAL_POOR and r.message == "Check sensor contact"


def test_signal_poor_imu_dropout(cfg):
    r = single(run(mods={"imu_ok": lambda t, th: ~((t > 2.0) & (t < 2.01))}, cfg=cfg)[0])
    assert r.primary_fault == F.SIGNAL_POOR


def test_timeout(cfg):
    reps, states = run([(2.0, 48), (21.0, 48), (3.2, 0)], cfg=cfg)   # past max_rep_s (20 s)
    r = reps[0]
    assert F.TIMEOUT in r.faults and r.message == "Try again"
    assert len(reps) == 1   # the late lowering is not judged as a second rep


def test_twitch_is_not_an_attempt(cfg):
    reps, _ = run([(0.8, 15), (0.8, 0)], cfg=cfg)
    assert reps == []


def test_noise_around_threshold_creates_no_reps(cfg):
    jitter = lambda t, th: 12.0 + 1.5 * np.sin(2 * np.pi * 2 * t)
    reps, _ = run([(1.0, 0)], theta_add=lambda t, th: np.where(t > 2.5, jitter(t, th), 0.0), cfg=cfg)
    assert reps == []


def test_multiple_faults_logged_primary_by_priority(cfg):
    r = single(run([(0.45, 30), (2.0, 30), (0.6, 0)], mods={"roll_dev": window(1.2, 2.0, 30.0)}, cfg=cfg)[0])
    assert {F.WRIST_TWIST, F.INCOMPLETE_ROM, F.EARLY_DOWN, F.DROP_FAST} <= set(r.faults)
    assert r.primary_fault == F.WRIST_TWIST


def test_priority_is_configurable(cfg):
    prio = [F.DROP_FAST] + [c for c in cfg["faults"]["priority"] if c != F.DROP_FAST]
    r = single(run([(0.45, 30), (2.0, 30), (0.6, 0)], cfg=cfg, cfg_over={"priority": prio})[0])
    assert r.primary_fault == F.DROP_FAST


def test_start_from_extension(cfg):
    lim = RepLimits(target_deg=45, rom_deg=55, start_angle=-15)
    r = single(run([(2.5, 48), (1.0, 48), (3.5, -15)], start=-15, limits=lim, cfg=cfg)[0])
    assert r.valid


@pytest.mark.parametrize("code", F.ALL_FAULTS)
def test_every_fault_has_message_and_icon(code):
    assert F.MESSAGES[code] and F.ICONS[code]
