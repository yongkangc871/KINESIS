"""GUI smoke test (offscreen): exercise screen with the virtual patient.

Checks counted reps reach the screen, the glow fires, and the UI keeps >= 30 FPS.
"""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from wristmon.headless import run_simulated_session  # noqa: E402
from wristmon.sources import SimSource  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    from wristmon.gui import theme
    a.setStyleSheet(theme.stylesheet())
    return a


def pump(app, seconds, until=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.003)
        if until and until():
            return True
    return False


def test_exercise_screen_counts_and_fps(app, long_set_cfg, tmp_path):
    from wristmon.gui.main_window import MainWindow
    profile = run_simulated_session(long_set_cfg, ["good"], seed=7).pipeline.profile
    cfg = dict(long_set_cfg, logging=dict(long_set_cfg["logging"], enabled=False,
                                          profiles_dir=str(tmp_path / "profiles")))
    win = MainWindow(cfg, SimSource(seed=7, realtime=True), "sim", auto_reps=False)
    win.resize(1600, 1000)
    win.show()
    try:
        win.profile = profile
        win.worker.call("begin_session", profile, False)
        win._on_wizard_done()
        assert pump(app, 5, lambda: win.stack.currentWidget() is win.exercise)
        pump(app, 2)
        assert win.exercise.arm.demo            # idle before the first rep -> demonstration
        win.worker.call("queue_reps", ["good", "passive"])
        assert pump(app, 25, lambda: len(win.reps) >= 2)
        assert [r.valid for r in win.reps] == [True, False]
        assert win.exercise.count.text().startswith("1 /")
        assert win.exercise.banner.text.text() == "Use your wrist muscle"
        assert win.glow.level > 0 or win.glow.anim.state() is not None
        assert win.fps >= 30, win.fps
    finally:
        win.close()
        pump(app, 0.5)


def test_arm_routine_runs_its_exercises_in_turn_by_rep_count(app, long_set_cfg, tmp_path):
    from wristmon import exercises as EX
    from wristmon.gui.main_window import MainWindow
    from wristmon.gui.widgets import CurlView
    cfg = dict(long_set_cfg, logging=dict(long_set_cfg["logging"], enabled=False,
                                          profiles_dir=str(tmp_path / "profiles")))
    win = MainWindow(cfg, SimSource(seed=7, realtime=True), "sim")
    win.resize(1600, 1000)
    win.show()
    assert EX.ROUTINES["arms"].keys == ["bicep_curl", "scapular_retraction", "pendulum"]
    assert EX.session_reps("bicep_curl") == (20, 2, 10)
    try:
        win.start_page.user.setEditText("Ann")
        win.start_page.go.click()
        win.limb_page.cards["arms"].clicked.emit()
        assert win.stack.currentWidget() is win.routine_pages["arms"]
        win.routine_pages["arms"].start.emit()
        assert win.exercise_key == "bicep_curl" and win.stack.currentWidget() is win.load_page
        assert isinstance(win.exercise.arm, CurlView)
        assert win.load_page.min_kg == 0.0           # assisted curls allowed
        assert pump(app, 2, lambda: "automatically" in win.load_page.auto_note.text())
        win._on_wizard_done()      # skip calibration: straight into the first set
        assert pump(app, 5, lambda: win.stack.currentWidget() is win.exercise)
        assert pump(app, 2, lambda: "0 of 20 reps" in win.exercise.routine_clock.text())
        win._end_session()                           # e.g. End for today
        assert pump(app, 5, lambda: win.stack.currentWidget() is win.summary)
        assert win.summary.fin.text().startswith("Next: Shoulder blade squeezes")
        assert win.summary.auto.active               # hands-free: goes on by itself
        assert pump(app, 2, lambda: "(" in win.summary.fin.text())     # "Next: ... (9)"
        win.summary.done.emit()
        assert win.exercise_key == "scapular_retraction" and win.stack.currentWidget() is win.load_page
        win.load_page.skip.emit()                    # Skip exercise before any set: straight on
        assert win.exercise_key == "pendulum" and win.stack.currentWidget() is win.load_page
        win.load_page.confirmed.emit(0.0, False)
        assert win.stack.currentWidget() is win.wizard
        assert pump(app, 2, lambda: "Starting in" in win.wizard.countdown.text())   # steps start by themselves
        win.wizard.skip.emit()                       # skipping the last exercise ends the routine
        assert win.routine is None and win.stack.currentWidget() is win.start_page
    finally:
        win.close()
        pump(app, 0.5)


def test_leg_routine_starts_with_quad_sets_counted_from_the_muscle(app, long_set_cfg, tmp_path):
    from wristmon import exercises as EX
    from wristmon.gui.main_window import MainWindow
    from wristmon.gui.screens import Photo
    from wristmon.gui.widgets import LegPlacementDiagram, QuadSetView
    key = "quad_set"
    c = EX.exercise_config(long_set_cfg, key)
    target = c["exercise"]["target_deg"]
    assert EX.ROUTINES["legs"].keys[0] == key
    # A returning user, so the quick calibration (no strongest-squeeze test) is used.
    profile = run_simulated_session(c, ["good"], seed=7, target_deg=target, rom_deg=target + 7,
                                    tempo=c["exercise"]["tempo"]).pipeline.profile
    profile.user = "Ann"
    EX.save_profile(tmp_path / "profiles", profile, key)
    cfg = dict(long_set_cfg, logging=dict(long_set_cfg["logging"], enabled=False,
                                          profiles_dir=str(tmp_path / "profiles")))
    win = MainWindow(cfg, SimSource(seed=7, realtime=True), "sim")
    win.resize(1600, 1000)
    win.show()
    try:
        win.start_page.user.setEditText("Ann")
        win.start_page.go.click()
        assert win.stack.currentWidget() is win.limb_page
        win.limb_page.cards["legs"].clicked.emit()
        assert win.stack.currentWidget() is win.routine_pages["legs"]
        win.routine_pages["legs"].start.emit()
        assert win.exercise_key == key
        assert win.stack.currentWidget() is win.load_page and win.load_page.full_btn.isVisible()
        assert isinstance(win.exercise.arm, QuadSetView)
        assert isinstance(win.wizard.diagram, (Photo, LegPlacementDiagram))   # photo when images/ has it
        assert win.exercise.arm.muscle_label == "Thigh muscle"
        win.load_page.confirmed.emit(0.0, False)
        assert win.wizard.steps == ["sensor_check", "rest", "zero", "practice"]
        win.load_page.confirmed.emit(0.0, True)        # full calibration: no half-way hold step
        assert win.wizard.steps == ["sensor_check", "rest", "zero", "top", "practice"]
        win._on_wizard_done()      # skip the wizard; the virtual patient squeezes on its own
        assert pump(app, 40, lambda: len(win.reps) >= 2)
        assert all(r.valid for r in win.reps), [r.faults for r in win.reps]
        assert win.reps[0].target_deg == pytest.approx(target, abs=1)
        assert win.exercise.arm.min_activation == pytest.approx(target, abs=1)   # bar marks the target
        assert win.exercise.count.text()[0] in "23"
        win.exercise.skip.emit()          # mid-set: the set is logged, then straight to the next one
        assert pump(app, 5, lambda: win.exercise_key == "straight_leg_raise")
        assert win.stack.currentWidget() is win.load_page
    finally:
        win.close()
        pump(app, 0.5)
