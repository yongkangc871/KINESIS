"""Session flow and safety caps: practice -> set -> rest -> next set -> summary.

Pure logic; the pipeline owns one instance and the GUI only sends requests to it.
"""
from __future__ import annotations

import math
from collections import Counter

from .datatypes import RepRecord

SETUP, PRACTICE, EXERCISE, REST, PAUSED, FINISHED = "setup", "practice", "exercise", "rest", "paused", "finished"
COUNTING_STATES = (PRACTICE, EXERCISE)


class SessionManager:
    def __init__(self, ex_cfg: dict):
        self.ex = ex_cfg
        self.state = SETUP
        self.paused_from = None
        self.pause_reason = ""
        self.rest_reason = ""
        self.set_no = 0
        self.reps: list[RepRecord] = []
        self.practice_reps: list[RepRecord] = []
        self.attempt_index = 0

    # ------------------------------------------------------------ counters
    def _set_reps(self):
        return [r for r in self.reps if r.set_no == self.set_no]

    @property
    def valid_in_set(self) -> int:
        return sum(r.valid for r in self._set_reps())

    @property
    def attempts_in_set(self) -> int:
        return len(self._set_reps())

    @property
    def invalid_in_set(self) -> int:
        return self.attempts_in_set - self.valid_in_set

    @property
    def total_valid(self) -> int:
        return sum(r.valid for r in self.reps)

    @property
    def planned_sets(self) -> int:
        return min(self.ex["sets"], self.ex["max_sets"])

    @property
    def counting(self) -> bool:
        return self.state in COUNTING_STATES

    # ------------------------------------------------------------ transitions
    def setup(self):
        """Back to calibration (the user left the practice step); nothing is counted."""
        if self.state == PRACTICE:
            self.practice_reps = []
            self.state = SETUP

    def start_practice(self):
        self.practice_reps = []
        self.state = PRACTICE

    def start_set(self) -> bool:
        if self.set_no >= self.planned_sets or self.total_valid >= self.ex["max_reps_per_session"]:
            self.state = FINISHED
            return False
        self.set_no += 1
        self.state = EXERCISE
        return True

    def rest(self, reason: str):
        self.rest_reason = reason
        self.state = REST

    def pause(self, reason: str):
        if self.state in (PAUSED, FINISHED, SETUP):
            return
        self.paused_from = self.state
        self.pause_reason = reason
        self.state = PAUSED

    def resume(self):
        if self.state == PAUSED:
            self.state = self.paused_from or EXERCISE
            self.pause_reason = ""

    def finish(self):
        self.state = FINISHED

    def record(self, rep: RepRecord) -> list[str]:
        """Books a judged rep and returns follow-up events."""
        if self.state == PRACTICE:
            rep.practice = True
            self.practice_reps.append(rep)
            return ["practice_rep"]
        if self.state != EXERCISE:
            return []
        self.attempt_index += 1
        rep.index = self.attempt_index
        rep.set_no = self.set_no
        self.reps.append(rep)
        events = ["counted" if rep.valid else "rejected"]
        if self.total_valid >= self.ex["max_reps_per_session"]:
            self.finish()
            events.append("session_complete")
        elif self.valid_in_set >= self.ex["reps_per_set"] or self.attempts_in_set >= self.ex["max_attempts_per_set"]:
            if self.set_no >= self.planned_sets:
                self.finish()
                events.append("session_complete")
            else:
                self.rest("set_complete")
                events.append("set_complete")
        return events

    def force_rest(self):
        if self.state == EXERCISE:
            if self.set_no >= self.planned_sets:
                self.finish()
                return ["session_complete"]
            self.rest("fatigue")
            return ["forced_rest"]
        return []

    # ------------------------------------------------------------ summary
    def summary(self) -> dict:
        valid = [r for r in self.reps if r.valid]
        faults = Counter(r.primary_fault for r in self.reps if not r.valid and r.primary_fault)
        peaks = [r.peak_theta for r in valid if math.isfinite(r.peak_theta)]
        return {
            "sets": self.set_no,
            "valid": len(valid),
            "invalid": len(self.reps) - len(valid),
            "attempts": len(self.reps),
            "most_common_fault": faults.most_common(1)[0][0] if faults else "",
            "fault_counts": dict(faults),
            "avg_peak_angle": sum(peaks) / len(peaks) if peaks else float("nan"),
            "fatigue_curve": [(r.index, r.fatigue_score) for r in valid if math.isfinite(r.fatigue_score)],
            "mdf_curve": [(r.index, r.features.get("mdf", float("nan"))) for r in valid],
        }
