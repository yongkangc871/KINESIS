"""Fault codes, user-facing messages and icons."""
from __future__ import annotations

INCOMPLETE_ROM = "INCOMPLETE_ROM"
EARLY_DOWN = "EARLY_DOWN"
NO_MUSCLE = "NO_MUSCLE"
FOREARM_LIFT = "FOREARM_LIFT"
DROP_FAST = "DROP_FAST"
SWING_FAST = "SWING_FAST"
WRIST_TWIST = "WRIST_TWIST"
SIDE_BEND = "SIDE_BEND"
OVER_FLEX = "OVER_FLEX"
NOT_RETURNED = "NOT_RETURNED"
JERKY = "JERKY"
SIGNAL_POOR = "SIGNAL_POOR"
TIMEOUT = "TIMEOUT"

ALL_FAULTS = [INCOMPLETE_ROM, EARLY_DOWN, NO_MUSCLE, FOREARM_LIFT, DROP_FAST, SWING_FAST,
              WRIST_TWIST, SIDE_BEND, OVER_FLEX, NOT_RETURNED, JERKY, SIGNAL_POOR, TIMEOUT]

MESSAGES = {
    INCOMPLETE_ROM: "Lift a little higher",
    EARLY_DOWN: "Pause at the top",
    NO_MUSCLE: "Use your wrist muscle",
    FOREARM_LIFT: "Keep your arm on the table",
    DROP_FAST: "Lower slowly",
    SWING_FAST: "Lift smoothly",
    WRIST_TWIST: "Keep your palm facing up",
    SIDE_BEND: "Keep your hand straight",
    OVER_FLEX: "Not so far",
    NOT_RETURNED: "Lower all the way down",
    JERKY: "Move steadily",
    SIGNAL_POOR: "Check sensor contact",
    TIMEOUT: "Try again",
}

# Shown next to the message so meaning never depends on colour alone.
ICONS = {
    INCOMPLETE_ROM: "⬆",   # up arrow
    EARLY_DOWN: "⏸",       # pause
    NO_MUSCLE: "\U0001f4aa",    # flexed biceps
    FOREARM_LIFT: "✋",     # raised hand
    DROP_FAST: "\U0001f422",    # turtle (slow down)
    SWING_FAST: "\U0001f422",
    WRIST_TWIST: "\U0001f504",  # rotate
    SIDE_BEND: "↔",        # left-right arrow
    OVER_FLEX: "⛔",        # no entry
    NOT_RETURNED: "⬇",     # down arrow
    JERKY: "〰",            # wavy line
    SIGNAL_POOR: "\U0001f50c",  # plug
    TIMEOUT: "⏱",          # stopwatch
}

OK_ICON = "✔"   # heavy check mark


def message_for(code: str, overrides: dict | None = None) -> str:
    return (overrides or {}).get(code, MESSAGES.get(code, code))


def primary_fault(faults: list[str], priority: list[str]) -> str:
    """Most important fault by the configured priority; unknown codes sort last."""
    if not faults:
        return ""
    rank = {c: i for i, c in enumerate(priority)}
    return min(faults, key=lambda c: rank.get(c, len(rank)))
