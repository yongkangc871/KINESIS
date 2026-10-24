"""Feedback controller: decides buzzer patterns and GUI glow for each event.

Beeps never overlap: a request that arrives while a pattern is still playing is scheduled
right after it (plus a gap). The controller is clocked by the caller (`tick(now)`), so it
runs identically on device time in replay and in tests.
"""
from __future__ import annotations

from collections import deque
from typing import Callable

KIND_TO_CMD = {"ok": "BEEP_OK", "bad": "BEEP_BAD", "long": "BEEP_LONG"}


class FeedbackController:
    def __init__(self, fb_cfg: dict, send_command: Callable[[str], None] | None = None):
        self.cfg = fb_cfg
        self.send_command = send_command
        self.listeners: list[Callable[[str], None]] = []   # e.g. GUI sound / glow, called on play
        self.queue: deque[str] = deque()
        self.busy_until = -1e18
        self.played: list[tuple[float, str]] = []           # (time, kind) audit trail for tests

    def request(self, kind: str, now: float):
        self.queue.append(kind)
        self.tick(now)

    def on_rep(self, rep, now: float):
        self.request("ok" if rep.valid else "bad", now)

    def on_fatigue_red(self, now: float):
        self.request("long", now)

    def tick(self, now: float):
        while self.queue and now >= self.busy_until:
            kind = self.queue.popleft()
            dur = self.cfg["pattern_s"][kind]
            self.busy_until = now + dur + self.cfg["beep_gap_s"]
            self.played.append((now, kind))
            if self.cfg.get("buzzer", True) and self.send_command:
                self.send_command(KIND_TO_CMD[kind])
            for cb in self.listeners:
                cb(kind)

    def clear(self):
        self.queue.clear()
