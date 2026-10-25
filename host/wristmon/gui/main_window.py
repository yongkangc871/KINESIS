"""Main window: session flow, worker wiring, status bar, edge glow, audio, emergency stop.

Flow: select user -> arms or legs -> that limb's routine. Each exercise of the routine runs
get ready -> calibration (full first time, quick after) -> sets with rests until its count of
good reps is done -> summary, and its summary leads to the next one.
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QSlider, QStackedWidget, QVBoxLayout, QWidget

from .. import exercises as EX
from ..calibration import FULL_STEPS, QUICK_STEPS, Profile, steps_for
from ..config import resolve_dir
from ..report import write_report
from . import theme as T
from .audio import HostAudio, Speaker
from .branding import APP_NAME, Backdrop
from .icons import icon_pixmap
from .screens import (ExercisePage, LoadPage, PausePage, RestPage, RoutinePage, StartPage, SummaryPage,
                      WizardPage, limb_page)
from .widgets import GlowOverlay, PacerClock
from .worker import PipelineWorker


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict, source, mode: str = "live", profile: Profile | None = None,
                 auto_reps: bool | None = None):
        super().__init__()
        QApplication.instance().setFont(T.app_font())   # Segoe UI base font, however we were launched
        # cfg may already be an exercise config (replay of a logged session); otherwise it is
        # the wrist curl with a weight, and other exercises are derived from it on demand.
        self.base_cfg = cfg
        self.exercise_key = cfg["exercise"].get("key", EX.DEFAULT)
        self.cfg = cfg if "key" in cfg["exercise"] else EX.exercise_config(cfg, self.exercise_key)
        self.mode = mode                       # live | sim | replay
        self.profiles_dir = resolve_dir(cfg, "profiles_dir")
        self.profile = profile
        self.user_name = profile.user if profile else ""
        self.reps = []
        self.trace: list[tuple] = []           # (t, theta, env_pct) for every frame of the sets
        self.shown_set = 0
        self.page_before_pause = None
        self.routine: EX.Routine | None = None     # the routine while it runs
        self.routine_idx = 0
        self._skipping = False                     # Skip exercise: go on without the summary
        self.session_dir: str | None = None        # the logger's folder; the report goes there
        self.setWindowTitle(APP_NAME)

        # Pipeline runs in its own thread.
        self.thread = QThread(self)
        self.worker = PipelineWorker(self.cfg, source, profile, replay=(mode == "replay"))
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        w = self.worker
        w.frames.connect(self._on_frames)
        w.rep.connect(self._on_rep)
        w.pipeline_event.connect(self._on_event)
        w.calibration.connect(self._on_calibration)
        w.profile.connect(self._on_profile)
        w.status.connect(self._on_status)
        w.beep.connect(self._on_beep)
        w.error.connect(self._on_error)
        w.replay_finished.connect(lambda: self.status_labels["conn"].setText("Replay finished"))

        # Pages: the start and choice pages are fixed; the rest depend on the exercise.
        self.stack = QStackedWidget()
        self.start_page = StartPage(EX.list_users(self.profiles_dir))
        self.limb_page = limb_page()
        self.routine_pages = {key: RoutinePage(r) for key, r in EX.ROUTINES.items()}
        for page in (self.start_page, self.limb_page, *self.routine_pages.values()):
            self.stack.addWidget(page)
        self.start_page.start.connect(self._on_start)
        self.limb_page.chosen.connect(self._on_limb)
        self.limb_page.back.connect(lambda: self.stack.setCurrentWidget(self.start_page))
        for key, page in self.routine_pages.items():
            page.start.connect(lambda k=key: self._start_routine(k))
            page.back.connect(lambda: self.stack.setCurrentWidget(self.limb_page))
        self._build_exercise_pages()

        # A page's minimum size must never make the window grow past the screen (that hides the
        # buttons and status bar); pages are laid out to fit instead.
        self.stack.setMinimumSize(1, 1)
        central = Backdrop()        # shared background; every page is transparent over it
        self.backdrop = central
        self.stack.currentChanged.connect(
            lambda _: central.set_quiet(self.stack.currentWidget() in (self.wizard, self.exercise, self.summary)))
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)
        self.setCentralWidget(central)
        self.glow = GlowOverlay(central)

        self._build_status_bar()
        # In sim mode the virtual patient does good reps by itself while a set is running
        # (tests turn this off to script the reps themselves).
        self.auto_reps = mode == "sim" if auto_reps is None else auto_reps
        self.in_set = False

        fb = cfg["feedback"]
        self.audio = HostAudio(fb["volume"], enabled=fb["pc_sound"])
        self.speaker = Speaker(fb["tts"])

        # Spacebar = emergency stop anywhere (buttons take no focus, so space never clicks them).
        for key in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(self.emergency_stop)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)

        self.fps_frames = 0
        self.fps_t0 = time.monotonic()
        self.fps = 0.0
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._tick)
        self.ui_timer.start(int(1000 / cfg["gui"]["fps"]))

        self.thread.start()
        if mode == "replay":
            self.exercise.reset_for_set(1, self.sets, profile.target_deg, profile.load_kg)
            self.stack.setCurrentWidget(self.exercise)

    # ------------------------------------------------------------ chrome
    def _build_status_bar(self):
        sb = self.statusBar()
        self.status_labels = {}
        for key in ("conn", "signal", "emg", "calib", "battery", "fps"):
            lab = QLabel("")
            self.status_labels[key] = lab
            sb.addWidget(lab)
        vol = QSlider(Qt.Orientation.Horizontal)
        vol.setRange(0, 100)
        vol.setValue(int(self.cfg["feedback"]["volume"] * 100))
        vol.setFixedWidth(160)
        vol.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        vol.valueChanged.connect(lambda v: self.audio.set_volume(v / 100))
        vol_icon = QLabel()
        vol_icon.setPixmap(icon_pixmap("volume", 18, T.MUTED))
        sb.addPermanentWidget(vol_icon)
        sb.addPermanentWidget(QLabel("Volume"))
        sb.addPermanentWidget(vol)

    def _build_exercise_pages(self):
        """(Re)creates every page that depends on the current exercise."""
        for name in ("load_page", "wizard", "exercise", "rest", "pause_page", "summary"):
            old = getattr(self, name, None)
            if old is not None:
                self.stack.removeWidget(old)
                old.deleteLater()
        ex = EX.get(self.exercise_key)
        exc = self.cfg["exercise"]
        self.sets = min(exc["sets"], exc["max_sets"])
        self.clock = PacerClock(exc["tempo"], exc["target_deg"], EX.captions(ex))
        self.load_page = LoadPage(self.cfg, ex)
        self.wizard = WizardPage(self.cfg, self.worker, self.clock, sim=(self.mode == "sim"), ex=ex)
        self.exercise = ExercisePage(self.cfg, self.clock, ex)
        self.rest = RestPage(ex)
        self.pause_page = PausePage(ex)
        self.summary = SummaryPage(self.cfg)
        for page in (self.load_page, self.wizard, self.exercise, self.rest, self.pause_page, self.summary):
            self.stack.addWidget(page)
        self.load_page.confirmed.connect(self._on_load)
        self.load_page.back.connect(self._load_back)
        for page in (self.load_page, self.wizard, self.exercise, self.rest, self.pause_page):
            page.skip.connect(self._skip_exercise)
        for page in (self.wizard, self.exercise, self.rest, self.pause_page):
            page.results.connect(self._skip_to_results)
        self.wizard.finished.connect(self._on_wizard_done)
        self.wizard.back.connect(lambda: self.stack.setCurrentWidget(self.load_page))
        self.exercise.stop_requested.connect(self.emergency_stop)
        # Back in the middle of a session asks first: continue, or end and see the summary.
        self.exercise.back.connect(lambda: self.emergency_stop("back"))
        self.rest.next_set.connect(self._next_set)
        self.rest.end_session.connect(self._end_session)
        self.rest.back.connect(lambda: self.emergency_stop("back"))
        self.pause_page.resume.connect(self._resume)
        self.pause_page.end_session.connect(self._end_session)
        self.pause_page.back.connect(self._resume)
        self.summary.done.connect(self._finish)
        self.summary.back.connect(self._summary_back)

    def _set_exercise(self, key: str):
        if key == self.exercise_key:
            return
        self.exercise_key = key
        self.cfg = EX.exercise_config(self.base_cfg, key)
        self.worker.call("set_config", copy.deepcopy(self.cfg))
        self._build_exercise_pages()

    def _apply_sim_auto(self):
        """Auto reps only while a set is running, never during practice, rest or a pause."""
        if self.auto_reps:
            self.worker.call("set_auto", "good" if self.in_set else None)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.glow.setGeometry(self.centralWidget().rect())

    def _toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def closeEvent(self, ev):
        self.worker.call("stop")
        self.thread.quit()
        self.thread.wait(3000)
        super().closeEvent(ev)

    # ------------------------------------------------------------ flow
    def _on_start(self, name: str):
        self.user_name = name
        self.stack.setCurrentWidget(self.limb_page)

    def _on_limb(self, limb: str):
        self.stack.setCurrentWidget(self.routine_pages[limb])

    def _start_routine(self, key: str):
        self.routine = EX.ROUTINES[key]
        self.routine_idx = 0
        self._begin_exercise(self.routine.keys[0])

    def _stop_routine(self):
        self.routine = None
        self.exercise.set_routine_clock("")

    def _on_exercise(self, key: str):
        """A single exercise outside any routine (kept for tools and tests)."""
        self._stop_routine()
        self._begin_exercise(key)

    def _load_back(self):
        if self.routine is not None:
            page = self.routine_pages[self.routine.key]
            self._stop_routine()
            self.stack.setCurrentWidget(page)
        else:
            self.stack.setCurrentWidget(self.limb_page)

    def _summary_back(self):
        self._stop_routine()
        self.stack.setCurrentWidget(self.limb_page)

    def _tick_routine(self):
        """Routine progress on the exercise screen: which exercise, and good reps of the count."""
        count = EX.session_reps(self.exercise_key)[0]
        done = sum(r.valid for r in self.reps)
        text = (f"Exercise {self.routine_idx + 1} of {len(self.routine.keys)} · "
                f"{min(done, count)} of {count} reps done")
        if text != self.exercise.routine_clock.text():
            self.exercise.set_routine_clock(text)

    def _begin_exercise(self, key: str):
        self._set_exercise(key)
        ex = EX.get(key)
        self.profile = EX.load_profile(self.profiles_dir, self.user_name, key, self.cfg["exercise"]["load_kg"])
        if not ex.uses_weight:
            self.profile.load_kg = 0.0
        self.reps = []
        self.trace = []
        self.shown_set = 0
        self.session_dir = None             # the worker reports the new folder ("logging")
        self.worker.call("begin_session", copy.deepcopy(self.profile), True)
        self.load_page.set_kg(self.profile.load_kg)
        self.load_page.set_returning(self.profile.ready)
        self.stack.setCurrentWidget(self.load_page)

    def _on_load(self, kg: float, full: bool = False):
        self.profile.load_kg = kg
        self.worker.call("set_load", kg)
        quick = self.profile.ready and not full
        steps = steps_for(self.cfg, QUICK_STEPS if quick else FULL_STEPS)
        target = self.profile.target_deg if quick else self.cfg["exercise"]["target_deg"]
        self.wizard.begin(steps, target, kg)
        self.stack.setCurrentWidget(self.wizard)

    def _on_wizard_done(self):
        self._save_profile()
        self.worker.call("session_action", "start_set")
        self.worker.call("hint", "exercise")

    def _next_set(self):
        self.worker.call("session_action", "start_set")
        self.worker.call("hint", "exercise")

    def _end_session(self):
        self.worker.call("session_action", "finish")

    def _skip_to_results(self):
        """Demo: end this exercise now and show its summary and report, however many reps
        (even none) were counted. Inside a routine the summary still offers the next exercise."""
        self._skipping = False
        self.worker.call("cancel_capture")
        self._end_session()

    def _write_report(self, summary: dict):
        root = Path(self.session_dir) if self.session_dir else resolve_dir(self.cfg, "dir") / "reports"
        name = "report.html" if self.session_dir else f"report_{time.strftime('%Y%m%d_%H%M%S')}.html"
        emg_mode = self.profile.emg_mode if self.profile else self.cfg["emg"]["mode"]
        try:
            path = write_report(root / name, summary, self.reps, self.cfg, self.user_name or "guest",
                                EX.get(self.exercise_key).title, emg_mode, self.profile, self.trace,
                                self.session_dir)
        except Exception as e:      # the summary must still show if the report cannot be written
            self._on_error(f"report: {e}")
            path = None
        self.summary.set_report(path)

    def _resume(self):
        self.worker.call("session_action", "resume")
        back = self.page_before_pause or self.exercise
        if back is self.wizard and self.wizard.phase != "practice":
            self.wizard.restart_step()     # an interrupted capture is simply redone
        self.stack.setCurrentWidget(back)

    def _advance_routine(self):
        self.routine_idx += 1
        self._begin_exercise(self.routine.keys[self.routine_idx])

    def _skip_exercise(self):
        """Skip exercise: straight to the next exercise of the routine, without its summary.
        A session already under way is finished first, so its reps are still logged; skipping
        the last exercise ends the routine (with the summary if any set was done)."""
        if self.routine is None:
            self._end_session()
            return
        self.worker.call("cancel_capture")
        last = self.routine_idx + 1 >= len(self.routine.keys)
        if self.shown_set > 0:                    # sets started: finish them properly first
            self._skipping = not last
            self._end_session()
        elif last:
            self._finish()
        else:
            self._advance_routine()

    def _finish(self):
        if self.routine is not None and self.routine_idx + 1 < len(self.routine.keys):
            self._advance_routine()
            return
        self._stop_routine()
        self.start_page.set_users(EX.list_users(self.profiles_dir))
        self.stack.setCurrentWidget(self.start_page)

    def emergency_stop(self, reason: str = "emergency"):
        cur = self.stack.currentWidget()
        if cur in (self.exercise, self.rest, self.wizard):
            self.page_before_pause = cur
            self.worker.call("cancel_capture")
            self.worker.call("pause", reason)
            self.pause_page.show_reason(reason)
            self.stack.setCurrentWidget(self.pause_page)

    def _save_profile(self):
        if self.profile is not None and self.mode != "replay":
            EX.save_profile(self.profiles_dir, self.profile, self.exercise_key)

    # ------------------------------------------------------------ worker signals
    def _on_frames(self, frames):
        if self.shown_set > 0:          # from the first set on: the whole-session graph
            self.trace.extend((f.t, f.theta, f.env_pct) for f, _ in frames)
        cur = self.stack.currentWidget()
        if cur is self.exercise:
            self.exercise.on_frames(frames)
        elif cur is self.wizard:
            self.wizard.on_frames(frames)
        else:
            self.exercise.plots.add_frames(frames)

    def _on_rep(self, rep):
        fb = self.cfg["feedback"]
        self.glow.setGeometry(self.centralWidget().rect())
        if rep.valid:
            self.glow.flash(T.GOOD, fb["glow_ok_ms"])
        else:
            self.glow.flash(T.BAD, fb["glow_bad_ms"])
            self.speaker.say(rep.message)
        if rep.practice:
            self.wizard.on_rep(rep)
            return
        self.reps.append(rep)
        self.exercise.on_rep(rep)

    def _on_event(self, name: str, payload):
        sets = self.sets
        if name in ("session_state", "set_complete", "forced_rest", "session_complete", "paused"):
            in_set = name == "session_state" and payload["state"] == "exercise"
            if in_set != self.in_set:
                self.in_set = in_set
                self._apply_sim_auto()
        if name == "session_state":
            state, set_no = payload["state"], payload["set_no"]
            if state == "exercise":
                if set_no != self.shown_set:
                    self.shown_set = set_no
                    target = self.profile.target_deg if self.profile else self.cfg["exercise"]["target_deg"]
                    load = self.profile.load_kg if self.profile else self.cfg["exercise"]["load_kg"]
                    self.exercise.reset_for_set(set_no, sets, target, load)
                self.stack.setCurrentWidget(self.exercise)
        elif name == "set_complete":
            self.rest.begin(self.cfg["exercise"]["rest_s"], False, self.shown_set, sets,
                            can_continue=self.mode != "replay")
            self.stack.setCurrentWidget(self.rest)
        elif name == "forced_rest":
            self.glow.flash(T.BAD, 1500)
            self.speaker.say("Please rest")
            self.rest.begin(self.cfg["exercise"]["forced_rest_s"], True, self.shown_set, sets,
                            can_continue=self.mode != "replay")
            self.stack.setCurrentWidget(self.rest)
        elif name == "session_complete":
            if self._skipping:
                self._skipping = False
                self._advance_routine()
                return
            target = self.profile.target_deg if self.profile else self.cfg["exercise"]["target_deg"]
            self.summary.show_summary(payload or {}, list(self.reps), self.trace, target)
            self._write_report(payload or {})
            if self.routine is not None:
                i, n = self.routine_idx, len(self.routine.keys)
                nxt = EX.get(self.routine.keys[i + 1]).title if i + 1 < n else ""
                self.summary.set_routine(i + 1, n, nxt)
            self.stack.setCurrentWidget(self.summary)
        elif name == "paused":
            if self.stack.currentWidget() is not self.pause_page:
                self.page_before_pause = self.stack.currentWidget()
            self.pause_page.show_reason(payload)
            self.stack.setCurrentWidget(self.pause_page)
        elif name == "logging":
            self.session_dir = payload
            self.status_labels["fps"].setToolTip(f"Logging to {payload}")

    def _on_calibration(self, step, res):
        self.wizard.on_calibration(step, res)

    def _on_profile(self, p: Profile):
        self.profile = p
        self._save_profile()

    def _on_beep(self, kind: str):
        self.audio.play(kind)

    def _on_error(self, msg: str):
        self.status_labels["conn"].setText(f"Error: {msg}")
        self.status_labels["conn"].setStyleSheet(f"color: {T.BAD};")

    def _on_status(self, st: dict):
        L = self.status_labels
        if st.get("replay"):
            L["conn"].setText("Replay")
            L["conn"].setStyleSheet(f"color: {T.ACCENT};")
        elif st["connected"]:
            L["conn"].setText(f"● Connected · loss {st['loss'] * 100:.1f}%")
            L["conn"].setStyleSheet(f"color: {T.GOOD};")
        else:
            L["conn"].setText("● Sensors disconnected")
            L["conn"].setStyleSheet(f"color: {T.BAD};")
        if st["signal_ok"]:
            L["signal"].setText("Signal good")
            L["signal"].setStyleSheet(f"color: {T.GOOD};")
        else:
            L["signal"].setText(f"Signal poor ({', '.join(st['quality_flags']) or 'check pads'})")
            L["signal"].setStyleSheet(f"color: {T.WARN};")
        L["emg"].setText("EMG raw (MDF on)" if st["emg_mode"] == "raw" else "EMG envelope-only (MDF off)")
        L["calib"].setText("Calibrated" if st["calibrated"] else "Calibration needed")
        L["calib"].setStyleSheet("" if st["calibrated"] else f"color: {T.WARN};")
        mv = st.get("battery_mv", 0)
        low_v = self.cfg["safety"].get("battery_low_v", 7.0)
        if not mv:
            L["battery"].setText("Battery n/a")
            L["battery"].setStyleSheet("")
        elif mv / 1000 < low_v:
            L["battery"].setText(f"Battery LOW {mv / 1000:.1f} V. Replace the 9 V battery")
            L["battery"].setStyleSheet(f"color: {T.WARN};")
        else:
            L["battery"].setText(f"Battery {mv / 1000:.1f} V")
            L["battery"].setStyleSheet("")
        self.wizard.on_status(st)

    def _tick(self):
        if self.stack.currentWidget() is self.exercise:
            self.exercise.tick()
        self.fps_frames += 1
        now = time.monotonic()
        if self.routine is not None:
            self._tick_routine()
        if now - self.fps_t0 >= 1.0:
            self.fps = self.fps_frames / (now - self.fps_t0)
            self.fps_frames = 0
            self.fps_t0 = now
            self.status_labels["fps"].setText(f"{self.fps:.0f} FPS")
