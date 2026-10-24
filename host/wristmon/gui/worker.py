"""Runs the source + pipeline in its own thread and talks to the GUI only through signals.

GUI -> worker: `call(name, *args)` queues a command executed on the worker thread.
Worker -> GUI: Qt signals (queued across threads automatically).
"""
from __future__ import annotations

import copy
import math
import queue
import time

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from ..config import resolve_dir
from ..headless import apply_event
from ..logger import SessionLogger
from ..pipeline import Pipeline
from ..sources import ReplayEvent, TcpSource

FRAME_EMIT_S = 1 / 60
STATUS_EMIT_S = 0.5


class PipelineWorker(QObject):
    frames = pyqtSignal(object)            # list[(Frame, sm_state)]
    rep = pyqtSignal(object)               # RepRecord
    pipeline_event = pyqtSignal(str, object)   # pipeline/session events (not "event": that is QObject.event)
    calibration = pyqtSignal(str, object)  # step, StepResult
    profile = pyqtSignal(object)           # Profile copy after any calibration change
    status = pyqtSignal(object)            # dict for the status bar
    beep = pyqtSignal(str)                 # 'ok' | 'bad' | 'long', when actually played
    error = pyqtSignal(str)
    replay_finished = pyqtSignal()
    stopped = pyqtSignal()

    def __init__(self, cfg: dict, source, profile=None, replay: bool = False):
        super().__init__()
        self.cfg = copy.deepcopy(cfg)        # the worker thread owns its copy
        self.source = source
        self.replay = replay
        self.pipe = self._make_pipeline(profile)
        self._frames = []
        self._cmds: queue.Queue = queue.Queue()
        self._running = True
        self._connected = True

    def _make_pipeline(self, profile=None) -> Pipeline:
        p = Pipeline(self.cfg, profile, send_command=self.source.write)
        p.link_stats = self.source.link_stats
        p.on_frame = self._on_frame
        p.on_rep = self.rep.emit
        p.on_event = self.pipeline_event.emit
        p.on_calibration = self._on_calibration
        p.feedback.listeners.append(self.beep.emit)
        return p

    # ------------------------------------------------------------ GUI side
    def call(self, name: str, *args):
        self._cmds.put((name, args))

    # ------------------------------------------------------------ worker thread
    def _on_frame(self, frame, state):
        self._frames.append((frame, state))

    def _on_calibration(self, step, res):
        self.calibration.emit(step, res)
        self.profile.emit(copy.deepcopy(self.pipe.profile))

    def _drain(self):
        while True:
            try:
                name, args = self._cmds.get_nowait()
            except queue.Empty:
                return
            try:
                getattr(self, f"_cmd_{name}")(*args)
            except Exception as e:   # a bad command must never kill acquisition
                self.error.emit(f"{name}: {e}")

    def _open(self) -> bool:
        try:
            self.source.open()
            return True
        except Exception as e:
            self.error.emit(str(e))
            return False

    @pyqtSlot()
    def run(self):
        opened = self._open()
        last_emit = last_status = last_try = 0.0
        last_packet = time.monotonic()
        replay_done = False
        # Wi-Fi delivers packets in bursts, so it gets a slightly longer silence allowance.
        timeout = (self.cfg["link"]["disconnect_timeout_s"] if isinstance(self.source, TcpSource)
                   else self.cfg["serial"]["disconnect_timeout_s"])
        while self._running:
            self._drain()
            now = time.monotonic()
            items = []
            if not opened:
                if now - last_try > 2.0:     # keep trying to reconnect (USB re-plugged)
                    last_try = now
                    opened = self._open()
                time.sleep(0.05)
            else:
                try:
                    items = self.source.read()
                except Exception as e:
                    self.error.emit(f"sensor link lost: {e}")
                    opened = False
                    try:
                        self.source.close()
                    except Exception:
                        pass
            if items:
                last_packet = now
            for it in items:
                if isinstance(it, ReplayEvent):
                    apply_event(self.pipe, it)
                else:
                    self.pipe.handle(it)
            if not items:
                self.pipe.feedback.tick(self.pipe.now)
                if not self.replay:
                    time.sleep(0.002)
            if self._frames and now - last_emit >= FRAME_EMIT_S:
                self.frames.emit(self._frames)
                self._frames = []
                last_emit = now
            connected = opened and (self.replay or (now - last_packet) < timeout)
            if self._connected and not connected and self.pipe.session.counting:
                # Pause instead of miscounting while the sensors are gone.
                self.pipe.pause("disconnect")
            self._connected = connected
            if now - last_status >= STATUS_EMIT_S:
                last_status = now
                self.status.emit(self._status(connected))
            if self.replay and self.source.finished and not replay_done:
                replay_done = True
                self.replay_finished.emit()
        try:
            self.source.close()
        finally:
            self.pipe.close()
            self.stopped.emit()

    def _status(self, connected: bool) -> dict:
        st = self.pipe.status()
        ls = self.source.link_stats
        st.update(connected=connected, loss=ls.loss_ratio if ls else 0.0,
                  crc_errors=ls.crc_errors if ls else 0, replay=self.replay,
                  emg_rate=self.pipe.counts["emg"], imu_rate=self.pipe.counts["imu"])
        return st

    # ------------------------------------------------------------ commands
    def _cmd_begin_session(self, profile, log: bool):
        p = self.pipe
        p.new_session()
        if self.cfg["emg"]["mode"] not in ("raw", "envelope") and math.isfinite(profile.rest_mean):
            p.set_emg_mode(profile.emg_mode)
        p.apply_profile(profile)
        if log and self.cfg["logging"]["enabled"]:
            if p.logger:
                p.logger.close()
            p.logger = SessionLogger(resolve_dir(self.cfg, "dir"), profile.user, self.cfg)
            p.logger.save_calibration(profile)
            p.logger.write_meta(emg_mode=p.emg_mode)
            self.pipeline_event.emit("logging", str(p.logger.dir))

    def _cmd_set_config(self, cfg: dict):
        """Switches exercise: a fresh pipeline with that exercise's thresholds and wording."""
        self.pipe.close()
        self.cfg = cfg
        self.pipe = self._make_pipeline()
        patient = getattr(self.source, "patient", None)
        if patient is not None:      # the virtual patient aims for the new exercise's target
            patient.target = cfg["exercise"]["target_deg"]
            patient.rom = patient.target + 7.0
            patient.tempo = cfg["exercise"]["tempo"]
            patient.isometric = cfg["exercise"].get("signal") == "emg"

    def _cmd_session_action(self, action: str):
        self.pipe.session_action(action)

    def _cmd_pause(self, reason: str):
        self.pipe.pause(reason)

    def _cmd_start_capture(self, step: str, duration: float):
        self.pipe.start_capture(step, duration)

    def _cmd_cancel_capture(self):
        self.pipe.cancel_capture()

    def _cmd_set_load(self, kg: float):
        self.pipe.set_load(kg)
        self.profile.emit(copy.deepcopy(self.pipe.profile))

    def _cmd_hint(self, step: str):
        self.source.hint(step)

    def _cmd_queue_reps(self, kinds):
        if hasattr(self.source, "queue_reps"):
            self.source.queue_reps(kinds)

    def _cmd_set_auto(self, kind):
        if hasattr(self.source, "patient"):
            self.source.patient.auto_kind = kind

    def _cmd_stop(self):
        self._running = False
