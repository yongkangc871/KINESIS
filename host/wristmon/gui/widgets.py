"""Custom-painted widgets: anatomical arm view, tempo pacer, edge glow, placement diagram,
message banner and fatigue gauge."""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QVariantAnimation
from PyQt6.QtGui import (QBrush, QColor, QFontMetrics, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap, QRadialGradient, QPolygonF)
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme as T
from .icons import Icon


def scene_font(scale: float, size: float, min_pt: float, **kw):
    """Font for text in a scene drawn at `scale`: `size` in scene units, but never smaller than
    `min_pt` on screen, so labels stay readable when the drawing shrinks to fit its card."""
    return T.font(max(size, min_pt / max(scale, 0.05)), **kw)


def min_jerk(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x ** 3 * (10 - 15 * x + 6 * x * x)


class PacerClock:
    """Tempo cycle Lift - Hold - Lower - Pause; the ideal angle at any time drives the
    ghost hand and the idle demo. `sync()` restarts the cycle at a real movement onset."""

    CAPTIONS = {"pause": "Arm on table…", "lift": "Lift your hand…",
                "hold": "Hold…", "lower": "Lower slowly…"}

    def __init__(self, tempo: dict, target: float = 45.0, captions: dict | None = None):
        self.phases = [("lift", tempo["lift_s"]), ("hold", tempo["hold_s"]),
                       ("lower", tempo["lower_s"]), ("pause", tempo["pause_s"])]
        self.cycle = sum(d for _, d in self.phases)
        self.target = target
        self.captions = {**self.CAPTIONS, **(captions or {})}     # demo caption per phase
        self.start = time.monotonic() - tempo["lift_s"] - tempo["hold_s"] - tempo["lower_s"]

    def sync(self, now: float | None = None):
        self.start = now if now is not None else time.monotonic()

    def lift_elapsed(self, theta: float) -> float:
        """Time into the guide's lift at which its ideal angle reaches theta."""
        lift_s = self.phases[0][1]
        x = max(0.0, min(1.0, theta / max(self.target, 1e-6)))
        lo, hi = 0.0, 1.0
        for _ in range(30):                   # invert the min-jerk profile
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if min_jerk(mid) < x else (lo, mid)
        return lo * lift_s

    def phase(self, now: float | None = None):
        """(name, fraction within phase, ideal theta, fraction of whole cycle)."""
        now = time.monotonic() if now is None else now
        x = (now - self.start) % self.cycle
        acc = 0.0
        for name, d in self.phases:
            if x < acc + d:
                f = (x - acc) / d if d > 0 else 1.0
                break
            acc += d
        else:
            name, f = "pause", 1.0
        th = {"lift": min_jerk(f) * self.target, "hold": self.target,
              "lower": (1 - min_jerk(f)) * self.target, "pause": 0.0}[name]
        return name, f, th, x / self.cycle


# ------------------------------------------------------------------ anatomy paths

SKIN_HI = QColor("#e4bc9c")
SKIN = QColor("#cf9f7f")
SKIN_LO = QColor("#a5745a")
SKIN_LINE = QColor(110, 72, 52, 190)

TABLE_TOP_Y = 380.0
TABLE_EDGE_X = 612.0
PIVOT = QPointF(612.0, 352.0)
HAND_SCALE = 1.35
PLATE_CENTER = QPointF(92.0, -34.0)    # dumbbell handle axis, hand-local units
PLATE_R = 58.0                         # large enough for the steel rim to show around the fist


def _forearm_path() -> QPainterPath:
    """Palm-up forearm, side view: elbow (left) resting on the table, muscle belly in the
    proximal third, tapering to the wrist at the table edge. Top contour = flexor side."""
    p = QPainterPath()
    p.moveTo(215, 380)
    p.lineTo(600, 380)
    # Rounded wrist cap around the pivot, so flexing the hand shows a smooth joint.
    p.cubicTo(622, 380, 640, 368, 640, 352)
    p.cubicTo(640, 336, 624, 324, 606, 324)
    p.cubicTo(560, 322, 420, 300, 330, 286)
    p.cubicTo(270, 278, 215, 279, 190, 292)
    p.cubicTo(164, 306, 160, 362, 190, 378)
    p.cubicTo(198, 381, 206, 380, 215, 380)
    p.closeSubpath()
    return p


def _upper_arm_path() -> QPainterPath:
    """Upper arm rising from the elbow toward the (off-screen) shoulder; drawn fading out."""
    p = QPainterPath()
    p.moveTo(170, 356)
    p.cubicTo(150, 250, 132, 120, 118, -10)
    p.lineTo(236, -10)
    p.cubicTo(238, 110, 248, 220, 268, 292)
    p.lineTo(200, 306)
    p.closeSubpath()
    return p


def _flexor_path() -> QPainterPath:
    """Wrist-flexor group (FCR / FDS) as a spindle on the palm side, proximal to mid forearm."""
    p = QPainterPath()
    p.moveTo(236, 296)
    p.cubicTo(300, 284, 430, 297, 508, 315)
    p.cubicTo(430, 322, 300, 324, 236, 296)
    p.closeSubpath()
    return p


def _fist_path() -> QPainterPath:
    """Hand gripping the handle, seen from the thumb side. Hand-local units: origin at the
    wrist pivot, +x toward the fingers, -y = palm side (up when the palm faces up).
    Left open at the wrist so the outline has no seam where it meets the forearm."""
    p = QPainterPath()
    p.moveTo(-6, 20)
    p.cubicTo(30, 23, 80, 24, 104, 21)        # dorsum over the metacarpals
    p.cubicTo(116, 19, 124, 12, 127, 3)       # MCP knuckle
    p.cubicTo(132, -8, 138, -16, 137, -27)    # proximal phalanx up to the PIP joint
    p.cubicTo(136, -42, 130, -54, 120, -60)   # middle phalanx to the DIP joint
    p.cubicTo(112, -65, 100, -66, 92, -60)    # distal phalanx over the top of the handle
    p.cubicTo(86, -55, 82, -44, 74, -38)      # finger tip tucked toward the palm
    p.cubicTo(60, -36, 46, -44, 30, -39)      # thenar eminence
    p.cubicTo(16, -34, 4, -24, -6, -20)       # palm side of the wrist
    return p


def _fist_thumb_path() -> QPainterPath:
    """Thumb wrapped across the middle phalanx of the index finger."""
    p = QPainterPath()
    p.moveTo(24, -36)
    p.cubicTo(44, -54, 76, -62, 104, -56)
    p.cubicTo(114, -54, 116, -46, 108, -43)
    p.cubicTo(88, -42, 60, -38, 44, -30)
    p.closeSubpath()
    return p


def _open_hand_path() -> QPainterPath:
    """Relaxed hand without a weight: fingers resting in slight flexion."""
    p = QPainterPath()
    p.moveTo(-6, 20)
    p.cubicTo(40, 24, 82, 24, 108, 20)
    p.cubicTo(138, 16, 166, 8, 184, -10)      # fingers curling gently upward
    p.cubicTo(190, -17, 188, -26, 180, -26)   # finger tips
    p.cubicTo(166, -20, 146, -12, 118, -16)
    p.cubicTo(96, -20, 78, -24, 64, -27)
    p.cubicTo(50, -31, 40, -41, 26, -36)
    p.cubicTo(14, -32, 4, -22, -6, -20)
    return p


def _open_thumb_path() -> QPainterPath:
    p = QPainterPath()
    p.moveTo(26, -36)
    p.cubicTo(46, -48, 74, -50, 96, -42)
    p.cubicTo(103, -40, 102, -33, 95, -33)
    p.cubicTo(76, -34, 56, -32, 40, -28)
    p.closeSubpath()
    return p


def _hexagon(c: QPointF, r: float) -> QPolygonF:
    return QPolygonF([QPointF(c.x() + r * math.cos(math.radians(30 + 60 * k)),
                              c.y() + r * math.sin(math.radians(30 + 60 * k))) for k in range(6)])


FOREARM = _forearm_path()
UPPER_ARM = _upper_arm_path()
FLEXOR = _flexor_path()
FIST = _fist_path()
FIST_THUMB = _fist_thumb_path()
OPEN_HAND = _open_hand_path()
OPEN_THUMB = _open_thumb_path()


def _skin_gradient(y0: float, y1: float, alpha: int = 255) -> QLinearGradient:
    g = QLinearGradient(0, y0, 0, y1)
    for pos, c in ((0.0, SKIN_HI), (0.55, SKIN), (1.0, SKIN_LO)):
        c = QColor(c)
        c.setAlpha(alpha)
        g.setColorAt(pos, c)
    return g


# Area (in arm-view units, relative to the wrist pivot) that the hand can occupy before rotation.
HAND_BOUNDS = QRectF(-20, -135, 300, 185)


def draw_hand(p: QPainter, theta: float, gripping: bool, load_kg: float, ghost: bool = False,
              label_plate: bool = True):
    """Hand (and dumbbell when gripping) rotated about the wrist pivot by theta (+ = flexion)."""
    p.save()
    p.translate(PIVOT)
    p.rotate(-theta)
    draw_hand_local(p, gripping, ghost)
    p.restore()
    if gripping and label_plate and not ghost:
        draw_load_label(p, theta, load_kg)


def draw_hand_local(p: QPainter, gripping: bool, ghost: bool = False, plate: bool = True):
    """The hand at the origin (wrist pivot), unrotated. Used directly and for the pixmap cache.
    gripping without a plate = a gentle fist (the no-weight exercise)."""
    p.save()
    p.scale(HAND_SCALE, HAND_SCALE)
    hand = FIST if gripping else OPEN_HAND
    thumb = FIST_THUMB if gripping else OPEN_THUMB
    plate = plate and gripping
    line_w = 1.3 / HAND_SCALE
    if ghost:
        p.setBrush(T.qcolor(T.ACCENT, 22))
        p.setPen(QPen(T.qcolor(T.ACCENT, 150), 1.6 / HAND_SCALE, Qt.PenStyle.DashLine))
        if plate:
            p.drawPolygon(_hexagon(PLATE_CENTER, PLATE_R))
        p.drawPath(hand)
        p.restore()
        return

    if plate:
        # The plate on the far side of the hand: brushed-steel hexagon with a darker rim.
        # (The near plate is left out so the gripping hand stays readable.)
        g = QRadialGradient(PLATE_CENTER + QPointF(-16, -18), PLATE_R * 1.7)
        g.setColorAt(0.0, QColor("#b9c1ca"))
        g.setColorAt(0.55, QColor("#727b85"))
        g.setColorAt(1.0, QColor("#3c434b"))
        p.setBrush(g)
        p.setPen(QPen(QColor("#252a30"), 2.2 / HAND_SCALE))
        p.drawPolygon(_hexagon(PLATE_CENTER, PLATE_R))
        p.setPen(QPen(QColor(255, 255, 255, 45), 1.0 / HAND_SCALE))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(_hexagon(PLATE_CENTER, PLATE_R * 0.8))

    # Fill, then stroke the open outline so there is no seam at the wrist.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_skin_gradient(-70, 25))
    p.drawPath(hand)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(SKIN_LINE, line_w))
    p.drawPath(hand)
    # Anatomical detail: knuckle and finger-joint creases.
    p.setPen(QPen(QColor(120, 78, 56, 120), 1.0 / HAND_SCALE))
    crease = QPainterPath()
    if gripping:
        crease.moveTo(116, 16)
        crease.quadTo(124, 9, 126, 1)       # MCP knuckle
        crease.moveTo(128, -26)
        crease.quadTo(133, -29, 137, -28)   # PIP joint
        crease.moveTo(116, -57)
        crease.quadTo(119, -61, 121, -60)   # DIP joint
        crease.moveTo(64, -30)
        crease.quadTo(72, -34, 78, -40)     # palm crease under the finger tips
    else:
        crease.moveTo(146, 10)
        crease.quadTo(150, 2, 148, -12)
        crease.moveTo(168, 0)
        crease.quadTo(172, -8, 170, -19)
    p.drawPath(crease)
    p.setBrush(_skin_gradient(-66, -28))
    p.setPen(QPen(SKIN_LINE, line_w))
    p.drawPath(thumb)
    if gripping:
        nail = QPainterPath()
        nail.moveTo(97, -56)
        nail.quadTo(105, -56, 110, -50)
        p.setPen(QPen(QColor(255, 240, 230, 120), 1.2 / HAND_SCALE))
        p.drawPath(nail)
    p.restore()


def draw_load_label(p: QPainter, theta: float, load_kg: float, scale: float = 1.0):
    """Load tag kept upright, just outside the plate's upper-right corner."""
    a = math.radians(-theta)
    lx = PLATE_CENTER.x() * HAND_SCALE + PLATE_R * HAND_SCALE * 0.75
    ly = PLATE_CENTER.y() * HAND_SCALE - PLATE_R * HAND_SCALE * 0.95
    cx = PIVOT.x() + lx * math.cos(a) - ly * math.sin(a)
    cy = PIVOT.y() + lx * math.sin(a) + ly * math.cos(a)
    p.save()
    p.setFont(scene_font(scale, 12, T.S, bold=True))
    p.setPen(QColor(T.TEXT))
    p.drawText(QRectF(cx, cy - 60, 160, 60), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
               f"{load_kg:g} kg")
    p.restore()


# ------------------------------------------------------------------ arm view

class ArmView(QWidget):
    """2D side view: forearm static on the table, hand + dumbbell rotating at the wrist by theta.

    The wrist-flexor group is tinted by the single EMG channel's %RVC (one uniform colour,
    no fake spatial gradient), with a bar and a target-activation marker beside it.
    The static scene (table, shadow, arm) is cached in a pixmap; only overlays and the hand
    are drawn per frame.
    """

    W, H = 1000.0, 600.0
    PIVOT = PIVOT
    BASE_ANGLE = 0.0           # screen direction (deg, counter-clockwise from +x) of theta = 0
    UNIT = "°"                 # "%" for muscle-driven exercises (theta = % of the strongest squeeze)
    POSE_TARGET = None         # muscle-driven: the drawn pose at 100 % of the target (deg or 0..1)
    CAPTION_W = 640.0          # caption box width (from x = 150)
    BAR_DELTA = False          # label the bar fill with its difference from the 100 % reference
    CAPTION_TOP = None         # caption box top in scene units; None = anchored at the bottom
    LEADER = ((330, 290), (352, 248))   # muscle label leader line

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theta = 0.0
        self.env_pct = float("nan")
        self.target = 45.0
        self.tol = 2.0
        self.min_activation = 30.0
        self.ghost = None             # ideal angle for the current tempo, or None
        self.demo = False             # idle demonstration replaces the live hand
        self.demo_theta = 0.0
        self.caption = ""
        self.dumbbell = True          # hand gripping (a fist); with a plate only when `weight`
        self.weight = True
        self.load_kg = 5.0
        self.show_bar = True
        self.show_angle = True
        self.angle_label = "Wrist angle"
        self.muscle_label = "Wrist flexors"
        self.phase_text = ""           # e.g. "Active phase" above the muscle bar ('' = none)
        self._cache: QPixmap | None = None
        self._cache_key = None
        self._hands: dict = {}         # (gripping, ghost, size key) -> pre-rendered hand pixmap
        self._s = 1.0                  # current scene scale (set by _transform)
        self.setMinimumSize(320, 190)
        # Not WA_OpaquePaintEvent: at fractional display scaling (e.g. 150 %) the last device-pixel
        # column falls outside the paint rect and would show as an unpainted grey line.
        self.setStyleSheet(f"background: {T.SURFACE};")

    def set_state(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.update()

    def _pose(self, v):
        """theta -> what is drawn. Angle exercises draw the angle itself; muscle-driven ones
        draw the effort as a fraction of the target, so reaching the target shows the full pose."""
        if v is None or self.POSE_TARGET is None:
            return v
        return max(0.0, min(1.15, v / max(self.target, 1.0))) * self.POSE_TARGET

    def _transform(self):
        s = self._s = min(self.width() / self.W, self.height() / self.H)
        return s, (self.width() - self.W * s) / 2, (self.height() - self.H * s) / 2

    def _font(self, size, min_pt=T.S, **kw):
        return scene_font(self._s, size, min_pt, **kw)

    def _background(self) -> QPixmap:
        """Everything that does not move: table, arm, target zone, static labels, bar track."""
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, self.target, self.tol, self.min_activation,
               self.show_bar, self.show_angle and not self.demo, self.angle_label, self.muscle_label)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(T.SURFACE))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s, ox, oy = self._transform()
        p.translate(ox, oy)
        p.scale(s, s)
        self._paint_static(p)
        self._target_zone(p)
        self._flexor_label(p)
        if self.show_bar:
            self._bar_static(p)
        if self.show_angle and not self.demo:
            p.setPen(QColor(T.MUTED))
            p.setFont(self._font(12, caps=True))
            p.drawText(QRectF(28, 18, 400, 80), Qt.AlignmentFlag.AlignLeft, self.angle_label)
        p.end()
        self._cache, self._cache_key = pm, key
        return pm

    def _paint_static(self, p: QPainter):
        # Table: thin lit top edge, darker front face, crisp corner at the edge.
        p.setPen(Qt.PenStyle.NoPen)
        face = QLinearGradient(0, TABLE_TOP_Y, 0, self.H)
        face.setColorAt(0, QColor(T.TABLE_FACE[0]))
        face.setColorAt(1, QColor(T.TABLE_FACE[1]))
        p.setBrush(face)
        p.drawRect(QRectF(0, TABLE_TOP_Y + 12, TABLE_EDGE_X, self.H - TABLE_TOP_Y - 12))
        top = QLinearGradient(0, TABLE_TOP_Y, 0, TABLE_TOP_Y + 12)
        top.setColorAt(0, QColor(T.TABLE_TOP[0]))
        top.setColorAt(1, QColor(T.TABLE_TOP[1]))
        p.setBrush(top)
        p.drawRect(QRectF(0, TABLE_TOP_Y, TABLE_EDGE_X, 12))
        p.setPen(QPen(QColor(T.BORDER_STRONG), 1.2))
        p.drawLine(QPointF(0, TABLE_TOP_Y), QPointF(TABLE_EDGE_X, TABLE_TOP_Y))
        # Soft contact shadow under the forearm.
        sh = QRadialGradient(QPointF(400, TABLE_TOP_Y + 2), 260)
        sh.setColorAt(0, QColor(0, 0, 0, T.SHADOW_ALPHA))
        sh.setColorAt(1, QColor(0, 0, 0, 0))
        p.save()
        p.translate(400, TABLE_TOP_Y + 2)
        p.scale(1.0, 0.05)
        p.translate(-400, -(TABLE_TOP_Y + 2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(sh)
        p.drawEllipse(QPointF(400, TABLE_TOP_Y + 2), 260, 260)
        p.restore()

        # Upper arm: a shaded limb (slightly darker, it is further away) that dissolves into the
        # background toward the shoulder, so it never competes with the readouts above it.
        g = QLinearGradient(110, 0, 270, 0)
        for pos, c in ((0.0, SKIN_LO), (0.45, SKIN), (1.0, SKIN_LO)):
            c = QColor(c).darker(112)
            g.setColorAt(pos, c)
        p.setBrush(g)
        p.setPen(QPen(SKIN_LINE, 1.2))
        p.drawPath(UPPER_ARM)
        fade = QLinearGradient(0, 300, 0, 140)
        fade.setColorAt(0, T.qcolor(T.SURFACE, 0))
        fade.setColorAt(1, T.qcolor(T.SURFACE, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fade)
        p.drawRect(QRectF(90, -20, 200, 322))

        # Forearm with cylindrical shading and a rim light along the palm side.
        p.setBrush(_skin_gradient(278, 382))
        p.setPen(QPen(SKIN_LINE, 1.3))
        p.drawPath(FOREARM)
        rim = QPainterPath()
        rim.moveTo(600, 327)
        rim.cubicTo(552, 325, 420, 304, 330, 290)
        rim.cubicTo(280, 283, 236, 284, 210, 292)
        p.setPen(QPen(QColor(255, 236, 220, 70), 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rim)

    def _hand_pixmap(self, gripping: bool, ghost: bool) -> QPixmap:
        """Hand rendered once per size; each frame only rotates this pixmap."""
        s, _, _ = self._transform()
        dpr = self.devicePixelRatioF()
        key = (gripping, ghost, self.weight, round(s, 4), dpr)
        pm = self._hands.get(key)
        if pm is None:
            b = HAND_BOUNDS
            pm = QPixmap(max(1, int(b.width() * s * dpr)), max(1, int(b.height() * s * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            hp = QPainter(pm)
            hp.setRenderHint(QPainter.RenderHint.Antialiasing)
            hp.scale(s, s)
            hp.translate(-b.left(), -b.top())
            draw_hand_local(hp, gripping, ghost, plate=self.weight)
            hp.end()
            if len(self._hands) > 8:
                self._hands.clear()
            self._hands[key] = pm
        return pm

    def _draw_limb(self, p: QPainter, theta: float, ghost: bool):
        """The moving part (here the hand), rotated by theta about the pivot."""
        p.save()
        p.translate(PIVOT)
        p.rotate(-theta)
        p.drawPixmap(HAND_BOUNDS, self._hand_pixmap(self.dumbbell, ghost), QRectF())
        p.restore()
        if not ghost and self.dumbbell and self.weight:
            draw_load_label(p, theta, self.load_kg, self._s)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(0, 0, self._background())
        s, ox, oy = self._transform()
        p.translate(ox, oy)
        p.scale(s, s)
        theta = self._pose(self.demo_theta if self.demo else self.theta)
        self._flexors(p, theta)
        if self.ghost is not None and not self.demo:
            self._draw_limb(p, self._pose(self.ghost), ghost=True)
        self._draw_limb(p, theta, ghost=False)
        self._target_label(p)
        if self.show_bar:
            self._activation_bar(p)
        self._labels(p)
        p.end()

    MUSCLE = FLEXOR

    def _muscle_brush(self, p):
        # One uniform colour for the single EMG value (no fake spatial gradient).
        col = T.activation_color(self.env_pct)
        fill = QColor(col)
        fill.setAlpha(165)
        p.setBrush(fill)
        p.setPen(QPen(QColor(col).darker(130), 1.2))

    def _flexors(self, p, theta: float):
        self._muscle_brush(p)
        p.drawPath(self.MUSCLE)

    def _flexor_label(self, p):
        # Label with a hairline leader.
        (x0, y0), (x1, y1) = self.LEADER
        p.setPen(QPen(QColor(T.MUTED), 1))
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        p.setFont(self._font(12, caps=True))
        p.drawText(QRectF(x1 + 4, y1 - 58, 400, 60), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   self.muscle_label)

    def _target_zone(self, p):
        r = 300
        rect = QRectF(self.PIVOT.x() - r, self.PIVOT.y() - r, 2 * r, 2 * r)
        target = self._pose(self.target)
        lo, hi = target - self.tol - 1, target + self.tol + 1
        lo, hi = lo + self.BASE_ANGLE, hi + self.BASE_ANGLE
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.qcolor(T.TARGET, 38))
        p.drawPie(rect, int(lo * 16), int((hi - lo) * 16))
        a = math.radians(target + self.BASE_ANGLE)
        end = QPointF(self.PIVOT.x() + r * math.cos(a), self.PIVOT.y() - r * math.sin(a))
        pen = QPen(T.qcolor(T.TARGET, 170), 1.6, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(self.PIVOT, end)
        p.setPen(QPen(T.qcolor(T.TARGET, 120), 1.4))
        p.drawArc(rect, int(lo * 16), int((hi - lo) * 16))

    TARGET_LABEL = "above"         # above | right | below the end of the target line

    def _target_label(self, p):
        """Drawn every frame over the limb, so a limb at the target never hides it."""
        if self.UNIT == "%":
            return      # a muscle target is marked on the muscle bar instead
        r = 300
        a = math.radians(self.target + self.BASE_ANGLE)
        end = QPointF(self.PIVOT.x() + r * math.cos(a), self.PIVOT.y() - r * math.sin(a))
        p.setFont(self._font(14, bold=True, tabular=True))
        p.setPen(QColor(T.TARGET))
        text = f"Target {self.target:.0f}°"
        if self.TARGET_LABEL == "right":
            # Level with the line end but clear of the knee, which lies just beyond it.
            p.drawText(QRectF(end.x() + 70, end.y() - 40, 400, 80),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        elif self.TARGET_LABEL == "below":
            p.drawText(QRectF(end.x() - 200, end.y() + 8, 400, 64),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)
        else:
            # Centred above the end of the target line, clear of the muscle bar on the right.
            p.drawText(QRectF(end.x() - 200, end.y() - 72, 400, 64),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, text)

    BAR = (930.0, 96.0, 470.0, 18.0)   # x, top, bottom, width
    BAR_FULL = 150.0                    # %RVC at the top of the bar

    def _bar_static(self, p):
        x, y0, y1, w = self.BAR
        p.setFont(self._font(12, caps=True))
        p.setPen(QColor(T.MUTED))
        p.drawText(QRectF(x - 100, 0, w + 200, y0 - 12), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                   "Muscle")
        p.setPen(QPen(QColor(T.BORDER_STRONG), 1))
        p.setBrush(QColor(T.SURFACE_2))
        p.drawRoundedRect(QRectF(x, y0, w, y1 - y0), 4, 4)
        ym = y1 - (y1 - y0) * self.min_activation / self.BAR_FULL
        p.setPen(QColor(T.MUTED))
        p.setFont(self._font(12))
        p.drawText(QRectF(x - 216, ym - 40, 200, 80), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "target")

    def _activation_bar(self, p):
        """Per-frame part of the bar: fill, target marker (drawn over the fill) and value."""
        x, y0, y1, w = self.BAR
        full = self.BAR_FULL
        pct = self.env_pct if self.env_pct == self.env_pct else 0.0
        h = (y1 - y0) * max(0.0, min(1.0, pct / full))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.activation_color(self.env_pct))
        if h > 0:
            p.drawRoundedRect(QRectF(x + 2, y1 - h, w - 4, max(h - 2, 2)), 3, 3)
        ym = y1 - (y1 - y0) * self.min_activation / full
        p.setPen(QPen(QColor(T.TEXT), 2))
        p.drawLine(QPointF(x - 10, ym), QPointF(x + w + 4, ym))
        tri = QPolygonF([QPointF(x - 10, ym - 6), QPointF(x - 2, ym), QPointF(x - 10, ym + 6)])
        p.setBrush(QColor(T.TEXT))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tri)
        p.setPen(QColor(T.TEXT))
        p.setFont(self._font(20, T.M, bold=True, tabular=True))
        txt = f"{pct:.0f}%" if self.env_pct == self.env_pct else "–"
        p.drawText(QRectF(x - 100, y1 + 8, w + 200, 120), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   txt)
        if self.BAR_DELTA and self.env_pct == self.env_pct and h > 0:
            # Tick at the top of the fill with the difference from the 100 % reference effort.
            yt = y1 - h
            p.setPen(QPen(QColor(T.TEXT), 1.6))
            p.drawLine(QPointF(x - 8, yt), QPointF(x + w + 4, yt))
            p.setFont(self._font(12, T.XS, bold=True, tabular=True))
            p.drawText(QRectF(x - 216, yt - 40, 200, 80), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{pct - 100:+.0f}%")
        if self.phase_text:
            p.setPen(QColor(T.TEXT))
            p.setFont(self._font(12, T.XS, bold=True, caps=True))
            fm = QFontMetricsF(self._font(12, caps=True))
            # Right-aligned to the scene edge: centred on the bar it would run off the right.
            p.drawText(QRectF(x - 400, 0, self.W - 6 - (x - 400), y0 - 16 - fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, self.phase_text)

    def _labels(self, p):
        if self.show_angle and not self.demo:
            # The value sits just below the "Wrist angle" caption, whatever size that caption is.
            top = 18 + QFontMetricsF(self._font(12, caps=True)).height()
            p.setPen(QColor(T.TEXT))
            p.setFont(self._font(44, T.L, light=True, tabular=True))
            p.drawText(QRectF(24, top, 400, 160), Qt.AlignmentFlag.AlignLeft, f"{self.theta:.0f}{self.UNIT}")
        if self.demo or self.caption:
            # Lower-third caption box, sized to its two lines of text and anchored at the bottom.
            head_font = self._font(11, T.XS, bold=True, caps=True)
            text_font = self._font(26, T.M, bold=True)
            hh = QFontMetricsF(head_font).height()
            th = QFontMetricsF(text_font).height()
            bh = max(86.0, 8 + hh + 4 + th + 12)
            top = self.CAPTION_TOP if self.CAPTION_TOP is not None else 578 - bh
            box = QRectF(150, top, self.CAPTION_W, bh)
            p.setPen(QPen(QColor(T.BORDER_STRONG), 1))
            p.setBrush(T.qcolor(T.SURFACE, 235))
            p.drawRoundedRect(box, 10, 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.ACCENT))
            p.drawRoundedRect(QRectF(box.left(), box.top(), 6, box.height()), 3, 3)
            p.setPen(QColor(T.ACCENT))
            p.setFont(head_font)
            p.drawText(QRectF(box.left() + 24, box.top() + 8, 600, hh), Qt.AlignmentFlag.AlignLeft,
                       "Demonstration" if self.demo else "Instruction")
            p.setPen(QColor(T.TEXT))
            p.setFont(text_font)
            p.drawText(QRectF(box.left() + 24, box.top() + 8 + hh + 4, box.width() - 40, th),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self.caption or "Watch how to do it")


# ------------------------------------------------------------------ leg views (seated, side view)

SEAT_Y = 332.0          # top of the chair seat
FLOOR_Y = 570.0
SHIN_LEN = 236.0        # knee centre to ankle
KNEE_R = 28.0


def _shin_path() -> QPainterPath:
    """Lower leg and foot in local units: origin at the knee centre, +x down the shin to the
    ankle, -y = the front of the leg (the toes point to -y)."""
    L = SHIN_LEN
    p = QPainterPath()
    p.moveTo(0, -26)
    p.cubicTo(80, -24, 170, -20, L, -17)                    # shin bone, front edge
    p.cubicTo(L + 6, -50, L + 14, -80, L + 22, -92)         # top of the foot
    p.quadTo(L + 36, -100, L + 36, -86)                      # toes
    p.lineTo(L + 36, 14)                                     # sole
    p.quadTo(L + 36, 26, L + 18, 24)                         # heel
    p.lineTo(L - 8, 18)                                      # back of the ankle
    p.cubicTo(L - 80, 26, 120, 48, 40, 36)                   # calf
    p.quadTo(10, 32, 0, 28)                                  # back of the knee
    p.closeSubpath()
    return p


SHIN = _shin_path()


def _draw_chair(p: QPainter, x0: float, x1: float):
    """Seat from x0 to x1, legs to the floor and a backrest at x0, in the table colours."""
    p.setPen(QPen(QColor(T.BORDER_STRONG), 1.2))
    p.setBrush(QColor(T.TABLE_FACE[0]))
    p.drawRect(QRectF(x0 + 10, SEAT_Y + 20, 16, FLOOR_Y - SEAT_Y - 20))      # back leg
    p.drawRect(QRectF(x1 - 30, SEAT_Y + 20, 16, FLOOR_Y - SEAT_Y - 20))      # front leg
    p.drawRoundedRect(QRectF(x0 - 22, 150, 24, SEAT_Y - 150 + 20), 6, 6)     # backrest
    top = QLinearGradient(0, SEAT_Y, 0, SEAT_Y + 22)
    top.setColorAt(0, QColor(T.TABLE_TOP[0]))
    top.setColorAt(1, QColor(T.TABLE_TOP[1]))
    p.setBrush(top)
    p.drawRoundedRect(QRectF(x0 - 22, SEAT_Y, x1 - x0 + 22, 22), 4, 4)
    p.setPen(QPen(QColor(T.BORDER_STRONG), 1.5))
    p.drawLine(QPointF(0, FLOOR_Y), QPointF(1000, FLOOR_Y))


def _draw_torso(p: QPainter, x0: float, x1: float, y_hip: float):
    """Trunk rising from the hips, fading out upward like the upper arm."""
    g = QLinearGradient(x0, 0, x1, 0)
    for pos, c in ((0.0, SKIN_LO), (0.5, SKIN), (1.0, SKIN_LO)):
        g.setColorAt(pos, QColor(c).darker(112))
    p.setBrush(g)
    p.setPen(QPen(SKIN_LINE, 1.2))
    p.drawRoundedRect(QRectF(x0, 60, x1 - x0, y_hip - 60 + 30), 40, 40)
    fade = QLinearGradient(0, y_hip - 40, 0, 120)
    fade.setColorAt(0, T.qcolor(T.SURFACE, 0))
    fade.setColorAt(1, T.qcolor(T.SURFACE, 255))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fade)
    p.drawRect(QRectF(x0 - 10, 40, x1 - x0 + 20, y_hip - 80))


def _draw_skin(p: QPainter, path: QPainterPath, y0: float, y1: float, ghost: bool = False):
    if ghost:
        p.setBrush(T.qcolor(T.ACCENT, 22))
        p.setPen(QPen(T.qcolor(T.ACCENT, 150), 1.6, Qt.PenStyle.DashLine))
    else:
        p.setBrush(_skin_gradient(y0, y1))
        p.setPen(QPen(SKIN_LINE, 1.3))
    p.drawPath(path)


def _spindle(x0, y0, x1, y1, bulge) -> QPainterPath:
    """Muscle belly between two points, bulging by `bulge` above and below the line."""
    p = QPainterPath()
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    p.moveTo(x0, y0)
    p.cubicTo(mx - (x1 - x0) * 0.2, my - bulge, mx + (x1 - x0) * 0.2, my - bulge, x1, y1)
    p.cubicTo(mx + (x1 - x0) * 0.2, my + bulge, mx - (x1 - x0) * 0.2, my + bulge, x0, y0)
    p.closeSubpath()
    return p


class KneeView(ArmView):
    """Seated knee straightening: thigh on the chair, shin + foot rotating at the knee.
    theta = 0 with the shin hanging straight down; + = knee straightening."""

    PIVOT = QPointF(520.0, 305.0)
    BASE_ANGLE = -90.0
    CAPTION_TOP = 18.0
    TARGET_LABEL = "below"         # above would collide with the muscle bar's "target" mark
    LEADER = ((330, 262), (352, 214))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dumbbell = self.weight = False
        self.angle_label, self.muscle_label = "Knee angle", "Thigh muscle"

    @staticmethod
    def _thigh() -> QPainterPath:
        p = QPainterPath()
        p.moveTo(150, SEAT_Y)
        p.lineTo(520, SEAT_Y + 1)
        p.cubicTo(552, SEAT_Y + 1, 556, 277, 520, 277)          # front of the knee
        p.cubicTo(440, 270, 300, 248, 200, 250)                  # top of the thigh
        p.cubicTo(160, 250, 140, 290, 150, SEAT_Y)
        p.closeSubpath()
        return p

    MUSCLE = _spindle(232, 265, 482, 284, 15)

    def _paint_static(self, p: QPainter):
        _draw_chair(p, 130, 480)
        _draw_torso(p, 140, 270, 300)
        _draw_skin(p, self._thigh(), 248, SEAT_Y)

    def _draw_limb(self, p: QPainter, theta: float, ghost: bool):
        p.save()
        p.translate(self.PIVOT)
        p.rotate(-(theta + self.BASE_ANGLE))
        _draw_skin(p, SHIN, -100, 40, ghost)
        if not ghost:       # knee cap over the joint so the shin never shows a seam
            _draw_skin(p, _circle(QPointF(0, 0), KNEE_R), -KNEE_R, KNEE_R)
        p.restore()


def _circle(c: QPointF, r: float) -> QPainterPath:
    p = QPainterPath()
    p.addEllipse(c, r, r)
    return p


class HipView(KneeView):
    """Seated knee lift (marching): the thigh rotates up at the hip; the shin hangs down from
    the knee. theta = 0 with the thigh resting on the chair; + = knee lifting."""

    PIVOT = QPointF(200.0, 300.0)
    BASE_ANGLE = 0.0
    THIGH_LEN = 330.0
    TARGET_LABEL = "right"         # above the line would sit under the demonstration caption

    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle_label = "Hip angle"

    @classmethod
    def _thigh_local(cls) -> QPainterPath:
        L = cls.THIGH_LEN
        p = QPainterPath()
        p.moveTo(-40, 32)
        p.lineTo(L, 29)
        p.cubicTo(L + 32, 29, L + 32, -29, L, -29)               # front of the knee
        p.cubicTo(L - 80, -32, 120, -52, 10, -50)                # top of the thigh
        p.cubicTo(-30, -48, -56, 0, -40, 32)
        p.closeSubpath()
        return p

    MUSCLE_LOCAL = _spindle(42, -36, 292, -17, 15)

    def _knee(self, theta: float) -> QPointF:
        a = math.radians(theta)
        return QPointF(self.PIVOT.x() + self.THIGH_LEN * math.cos(a), self.PIVOT.y() - self.THIGH_LEN * math.sin(a))

    def _paint_static(self, p: QPainter):
        _draw_chair(p, 110, 470)
        _draw_torso(p, 120, 250, 290)

    def _flexors(self, p, theta: float):
        pass        # the muscle moves with the thigh, so it is drawn in _draw_limb

    def _flexor_label(self, p):
        pass        # the muscle moves with the thigh, so a fixed label would point at nothing

    def _draw_limb(self, p: QPainter, theta: float, ghost: bool):
        knee = self._knee(theta)
        p.save()                    # shin hangs straight down from the knee
        p.translate(knee)
        p.rotate(90)
        _draw_skin(p, SHIN, -100, 40, ghost)
        p.restore()
        p.save()
        p.translate(self.PIVOT)
        p.rotate(-theta)
        _draw_skin(p, self._thigh_local(), -52, 32, ghost)
        if not ghost:
            self._muscle_brush(p)
            p.drawPath(self.MUSCLE_LOCAL)
            _draw_skin(p, _circle(QPointF(self.THIGH_LEN, 0), KNEE_R), -KNEE_R, KNEE_R)
        p.restore()


# ------------------------------------------------------------------ arm-routine views (whole arm)

SHIRT = ("#a9bed3", "#7f9ab5", "#62809c")       # light, mid, shadow
TROUSERS = ("#7a8a9d", "#5f6f83", "#4a596b")
CLOTH_LINE = QColor(60, 80, 100, 170)


def _limb(L: float, r0: float, r1: float, belly: float = 0.0) -> QPainterPath:
    """Tapered limb segment in local units: origin at the proximal joint, +x to the distal end,
    half-width r0 -> r1, rounded ends, a muscle belly of `belly` on the -y side."""
    p = QPainterPath()
    p.moveTo(0, -r0)
    p.cubicTo(L * 0.35, -r0 - belly, L * 0.7, -r1, L, -r1)
    p.arcTo(QRectF(L - r1, -r1, 2 * r1, 2 * r1), 90, -180)
    p.cubicTo(L * 0.7, r1, L * 0.35, r0 + belly * 0.4, 0, r0)
    p.arcTo(QRectF(-r0, -r0, 2 * r0, 2 * r0), 270, -180)
    p.closeSubpath()
    return p


def _draw_cloth(p: QPainter, path: QPainterPath, y0: float, y1: float, cols=SHIRT):
    g = QLinearGradient(0, y0, 0, y1)
    for pos, c in zip((0.0, 0.5, 1.0), cols):
        g.setColorAt(pos, QColor(c))
    p.setBrush(g)
    p.setPen(QPen(CLOTH_LINE, 1.2))
    p.drawPath(path)


def _fade_top(p: QPainter, x0: float, x1: float, y_top: float, y_clear: float):
    """Dissolves everything above y_clear into the background (opaque at y_top)."""
    fade = QLinearGradient(0, y_clear, 0, y_top)
    fade.setColorAt(0, T.qcolor(T.SURFACE, 0))
    fade.setColorAt(1, T.qcolor(T.SURFACE, 255))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fade)
    p.drawRect(QRectF(x0, y_top - 40, x1 - x0, y_clear - y_top + 40))


def _draw_hand_at(p: QPainter, x: float, scale: float, gripping: bool, ghost: bool, plate: bool = False):
    """Hand at local x along a limb whose +x points to the fingers."""
    p.save()
    p.translate(x, 0)
    p.scale(scale, scale)
    draw_hand_local(p, gripping, ghost, plate=plate)
    p.restore()


BONE = ("#f8efdc", "#e8d6b3", "#c4a983")
BONE_LINE = QColor(140, 112, 78, 200)


def _paint_muscle(p: QPainter, path: QPainterPath, k: float, along_y: bool = True):
    """Anatomical muscle: red with cylindrical shading and fibre lines; k (0..1) = how hard it
    works, from a dull relaxed red to a bright, glowing contracted red."""
    k = max(0.0, min(1.0, k))
    relaxed, active = QColor("#b9776c"), QColor("#d8362c")
    base = QColor(int(relaxed.red() + (active.red() - relaxed.red()) * k),
                  int(relaxed.green() + (active.green() - relaxed.green()) * k),
                  int(relaxed.blue() + (active.blue() - relaxed.blue()) * k))
    r = path.boundingRect()
    g = (QLinearGradient(r.left(), 0, r.right(), 0) if along_y else QLinearGradient(0, r.top(), 0, r.bottom()))
    g.setColorAt(0.0, base.darker(150))
    g.setColorAt(0.42, base.lighter(128))
    g.setColorAt(1.0, base.darker(165))
    if k > 0.6:                                     # contracted: soft glow around the belly
        p.setPen(QPen(QColor(230, 60, 45, int(150 * (k - 0.6) / 0.4)), 7))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.setBrush(g)
    p.setPen(QPen(base.darker(190), 1.2))
    p.drawPath(path)
    p.save()
    p.setClipPath(path)
    p.setPen(QPen(QColor(255, 235, 230, 55), 1.0))
    n = 7
    for i in range(1, n):
        f = i / n
        fibre = QPainterPath()
        if along_y:
            cx, x = r.center().x(), r.left() + r.width() * f
            fibre.moveTo(cx + (x - cx) * 0.25, r.top())
            fibre.quadTo(x, r.center().y(), cx + (x - cx) * 0.25, r.bottom())
        else:
            cy, y = r.center().y(), r.top() + r.height() * f
            fibre.moveTo(r.left(), cy + (y - cy) * 0.25)
            fibre.quadTo(r.center().x(), y, r.right(), cy + (y - cy) * 0.25)
        p.drawPath(fibre)
    p.restore()


def _paint_bone(p: QPainter, path: QPainterPath):
    r = path.boundingRect()
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    for pos, c in zip((0.0, 0.5, 1.0), BONE):
        g.setColorAt(pos, QColor(c))
    p.setBrush(g)
    p.setPen(QPen(BONE_LINE, 1.3))
    p.drawPath(path)


def _effort(env_pct: float, default: float = 0.35) -> float:
    """Muscle colour level 0..1 from %RVC (a mid level when there is no reading, e.g. demos)."""
    return default if env_pct != env_pct else max(0.0, min(1.0, env_pct / 100.0))


class _WholeArmView(ArmView):
    """Shared set-up for the routine views: no wrist-flexor overlay of the wrist-curl scene."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dumbbell = self.weight = False

    def _flexors(self, p, theta: float):
        pass

    def _flexor_label(self, p):
        pass


class CurlView(_WholeArmView):
    """Bicep curl, anatomical side view facing right: a muscular upper arm still at the side
    (biceps in red, brighter as it works), the forearm turning at the elbow to a fist.
    theta = 0 with the arm hanging straight down; + = elbow bending."""

    PIVOT = QPointF(450.0, 270.0)
    BASE_ANGLE = -90.0
    CAPTION_TOP = 18.0
    TARGET_LABEL = "below"                 # above, it would sit on the fist at the target
    LEADER = ((452, 150), (330, 190))      # to the left: the forearm sweeps the right side
    BAR_DELTA = True
    FOREARM_L = 185.0
    FOREARM = _limb(185.0, 29, 18, belly=8)
    BRACHIORADIALIS = _spindle(14, -14, 128, -10, 11)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.weight = True
        self.dumbbell = True
        self.angle_label, self.muscle_label = "Elbow angle", "Elbow flexors"

    @staticmethod
    def _upper_arm() -> QPainterPath:
        p = QPainterPath()
        p.moveTo(404, -20)
        p.cubicTo(398, 100, 410, 220, 422, 270)
        p.quadTo(450, 306, 478, 270)
        p.cubicTo(494, 220, 504, 100, 496, -20)
        p.closeSubpath()
        return p

    @staticmethod
    def _biceps() -> QPainterPath:
        p = QPainterPath()
        p.moveTo(468, 66)       # below the faded shoulder: it is drawn every frame, unfaded
        p.cubicTo(510, 116, 512, 200, 474, 256)
        p.cubicTo(446, 204, 440, 120, 468, 66)
        p.closeSubpath()
        return p

    @staticmethod
    def _triceps() -> QPainterPath:
        p = QPainterPath()
        p.moveTo(420, 20)
        p.cubicTo(398, 100, 402, 200, 428, 254)
        p.cubicTo(446, 196, 446, 100, 420, 20)
        p.closeSubpath()
        return p

    MUSCLE = _biceps.__func__()

    def _paint_static(self, p: QPainter):
        path = QPainterPath()
        path.addRoundedRect(QRectF(340, -40, 220, 700), 60, 60)
        _draw_cloth(p, path, 0, 600)
        _draw_skin(p, self._upper_arm(), 0, 300)
        _paint_muscle(p, self._triceps(), 0.15)
        _fade_top(p, 320, 580, -20, 80)

    def _flexors(self, p, theta: float):
        _paint_muscle(p, self.MUSCLE, _effort(self.env_pct))

    def _flexor_label(self, p):
        (x0, y0), (x1, y1) = self.LEADER
        p.setPen(QPen(QColor(T.MUTED), 1))
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        p.setFont(self._font(12, caps=True))
        p.drawText(QRectF(x1 - 404, y1 - 58, 400, 60), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                   self.muscle_label)

    def _draw_limb(self, p: QPainter, theta: float, ghost: bool):
        p.save()
        p.translate(self.PIVOT)
        p.rotate(-(theta + self.BASE_ANGLE))
        _draw_skin(p, self.FOREARM, -29, 29, ghost)
        _draw_hand_at(p, self.FOREARM_L - 6, 0.62, self.dumbbell, ghost,
                      plate=self.weight and self.load_kg > 0)
        if not ghost:
            # Brachioradialis on the thumb side helps the biceps bend the elbow.
            _paint_muscle(p, self.BRACHIORADIALIS, 0.8 * _effort(self.env_pct), along_y=False)
            _draw_skin(p, _circle(QPointF(0, 0), 28), -28, 28)
        p.restore()


class ScapulaView(_WholeArmView):
    """Shoulder blade squeeze: side view facing left with the arm hanging at the side (it moves
    back a little as the blades squeeze), plus a view from behind of the two shoulder blades
    drawing together. theta = 0 with the arm straight down; + = arm moving back."""

    PIVOT = QPointF(355.0, 150.0)
    BASE_ANGLE = -90.0
    CAPTION_TOP = 18.0             # the caption ends above the back view (INSET starts at y 112)
    TARGET_LABEL = "right"
    ARM_L = 300.0
    ARM = _limb(300.0, 30, 18, belly=5)
    DELTOID = _limb(105.0, 42, 30, belly=6)
    ARC_R = 230.0
    BAND = QRectF(240, -30, 230, 660)
    INSET = QRectF(600, 112, 380, 470)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_bar = False         # the forearm sensor does not see the shoulder-blade muscles
        self.angle_label, self.muscle_label = "Arm angle", "Shoulder blades"

    def _text(self, rect: QRectF, text: str, size=13, color=T.TEXT, align=Qt.AlignmentFlag.AlignCenter,
              bold=True, caps=True):
        p = self._p
        p.setPen(QColor(color))
        p.setFont(self._font(size, T.XS, bold=bold, caps=caps))
        p.drawText(rect, align, text)

    def _paint_static(self, p: QPainter):
        # Body band behind the arm (the trunk seen from the side), as in an anatomy plate.
        g = QLinearGradient(0, 0, 0, 620)
        g.setColorAt(0, T.qcolor("#4f6f96", 210))
        g.setColorAt(1, T.qcolor("#7d97b5", 170))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(self.BAND, 18, 18)
        _fade_top(p, 230, 480, -30, 60)
        r = self.INSET
        p.setPen(QPen(QColor(T.BORDER), 1.2))
        p.setBrush(QColor(T.BG))
        p.drawRoundedRect(r, 16, 16)
        if not getattr(self, "compact", False):
            self._p = p
            self._text(QRectF(r.left(), r.bottom() - 54, r.width(), 40), "From behind", 14, T.MUTED,
                       bold=False)
            self._text(QRectF(20, 270, 210, 90), "Anterior\n(front)", 13,
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._text(QRectF(480, 190, 220, 90), "Posterior\n(back)", 13,
                       align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            b = self.BAND
            self._text(QRectF(b.left(), 542, b.width(), 70), "Lateral view\n(left arm)", 12, caps=False)

    def _target_zone(self, p):
        """Green sector from straight down to the target, with the dashed rest line."""
        r = 300
        rect = QRectF(self.PIVOT.x() - r, self.PIVOT.y() - r, 2 * r, 2 * r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.qcolor(T.TARGET, 46))
        p.drawPie(rect, int(self.BASE_ANGLE * 16), int(self.target * 16))
        a = math.radians(self.target + self.BASE_ANGLE)
        end = QPointF(self.PIVOT.x() + r * math.cos(a), self.PIVOT.y() - r * math.sin(a))
        p.setPen(QPen(T.qcolor(T.TARGET, 190), 1.6, Qt.PenStyle.DashLine))
        p.drawLine(self.PIVOT, end)
        p.drawLine(self.PIVOT, QPointF(self.PIVOT.x(), self.PIVOT.y() + r))
        p.setPen(QPen(T.qcolor(T.TARGET, 140), 1.4))
        p.drawArc(rect, int(self.BASE_ANGLE * 16), int(self.target * 16))

    def _shoulder_bones(self, p: QPainter):
        """Clavicle, acromion and humeral head over the top of the arm."""
        c = self.PIVOT
        clav = QPainterPath()
        clav.moveTo(c.x() - 66, c.y() - 58)
        clav.cubicTo(c.x() - 30, c.y() - 70, c.x() + 10, c.y() - 54, c.x() + 40, c.y() - 46)
        clav.cubicTo(c.x() + 46, c.y() - 36, c.x() + 40, c.y() - 30, c.x() + 30, c.y() - 32)
        clav.cubicTo(c.x() + 4, c.y() - 40, c.x() - 30, c.y() - 52, c.x() - 64, c.y() - 44)
        clav.closeSubpath()
        _paint_bone(p, clav)
        head = QPainterPath()
        head.addEllipse(QPointF(c.x() + 2, c.y() - 8), 30, 28)
        _paint_bone(p, head)
        acro = QPainterPath()
        acro.moveTo(c.x() - 34, c.y() - 40)
        acro.quadTo(c.x() + 8, c.y() - 62, c.x() + 38, c.y() - 34)
        acro.quadTo(c.x() + 6, c.y() - 44, c.x() - 34, c.y() - 30)
        acro.closeSubpath()
        _paint_bone(p, acro)

    def _draw_limb(self, p: QPainter, theta: float, ghost: bool):
        p.save()
        p.translate(self.PIVOT)
        p.rotate(-(theta + self.BASE_ANGLE))
        _draw_skin(p, self.ARM, -30, 30, ghost)
        _draw_hand_at(p, self.ARM_L - 6, 0.5, False, ghost)
        if not ghost:
            _draw_skin(p, self.DELTOID, -42, 42)
            crease = QPainterPath()                  # elbow crease
            crease.moveTo(self.ARM_L * 0.55, -18)
            crease.quadTo(self.ARM_L * 0.56, 0, self.ARM_L * 0.55, 16)
            p.setPen(QPen(QColor(120, 78, 56, 110), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(crease)
        p.restore()
        if ghost:
            return
        self._shoulder_bones(p)
        self._angle_arc(p, theta)
        self._back_view(p, theta)

    def _angle_arc(self, p: QPainter, theta: float):
        """Arm axis with dots at both ends, and an arrowed arc from straight down to the arm."""
        c, r = self.PIVOT, self.ARC_R
        a = math.radians(theta + self.BASE_ANGLE)
        tip = QPointF(c.x() + r * math.cos(a), c.y() - r * math.sin(a))
        green = QColor(T.TARGET)
        p.setPen(QPen(green, 2, Qt.PenStyle.DotLine))
        p.drawLine(c, tip)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(green)
        for pt in (c, tip, QPointF(c.x(), c.y() + r)):
            p.drawEllipse(pt, 6, 6)
        if theta > 1.5:
            rect = QRectF(c.x() - r * 0.62, c.y() - r * 0.62, 2 * r * 0.62, 2 * r * 0.62)
            pen = QPen(green, 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(rect, int(self.BASE_ANGLE * 16), int(theta * 16))
            e = math.radians(theta + self.BASE_ANGLE)
            ep = QPointF(c.x() + r * 0.62 * math.cos(e), c.y() - r * 0.62 * math.sin(e))
            d = QPointF(-math.sin(e), -math.cos(e))           # arc direction (counter-clockwise)
            n = QPointF(math.cos(e), -math.sin(e))
            p.drawLine(ep, ep - d * 10 + n * 6)
            p.drawLine(ep, ep - d * 10 - n * 6)
            m = math.radians(theta / 2 + self.BASE_ANGLE)
            lp = QPointF(c.x() + r * 0.62 * math.cos(m), c.y() - r * 0.62 * math.sin(m))
            p.setPen(green)
            p.setFont(self._font(15, T.S, bold=True, tabular=True))
            p.drawText(QRectF(lp.x() + 8, lp.y() - 30, 120, 40),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{theta:.0f}°")

    def _back_view(self, p: QPainter, theta: float):
        """Muscular upper back from behind: the blades slide toward the spine and the muscles
        between them (blue) light up as theta nears the target."""
        k = max(0.0, min(1.0, theta / max(self.target, 1.0)))
        cx, top = self.INSET.center().x(), self.INSET.top()
        p.setPen(QPen(SKIN_LINE, 1.2))
        p.setBrush(_skin_gradient(top + 20, top + 110))
        p.drawRoundedRect(QRectF(cx - 22, top + 70, 44, 50), 10, 10)           # neck
        p.drawEllipse(QPointF(cx, top + 54), 34, 38)                             # head
        y_sh, y_w = top + 128, top + 380
        back = QPainterPath()
        back.moveTo(cx - 150, y_sh + 40)
        back.cubicTo(cx - 150, y_sh + 6, cx - 110, y_sh - 6, cx - 40, y_sh - 10)
        back.lineTo(cx + 40, y_sh - 10)
        back.cubicTo(cx + 110, y_sh - 6, cx + 150, y_sh + 6, cx + 150, y_sh + 40)
        back.cubicTo(cx + 138, y_sh + 150, cx + 104, y_w - 60, cx + 96, y_w)
        back.lineTo(cx - 96, y_w)
        back.cubicTo(cx - 104, y_w - 60, cx - 138, y_sh + 150, cx - 150, y_sh + 40)
        back.closeSubpath()
        _paint_muscle(p, back, 0.25)
        trap = QPainterPath()                                                     # trapezius
        trap.moveTo(cx, y_sh - 40)
        trap.cubicTo(cx - 40, y_sh - 6, cx - 110, y_sh + 2, cx - 128, y_sh + 24)
        trap.lineTo(cx, y_sh + 210)
        trap.lineTo(cx + 128, y_sh + 24)
        trap.cubicTo(cx + 110, y_sh + 2, cx + 40, y_sh - 6, cx, y_sh - 40)
        trap.closeSubpath()
        _paint_muscle(p, trap, 0.4 + 0.4 * k)
        for side in (-1, 1):                                                      # deltoids
            d = QPainterPath()
            d.addEllipse(QPointF(cx + side * 140, y_sh + 34), 30, 44)
            _paint_muscle(p, d, 0.3)
        p.setPen(QPen(QColor(250, 240, 232, 150), 2.2, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(cx, y_sh - 30), QPointF(cx, y_w - 6))                   # spine
        gap = 50 - 30 * k
        p.setPen(QPen(T.qcolor("#24558f", 200), 1.4))
        p.setBrush(T.qcolor("#3f7fc4", int(120 + 100 * k)))
        p.drawRoundedRect(QRectF(cx - gap - 4, y_sh + 26, 2 * gap + 8, 116), 6, 6)  # rhomboids
        for side in (-1, 1):
            blade = QPainterPath()
            x_in = cx + side * gap
            blade.moveTo(x_in, y_sh + 18)
            blade.quadTo(cx + side * (gap + 50), y_sh + 4, cx + side * (gap + 96), y_sh + 16)
            blade.quadTo(cx + side * (gap + 70), y_sh + 80, x_in + side * 10, y_sh + 164)
            blade.quadTo(x_in - side * 4, y_sh + 90, x_in, y_sh + 18)
            blade.closeSubpath()
            _paint_bone(p, blade)
            ridge = QPainterPath()                                                # spine of the scapula
            ridge.moveTo(x_in + side * 4, y_sh + 44)
            ridge.quadTo(cx + side * (gap + 50), y_sh + 30, cx + side * (gap + 92), y_sh + 20)
            p.setPen(QPen(BONE_LINE, 2.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(ridge)


class PendulumView(_WholeArmView):
    """Pendulum swing: standing bent forward, facing right, the other hand on a table and the
    working arm hanging from the shoulder. theta = 0 hanging straight down; + = swung forward."""

    PIVOT = QPointF(560.0, 215.0)
    BASE_ANGLE = -90.0
    CAPTION_TOP = 18.0
    TARGET_LABEL = "right"
    ARM_L = 240.0
    ARM = _limb(240.0, 26, 17, belly=3)
    SLEEVE = _limb(80.0, 32, 29)
    SHOULDER = QPointF(585.0, 205.0)
    HIP = QPointF(300.0, 240.0)
    TABLE_X0, TABLE_X1, TABLE_Y = 690.0, 980.0, 330.0
    SHIRT = ("#eef0f3", "#c9ced5", "#a3abb5")
    TROUSERS = ("#565b63", "#3d4148", "#2a2d33")
    GLOW = "#3cc7e0"
    OVERSWING_DEG = 10.0           # warn this far past the target (as the OVER_FLEX margin)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_bar = False         # a relaxed swing: there is no muscle target to show
        self.angle_label, self.muscle_label = "Swing angle", "Shoulder"

    @staticmethod
    def _segment(p: QPainter, a: QPointF, b: QPointF, path_fn, draw):
        """Draws path_fn(length) from a to b (local +x along a -> b)."""
        dx, dy = b.x() - a.x(), b.y() - a.y()
        p.save()
        p.translate(a)
        p.rotate(math.degrees(math.atan2(dy, dx)))
        draw(path_fn(math.hypot(dx, dy)))
        p.restore()

    def _paint_static(self, p: QPainter):
        p.setPen(QPen(QColor(T.BORDER_STRONG), 1.5))
        p.drawLine(QPointF(0, FLOOR_Y + 10), QPointF(1000, FLOOR_Y + 10))
        # Table: legs, then the top in the arm-view table colours.
        x0, x1, ty = self.TABLE_X0, self.TABLE_X1, self.TABLE_Y
        p.setPen(QPen(QColor(T.BORDER_STRONG), 1.2))
        p.setBrush(QColor(T.TABLE_FACE[0]))
        for x in (x0 + 16, x1 - 32):
            p.drawRect(QRectF(x, ty + 14, 16, FLOOR_Y + 10 - ty - 14))
        top = QLinearGradient(0, ty, 0, ty + 14)
        top.setColorAt(0, QColor(T.TABLE_TOP[0]))
        top.setColorAt(1, QColor(T.TABLE_TOP[1]))
        p.setBrush(top)
        p.drawRoundedRect(QRectF(x0, ty, x1 - x0, 14), 3, 3)
        # Legs (far one first, darker) and shoes.
        hip = self.HIP
        tr = self.TROUSERS
        for foot, cols in ((QPointF(270, 560), (tr[1], tr[2], tr[2])), (QPointF(318, 560), tr)):
            self._segment(p, hip, foot, lambda L: _limb(L, 42, 26),
                          lambda path, c=cols: _draw_cloth(p, path, -42, 42, c))
            p.setPen(QPen(QColor(20, 22, 26), 1.2))
            p.setBrush(QColor("#22252a"))
            p.drawRoundedRect(QRectF(foot.x() - 26, foot.y() - 6, 74, 24), 10, 10)
        # Supporting (far) arm to the table, slightly darker.
        hand = QPointF(740, ty - 6)
        p.setOpacity(0.88)
        self._segment(p, self.SHOULDER, hand, lambda L: _limb(L, 24, 16),
                      lambda path: _draw_skin(p, path, -24, 24))
        p.save()
        p.translate(hand)
        p.scale(0.45, 0.45)
        draw_hand_local(p, False)
        p.restore()
        p.setOpacity(1.0)
        # Trunk bent forward from the hips, neck and head.
        self._segment(p, hip, self.SHOULDER, lambda L: _limb(L, 58, 52),
                      lambda path: _draw_cloth(p, path, -58, 58, self.SHIRT))
        p.setPen(QPen(SKIN_LINE, 1.2))
        p.setBrush(_skin_gradient(150, 220))
        p.drawRoundedRect(QRectF(612, 176, 40, 34), 10, 10)
        p.drawEllipse(QPointF(668, 176), 40, 38)
        p.drawEllipse(QPointF(660, 182), 7, 10)                       # ear
        # Body line: hip to shoulder, dotted.
        p.setPen(QPen(T.qcolor(self.GLOW, 200), 2.2, Qt.PenStyle.DotLine))
        p.drawLine(hip, self.PIVOT)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(QColor(self.GLOW))
        p.drawEllipse(hip, 7, 7)

    def _target_zone(self, p):
        """Soft cyan swing area from the vertical forward to the target."""
        r = self.ARM_L + 60
        rect = QRectF(self.PIVOT.x() - r, self.PIVOT.y() - r, 2 * r, 2 * r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.qcolor(T.TARGET, 34))
        p.drawPie(rect, int(self.BASE_ANGLE * 16), int(self.target * 16))
        a = math.radians(self.target + self.BASE_ANGLE)
        end = QPointF(self.PIVOT.x() + r * math.cos(a), self.PIVOT.y() - r * math.sin(a))
        p.setPen(QPen(T.qcolor(T.TARGET, 170), 1.6, Qt.PenStyle.DashLine))
        p.drawLine(self.PIVOT, end)

    def _draw_limb(self, p: QPainter, theta: float, ghost: bool):
        if not ghost:
            self._swing_glow(p, theta)
        p.save()
        p.translate(self.PIVOT)
        p.rotate(-(theta + self.BASE_ANGLE))
        _draw_skin(p, self.ARM, -26, 26, ghost)
        _draw_hand_at(p, self.ARM_L - 6, 0.45, False, ghost)
        if not ghost:
            _draw_cloth(p, self.SLEEVE, -32, 32, self.SHIRT)
            p.setPen(QPen(QColor(255, 255, 255, 230), 2.2, Qt.PenStyle.DotLine))     # pendulum line
            p.drawLine(QPointF(0, 0), QPointF(self.ARM_L + 50, 0))
        p.restore()
        if ghost:
            return
        c = self.PIVOT
        p.setPen(QPen(QColor(self.GLOW), 3))                            # shoulder joint ring
        p.setBrush(T.qcolor("#ffffff", 200))
        p.drawEllipse(c, 20, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.GLOW))
        p.drawEllipse(c, 6, 6)
        a = math.radians(theta + self.BASE_ANGLE)
        r = self.ARM_L + 50
        hand = QPointF(c.x() + r * math.cos(a), c.y() - r * math.sin(a))
        p.setPen(QColor(T.TEXT))
        p.setFont(self._font(15, T.S, tabular=True))
        p.drawText(QRectF(hand.x() - 150, hand.y() - 34, 120, 40),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{theta:.0f}°")
        if not self.demo and theta > self.target + self.OVERSWING_DEG:
            self._pill(p, QPointF(hand.x() - 270, hand.y() + 14), "Over-swing detected")

    def _swing_glow(self, p: QPainter, theta: float):
        """Glowing arc the hand travels along, brightest near the arm."""
        c = self.PIVOT
        r = self.ARM_L + 50
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        start = self.BASE_ANGLE - 55
        span = theta + 55
        for width, alpha in ((16, 40), (8, 80), (3, 200)):
            pen = QPen(T.qcolor(self.GLOW, alpha), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(rect, int(start * 16), int(span * 16))

    def _pill(self, p: QPainter, at: QPointF, text: str):
        font = self._font(12, T.XS, bold=True)
        w = QFontMetricsF(font).horizontalAdvance(text) + 28
        h = QFontMetricsF(font).height() + 12
        box = QRectF(at.x(), at.y(), w, h)
        p.setPen(QPen(T.qcolor(T.WARN, 200), 1.2))
        p.setBrush(T.qcolor(T.SURFACE, 235))
        p.drawRoundedRect(box, h / 2, h / 2)
        p.setPen(QColor(T.WARN))
        p.setFont(font)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


# ------------------------------------------------------------------ leg-routine views

MAT_Y = 470.0           # top of the exercise mat (lying exercises)


def _fade_left(p: QPainter, x_left: float, x_clear: float, y0: float, y1: float):
    """Dissolves everything left of x_clear into the background (opaque at x_left)."""
    fade = QLinearGradient(x_clear, 0, x_left, 0)
    fade.setColorAt(0, T.qcolor(T.SURFACE, 0))
    fade.setColorAt(1, T.qcolor(T.SURFACE, 255))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fade)
    # Reaches far left: on wide cards the scene is centred and its left margin is visible.
    p.drawRect(QRectF(x_left - 600, y0, x_clear - x_left + 600, y1 - y0))


def _draw_mat(p: QPainter):
    p.setPen(QPen(QColor(T.BORDER_STRONG), 1.2))
    p.setBrush(QColor("#dfe9f2"))
    p.drawRoundedRect(QRectF(-20, MAT_Y, 1040, 22), 8, 8)


def _draw_lying_torso(p: QPainter, hip: QPointF):
    """Trunk lying on its back to the left of the hip, fading out toward the head."""
    path = QPainterPath()
    path.addRoundedRect(QRectF(-80, hip.y() - 66, hip.x() + 120, 132), 60, 60)
    _draw_cloth(p, path, hip.y() - 66, hip.y() + 66)
    _fade_left(p, 0, 200, hip.y() - 90, MAT_Y)


def _segment_between(p: QPainter, a: QPointF, b: QPointF, path_fn, draw):
    PendulumView._segment(p, a, b, path_fn, draw)


def _draw_foot_up(p: QPainter, ankle: QPointF, tilt: float, ghost: bool = False):
    """Foot standing up from the heel (lying on the back), toes tilted `tilt` deg toward the head."""
    foot = QPainterPath()
    foot.moveTo(-22, 10)
    foot.cubicTo(-30, -30, -26, -70, -10, -96)       # sole side up to the toes
    foot.quadTo(2, -104, 10, -92)
    foot.cubicTo(14, -60, 20, -30, 22, 0)            # top of the foot down to the ankle
    foot.quadTo(10, 18, -22, 10)
    foot.closeSubpath()
    p.save()
    p.translate(ankle)
    p.rotate(-tilt)
    _draw_skin(p, foot, -100, 20, ghost)
    p.restore()


class QuadSetView(ArmView):
    """Quad set, lying on the back: the leg stays straight; tightening the thigh presses the
    knee down into a rolled towel and pulls the toes up. Driven by muscle effort (0..1 pose)."""

    UNIT = "%"
    POSE_TARGET = 1.0
    CAPTION_TOP = 18.0
    HIP = QPointF(300.0, 402.0)
    KNEE = QPointF(545.0, 410.0)
    ANKLE = QPointF(760.0, 414.0)
    LEADER = ((430, 366), (460, 300))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dumbbell = self.weight = False
        self.angle_label, self.muscle_label = "Muscle effort", "Thigh muscle"

    def _paint_static(self, p: QPainter):
        _draw_mat(p)
        _draw_lying_torso(p, self.HIP)
        p.setPen(QPen(QColor(T.BORDER_STRONG), 1.2))           # rolled towel under the knee
        p.setBrush(QColor("#f3efe6"))
        p.drawEllipse(QPointF(self.KNEE.x(), MAT_Y - 20), 22, 20)

    def _target_zone(self, p):
        pass

    def _flexors(self, p, theta: float):
        pass        # drawn with the thigh, which moves

    def _draw_limb(self, p: QPainter, k: float, ghost: bool):
        if ghost:
            return          # the leg barely moves: the effort bar and caption guide instead
        k = max(0.0, min(1.15, k))
        knee = QPointF(self.KNEE.x(), self.KNEE.y() + 7 * k)
        _draw_foot_up(p, self.ANKLE, 14 * k)
        _segment_between(p, knee, self.ANKLE, lambda L: _limb(L, 28, 18, belly=4),
                         lambda path: _draw_skin(p, path, -28, 28))
        thigh_len = [0.0]

        def thigh(path):
            _draw_skin(p, path, -46, 46)
            self._muscle_brush(p)
            p.drawPath(_spindle(90, -28, thigh_len[0] - 40, -18, 13))
            _draw_cloth(p, _limb(90, 50, 46), -50, 50, TROUSERS)

        def thigh_path(L):
            thigh_len[0] = L
            return _limb(L, 46, 30)
        _segment_between(p, self.HIP, knee, thigh_path, thigh)
        _draw_skin(p, _circle(knee, 26), knee.y() - 26, knee.y() + 26)
        if k > 0.1:         # "press the knee down"
            pen = QPen(T.qcolor(T.ACCENT, int(120 + 135 * min(1.0, k))), 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            x, y = knee.x(), knee.y() - 92
            p.drawLine(QPointF(x, y), QPointF(x, y + 40))
            p.drawLine(QPointF(x, y + 40), QPointF(x - 9, y + 30))
            p.drawLine(QPointF(x, y + 40), QPointF(x + 9, y + 30))


class LegRaiseView(ArmView):
    """Straight leg raise, lying on the back with the other knee bent: the straight leg
    rotates up at the hip. theta = 0 flat on the mat; + = lifting."""

    PIVOT = QPointF(300.0, 402.0)
    BASE_ANGLE = 0.0
    CAPTION_TOP = 18.0
    TARGET_LABEL = "above"
    THIGH_L, SHIN_L = 240.0, 210.0
    THIGH = _limb(240.0, 46, 30)
    SHIN = _limb(210.0, 28, 18, belly=4)
    MUSCLE_LOCAL = _spindle(60, -32, 200, -20, 13)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dumbbell = self.weight = False
        self.angle_label, self.muscle_label = "Leg angle", "Thigh muscle"

    def _paint_static(self, p: QPainter):
        _draw_mat(p)
        hip = self.PIVOT
        knee, foot = QPointF(440, 270), QPointF(560, 452)
        p.setOpacity(0.85)            # the bent leg is the far one
        _segment_between(p, knee, foot, lambda L: _limb(L, 28, 18),
                         lambda path: _draw_skin(p, path, -28, 28))
        _segment_between(p, QPointF(hip.x(), hip.y() - 10), knee, lambda L: _limb(L, 44, 30),
                         lambda path: _draw_skin(p, path, -44, 44))
        p.setPen(QPen(QColor(40, 45, 55), 1.2))
        p.setBrush(QColor("#c99a7c"))
        p.drawRoundedRect(QRectF(foot.x() - 20, MAT_Y - 20, 100, 22), 10, 10)
        p.setOpacity(1.0)
        _draw_lying_torso(p, hip)

    def _flexors(self, p, theta: float):
        pass        # drawn with the moving thigh

    def _flexor_label(self, p):
        pass

    def _draw_limb(self, p: QPainter, theta: float, ghost: bool):
        p.save()
        p.translate(self.PIVOT)
        p.rotate(-theta)
        p.save()
        p.translate(self.THIGH_L, 0)
        _draw_skin(p, self.SHIN, -28, 28, ghost)
        _draw_foot_up(p, QPointF(self.SHIN_L, 4), 0, ghost)
        p.restore()
        _draw_skin(p, self.THIGH, -46, 46, ghost)
        if not ghost:
            _draw_cloth(p, _limb(90, 50, 46), -50, 50, TROUSERS)
            self._muscle_brush(p)
            p.drawPath(self.MUSCLE_LOCAL)
            _draw_skin(p, _circle(QPointF(self.THIGH_L, 0), 26), -26, 26)
        p.restore()


class EmgKneeView(KneeView):
    """Seated knee extension drawn from the thigh muscle signal: reaching the effort target
    shows the leg straight, level with the floor."""

    UNIT = "%"
    POSE_TARGET = 85.0


class CalfRaiseView(ArmView):
    """Standing calf raise, facing a chair held for balance: the heels lift and the whole body
    rises on the balls of the feet. Driven by calf effort (0..1 pose)."""

    UNIT = "%"
    POSE_TARGET = 1.0
    CAPTION_TOP = 18.0
    TOE = QPointF(500.0, 556.0)
    FOOT_L = 120.0
    SHIN_L, THIGH_L = 170.0, 180.0
    CHAIR_X, CHAIR_TOP, SEAT_Y_ = 640.0, 236.0, 380.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dumbbell = self.weight = False
        self.angle_label, self.muscle_label = "Muscle effort", "Calf muscle"

    def _paint_static(self, p: QPainter):
        p.setPen(QPen(QColor(T.BORDER_STRONG), 1.5))
        p.drawLine(QPointF(0, self.TOE.y() + 4), QPointF(1000, self.TOE.y() + 4))
        x, top, seat = self.CHAIR_X, self.CHAIR_TOP, self.SEAT_Y_
        p.setPen(QPen(QColor(T.BORDER_STRONG), 1.2))
        p.setBrush(QColor(T.TABLE_FACE[0]))
        p.drawRoundedRect(QRectF(x - 10, top, 22, seat - top + 10), 6, 6)                 # backrest
        for lx in (x - 6, x + 160):
            p.drawRect(QRectF(lx, seat + 18, 14, self.TOE.y() + 4 - seat - 18))           # legs
        g = QLinearGradient(0, seat, 0, seat + 20)
        g.setColorAt(0, QColor(T.TABLE_TOP[0]))
        g.setColorAt(1, QColor(T.TABLE_TOP[1]))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(x - 10, seat, 194, 20), 4, 4)                              # seat

    def _target_zone(self, p):
        pass

    def _flexors(self, p, theta: float):
        pass

    def _flexor_label(self, p):
        pass        # the calf moves up and down, so its label is drawn with it

    def _draw_limb(self, p: QPainter, k: float, ghost: bool):
        if ghost:
            return
        k = max(0.0, min(1.15, k))
        a = math.radians(24 * k)
        u = QPointF(math.cos(a), math.sin(a))              # heel -> toe
        n = QPointF(math.sin(a), -math.cos(a))             # up from the sole
        toe = self.TOE
        heel = toe - u * self.FOOT_L
        ankle = heel + u * 26 + n * 34
        knee = QPointF(ankle.x(), ankle.y() - self.SHIN_L)
        hip = QPointF(knee.x(), knee.y() - self.THIGH_L)
        # Torso and the arm to the chair first, faded at the top like the other views.
        torso = QPainterPath()
        torso.addRoundedRect(QRectF(hip.x() - 70, -60, 150, hip.y() + 100), 60, 60)
        _draw_cloth(p, torso, 0, hip.y())
        hand = QPointF(self.CHAIR_X - 4, self.CHAIR_TOP + 4)
        _segment_between(p, QPointF(hip.x() + 20, hip.y() - 150), hand, lambda L: _limb(L, 22, 15),
                         lambda path: _draw_skin(p, path, -22, 22))
        _fade_top(p, hip.x() - 90, hip.x() + 200, -20, 110)
        _segment_between(p, hip, knee, lambda L: _limb(L, 44, 34),
                         lambda path: _draw_cloth(p, path, -44, 44, TROUSERS))
        # Lower leg with the calf on its back (+y in the segment's frame points backward).
        calf = [None]

        def shin(path):
            p.save()
            p.scale(1, -1)
            _draw_skin(p, path, -32, 32)
            p.restore()
            self._muscle_brush(p)
            calf[0] = _spindle(18, 20, 120, 13, 11)
            p.drawPath(calf[0])
        _segment_between(p, knee, ankle, lambda L: _limb(L, 32, 17, belly=10), shin)
        _draw_skin(p, _circle(knee, 30), knee.y() - 30, knee.y() + 30)
        shoe = QPainterPath()
        shoe.moveTo(heel)
        shoe.lineTo(toe)
        shoe.lineTo(toe + n * 18)
        shoe.quadTo(heel + u * 60 + n * 46, heel + n * 34)
        shoe.closeSubpath()
        p.setPen(QPen(QColor(40, 45, 55), 1.2))
        p.setBrush(QColor("#3b4350"))
        p.drawPath(shoe)
        if getattr(self, "compact", False):
            return
        # Muscle label with a leader to the back of the calf.
        c = QPointF(knee.x() - 28, knee.y() + 70)
        tip = QPointF(c.x() - 90, c.y() - 50)
        p.setPen(QPen(QColor(T.MUTED), 1))
        p.drawLine(c, tip)
        p.setFont(self._font(12, caps=True))
        p.drawText(QRectF(tip.x() - 404, tip.y() - 58, 400, 60),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, self.muscle_label)


def limb_view(kind: str) -> ArmView:
    """The side-view drawing for an exercise's `view` (see exercises.Exercise.view)."""
    return {"knee": KneeView, "hip": HipView, "scapula": ScapulaView, "curl": CurlView,
            "pendulum": PendulumView, "quad_set": QuadSetView, "leg_raise": LegRaiseView,
            "knee_emg": EmgKneeView, "calf_raise": CalfRaiseView}.get(kind, ArmView)()


# ------------------------------------------------------------------ pacer

class PacerWidget(QWidget):
    """Segmented tempo track in one accent colour; the current phase is filled and named."""

    NAMES = {"lift": "Lift", "hold": "Hold", "lower": "Lower", "pause": "Rest"}

    TRACK_GAP, TRACK_H = 8, 12

    def __init__(self, clock: PacerClock, parent=None):
        super().__init__(parent)
        self.clock = clock
        # Height follows the fonts, so the layout can never squeeze the labels off the bottom.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _rows():
        """Heights of the caption, phase-name and segment-label rows for the current type scale."""
        return (QFontMetrics(T.font(12, caps=True)).height(), QFontMetrics(T.font(T.L, bold=True)).height(),
                QFontMetrics(T.font(12, bold=True)).height())

    def sizeHint(self):
        cap, big, small = self._rows()
        return QSize(T.pt(320), cap + big + self.TRACK_GAP + self.TRACK_H + 4 + small + 2)

    def minimumSizeHint(self):
        return QSize(T.pt(200), self.sizeHint().height())

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        cap, big, small = self._rows()
        name, f, _, _ = self.clock.phase()
        durations = dict(self.clock.phases)
        remain = max(0, math.ceil((1 - f) * durations[name] - 1e-6))

        p.setPen(QColor(T.MUTED))
        p.setFont(T.font(12, caps=True))
        p.drawText(QRectF(0, 0, w, cap), Qt.AlignmentFlag.AlignLeft, "Tempo")
        p.setPen(QColor(T.ACCENT))
        p.setFont(T.font(T.L, bold=True))
        p.drawText(QRectF(0, cap, w * 0.7, big), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.NAMES[name])
        p.setPen(QColor(T.TEXT))
        p.setFont(T.font(T.L, light=True, tabular=True))
        p.drawText(QRectF(w * 0.5, cap, w * 0.5, big), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{remain} s")

        gap = 6.0
        track_y, track_h = float(cap + big + self.TRACK_GAP), float(self.TRACK_H)
        avail = w - gap * (len(self.clock.phases) - 1)
        x = 0.0
        past = True
        for ph, d in self.clock.phases:
            seg_w = avail * d / self.clock.cycle
            seg = QRectF(x, track_y, seg_w, track_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.SURFACE_2))
            p.drawRoundedRect(seg, 4, 4)
            if ph == name:
                past = False
                p.setBrush(QColor(T.ACCENT))
                p.drawRoundedRect(QRectF(x, track_y, max(seg_w * f, 4), track_h), 4, 4)
            elif past:
                p.setBrush(T.qcolor(T.ACCENT, 90))
                p.drawRoundedRect(seg, 4, 4)
            p.setPen(QColor(T.TEXT) if ph == name else QColor(T.FAINT))
            p.setFont(T.font(12, bold=(ph == name)))
            p.drawText(QRectF(x, track_y + track_h + 4, seg_w, small), Qt.AlignmentFlag.AlignLeft,
                       f"{self.NAMES[ph]} {d:g}s")
            x += seg_w + gap
        p.end()


# ------------------------------------------------------------------ edge glow

class GlowOverlay(QWidget):
    """Coloured border around the whole window; fades out. Ignores the mouse."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.color = QColor(T.GOOD)
        self.level = 0.0
        self.anim = QVariantAnimation(self)
        self.anim.valueChanged.connect(self._set_level)

    def _set_level(self, v):
        self.level = float(v)
        self.update()

    def flash(self, color: str, ms: int):
        self.color = QColor(color)
        self.anim.stop()
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.setDuration(ms)
        self.raise_()
        self.anim.start()

    def paintEvent(self, ev):
        if self.level <= 0.01:
            return
        p = QPainter(self)
        w, h = self.width(), self.height()
        d = max(24, int(min(w, h) * 0.05))
        c0 = QColor(self.color)
        c0.setAlpha(int(220 * self.level))
        c1 = QColor(self.color)
        c1.setAlpha(0)
        for rect, start, end in (
            (QRectF(0, 0, w, d), QPointF(0, 0), QPointF(0, d)),
            (QRectF(0, h - d, w, d), QPointF(0, h), QPointF(0, h - d)),
            (QRectF(0, 0, d, h), QPointF(0, 0), QPointF(d, 0)),
            (QRectF(w - d, 0, d, h), QPointF(w, 0), QPointF(w - d, 0)),
        ):
            g = QLinearGradient(start, end)
            g.setColorAt(0, c0)
            g.setColorAt(1, c1)
            p.fillRect(rect, QBrush(g))
        p.end()


# ------------------------------------------------------------------ placement diagram

def _topview_forearm() -> QPainterPath:
    """Forearm seen from above, palm side up: wide at the elbow, tapering to the wrist."""
    p = QPainterPath()
    p.moveTo(46, 182)
    p.cubicTo(44, 124, 96, 112, 160, 114)
    p.cubicTo(270, 118, 390, 142, 470, 152)
    p.lineTo(470, 212)
    p.cubicTo(390, 222, 270, 246, 160, 250)
    p.cubicTo(96, 252, 48, 240, 46, 182)
    p.closeSubpath()
    return p


def _draw_topview_hand(p: QPainter, x0: float, y_mid: float, thumb_up: bool = True):
    """Hand seen from above: palm, four tapered fingers and the thumb, each a separate shape
    so the fingers read clearly. x0 = wrist line; fingers point to +x."""
    sy = -1.0 if thumb_up else 1.0
    pen = QPen(SKIN_LINE, 1.2)
    # Fingers first (index nearest the thumb), so the palm overlaps their bases.
    for i, (length, width) in enumerate(((78, 21), (90, 22), (84, 21), (66, 19))):
        cy = y_mid + sy * (-33 + i * 22)
        f = QPainterPath()
        f.addRoundedRect(QRectF(x0 + 96, cy - width / 2, length + 14, width), width / 2, width / 2)
        p.setPen(pen)
        p.setBrush(_skin_gradient(cy - width / 2, cy + width / 2))
        p.drawPath(f)
    # Thumb, angled away from the palm.
    p.save()
    p.translate(x0 + 44, y_mid + sy * 34)
    p.rotate(-38 if thumb_up else 38)
    t = QPainterPath()
    t.addRoundedRect(QRectF(0, -11, 78, 22), 11, 11)
    p.setBrush(_skin_gradient(-11, 11))
    p.drawPath(t)
    p.restore()
    palm = QPainterPath()
    palm.moveTo(x0 - 4, y_mid - 32)
    palm.cubicTo(x0 + 40, y_mid - 48, x0 + 92, y_mid - 50, x0 + 112, y_mid - 42)
    palm.cubicTo(x0 + 120, y_mid - 20, x0 + 120, y_mid + 30, x0 + 110, y_mid + 48)
    palm.cubicTo(x0 + 80, y_mid + 54, x0 + 40, y_mid + 48, x0 - 4, y_mid + 32)
    palm.closeSubpath()
    p.setBrush(_skin_gradient(y_mid - 50, y_mid + 54))
    p.drawPath(palm)


class PlacementDiagram(QWidget):
    """Electrodes on the wrist flexors (palm side) and the IMU on the back of the hand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 170)
        self.forearm = _topview_forearm()
        self._s = 1.0

    def _callout(self, p, anchor: QPointF, text_pos: QPointF, text: str, align=Qt.AlignmentFlag.AlignLeft):
        p.setPen(QPen(QColor(T.MUTED), 1.2))
        p.drawLine(anchor, text_pos)
        p.setBrush(QColor(T.MUTED))
        p.drawEllipse(anchor, 2.5, 2.5)
        p.setPen(QColor(T.TEXT))
        p.setFont(scene_font(self._s, 14, T.M))
        w = 600
        x = text_pos.x() - w - 6 if align == Qt.AlignmentFlag.AlignRight else text_pos.x() + 6
        p.drawText(QRectF(x, text_pos.y() - 60, w, 120), align | Qt.AlignmentFlag.AlignVCenter, text)

    @staticmethod
    def _electrode(p, c: QPointF, color: str):
        p.setPen(QPen(QColor("#9aa3ad"), 1.5))
        p.setBrush(QColor("#e9edf1"))
        p.drawEllipse(c, 15, 15)
        p.setBrush(QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(c, 6, 6)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = 1000.0, 520.0
        s = self._s = min(self.width() / W, self.height() / H)
        p.translate((self.width() - W * s) / 2, (self.height() - H * s) / 2)
        p.scale(s, s)

        p.setPen(QColor(T.MUTED))
        p.setFont(scene_font(s, 12, T.S, caps=True))
        p.drawText(QRectF(40, 20, 640, 60), Qt.AlignmentFlag.AlignLeft, "1  Palm side · muscle pads")
        p.drawText(QRectF(700, 20, 640, 60), Qt.AlignmentFlag.AlignLeft, "2  Back of hand · sensor")

        # Palm-side forearm and hand.
        _draw_topview_hand(p, 466, 182, thumb_up=True)
        p.setPen(QPen(SKIN_LINE, 1.3))
        p.setBrush(_skin_gradient(110, 254))
        p.drawPath(self.forearm)
        x13 = 46 + (470 - 46) / 3
        p.setPen(QPen(T.qcolor(T.ACCENT, 160), 1.2, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x13, 96), QPointF(x13, 268))
        self._electrode(p, QPointF(x13 - 20, 164), T.ACCENT)
        self._electrode(p, QPointF(x13 + 20, 164), T.ACCENT)
        self._electrode(p, QPointF(66, 196), "#d98a4f")
        self._callout(p, QPointF(x13, 150), QPointF(x13 + 30, 76), "Muscle pads, 1/3 from the elbow")
        self._callout(p, QPointF(66, 210), QPointF(90, 320), "Reference pad on the elbow bone")
        p.setPen(QColor(T.MUTED))
        p.setFont(scene_font(s, 12, T.S, caps=True))
        # "elbow" sits above the arm so it does not cross the reference-pad leader line.
        p.drawText(QRectF(30, 40, 200, 70), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, "elbow")
        p.drawText(QRectF(430, 262, 200, 60), Qt.AlignmentFlag.AlignLeft, "wrist")

        # Back of the hand with the IMU.
        stub = QPainterPath()
        stub.moveTo(640, 148)
        stub.cubicTo(670, 146, 690, 148, 712, 150)
        stub.lineTo(712, 214)
        stub.cubicTo(690, 216, 670, 218, 640, 216)
        stub.closeSubpath()
        _draw_topview_hand(p, 708, 182, thumb_up=False)
        p.setPen(QPen(SKIN_LINE, 1.3))
        p.setBrush(_skin_gradient(140, 224))
        p.drawPath(stub)
        p.setPen(QPen(QColor("#0b0f13"), 1.5))
        p.setBrush(QColor("#232a31"))
        box = QRectF(742, 154, 64, 56)
        p.drawRoundedRect(box, 6, 6)
        p.setPen(QColor(T.MUTED))
        p.setFont(T.font(9, bold=True))
        p.drawText(box.adjusted(0, 0, 0, -18), Qt.AlignmentFlag.AlignCenter, "JY901S")
        arrow_pen = QPen(QColor(T.ACCENT), 3)
        arrow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arrow_pen)
        y = box.center().y() + 10
        p.drawLine(QPointF(box.left() + 14, y), QPointF(960, y))
        p.drawLine(QPointF(960, y), QPointF(948, y - 8))
        p.drawLine(QPointF(960, y), QPointF(948, y + 8))
        p.setPen(QColor(T.ACCENT))
        p.setFont(scene_font(s, 14, T.M, bold=True))
        p.drawText(QRectF(930, y - 60, 60, 48), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, "X")
        self._callout(p, QPointF(784, 214), QPointF(800, 330), "Sensor box, X arrow\ntoward the fingers")

        p.setPen(QColor(T.MUTED))
        p.setFont(scene_font(s, 14, T.M))
        p.drawText(QRectF(40, 420, 920, 100), Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                   "Clean the skin first. Press the dry pads firmly onto the skin.")
        p.end()


class LegPlacementDiagram(PlacementDiagram):
    """Seated side view: muscle pads on the front of the thigh, reference pad on the kneecap,
    and the sensor box on the shin (`shin`, knee straightening) or just above the knee
    (`thigh`, knee lift), with its X arrow along the moving segment."""

    W, H = 1000.0, 700.0

    def __init__(self, where: str = "shin", parent=None):
        super().__init__(parent)
        self.where = where

    def _sensor_box(self, p, rect: QRectF, arrow_from: QPointF, arrow_to: QPointF):
        p.setPen(QPen(QColor("#0b0f13"), 1.5))
        p.setBrush(QColor("#232a31"))
        p.drawRoundedRect(rect, 5, 5)
        pen = QPen(QColor(T.ACCENT), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(arrow_from, arrow_to)
        d = arrow_to - arrow_from
        n = math.hypot(d.x(), d.y()) or 1.0
        ux, uy = d.x() / n, d.y() / n
        for side in (1, -1):        # arrow head
            p.drawLine(arrow_to, QPointF(arrow_to.x() - 12 * ux + side * 8 * uy, arrow_to.y() - 12 * uy - side * 8 * ux))
        p.setPen(QColor(T.ACCENT))
        p.setFont(scene_font(self._s, 14, T.M, bold=True))
        p.drawText(QRectF(arrow_to.x() + 8, arrow_to.y() - 30, 80, 60),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "X")

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.W, self.H
        s = self._s = min(self.width() / W, self.height() / H)
        p.translate((self.width() - W * s) / 2, (self.height() - H * s) / 2)
        p.scale(s, s)

        # The leg at rest, as in the exercise view.
        _draw_chair(p, 130, 480)
        _draw_torso(p, 140, 270, 300)
        _draw_skin(p, KneeView._thigh(), 248, SEAT_Y)
        knee = KneeView.PIVOT
        p.save()
        p.translate(knee)
        p.rotate(90)
        _draw_skin(p, SHIN, -100, 40)
        _draw_skin(p, _circle(QPointF(0, 0), KNEE_R), -KNEE_R, KNEE_R)
        p.restore()

        # Muscle pads on the middle of the front of the thigh; reference pad on the kneecap.
        self._electrode(p, QPointF(330, 256), T.ACCENT)
        self._electrode(p, QPointF(372, 260), T.ACCENT)
        self._electrode(p, QPointF(knee.x() + 22, knee.y() - 10), "#d98a4f")
        self._callout(p, QPointF(350, 250), QPointF(380, 120), "Muscle pads, middle of the thigh")
        self._callout(p, QPointF(knee.x() + 22, knee.y() - 10), QPointF(640, 250), "Reference pad on the kneecap")

        if self.where == "shin":
            # Box on the front of the shin, X arrow pointing down to the foot.
            box = QRectF(knee.x() + 22, 400, 22, 64)
            self._sensor_box(p, box, QPointF(box.right() + 14, 404), QPointF(box.right() + 14, 500))
            self._callout(p, QPointF(box.right(), 440), QPointF(640, 410),
                          "Sensor box on the shin,\nX arrow down to the foot")
        else:
            # Box on top of the thigh just above the knee, X arrow toward the knee.
            box = QRectF(410, 238, 64, 20)
            self._sensor_box(p, box, QPointF(414, 224), QPointF(500, 230))
            self._callout(p, QPointF(474, 240), QPointF(640, 340),
                          "Sensor box above the knee,\nX arrow toward the knee")

        p.setPen(QColor(T.MUTED))
        p.setFont(scene_font(s, 14, T.M))
        p.drawText(QRectF(40, 590, 920, 110), Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                   "Clean the skin first. Press the dry pads firmly onto the skin.")
        p.end()


def placement_diagram(where: str) -> PlacementDiagram:
    """Sensor placement drawing for an exercise's `placement`: arm | shin | thigh."""
    return PlacementDiagram() if where == "arm" else LegPlacementDiagram(where)


# ------------------------------------------------------------------ banners

def section_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setFont(T.font(12, caps=True))
    lab.setStyleSheet(f"color: {T.MUTED};")
    return lab


class MessageBanner(QFrame):
    """Large icon + text message in a card with a coloured edge. Colour is never the only cue."""

    NEUTRAL = "Follow the guide"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 16, 0)
        outer.setSpacing(16)
        self.edge = QFrame()
        self.edge.setFixedWidth(6)
        outer.addWidget(self.edge)
        self.icon = Icon("target", T.MUTED, T.pt(40))
        outer.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = QLabel()
        self.text.setFont(T.font(T.L, bold=True))
        self.text.setWordWrap(True)
        outer.addWidget(self.text, 1)
        self.setMinimumHeight(T.pt(72))
        self.until = 0.0
        self.neutral(self.NEUTRAL)

    def _style(self, color: str | None):
        if color is None:
            self.setStyleSheet(f"QFrame#card {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; "
                               f"border-radius: 10px; }}")
            self.edge.setStyleSheet(f"background: {T.BORDER_STRONG}; border: none; border-radius: 3px;")
            self.text.setStyleSheet(f"color: {T.MUTED};")
            return
        c = QColor(color)
        tint = f"rgba({c.red()}, {c.green()}, {c.blue()}, 38)"
        self.setStyleSheet(f"QFrame#card {{ background: {tint}; border: 1px solid {color}; border-radius: 10px; }}")
        self.edge.setStyleSheet(f"background: {color}; border: none; border-radius: 3px;")
        self.text.setStyleSheet(f"color: {T.TEXT};")

    def show_message(self, icon: str, text: str, color: str, seconds: float):
        """icon: name from icons.py (e.g. 'check', or icons.fault_icon(code))."""
        self.icon.set_icon(icon, color)
        self.text.setText(text)
        self._style(color)
        self.until = time.monotonic() + seconds

    def neutral(self, text: str):
        self.icon.set_icon("target", T.MUTED)
        self.text.setText(text)
        self._style(None)

    def expired(self) -> bool:
        return time.monotonic() >= self.until


class GaugeBar(QWidget):
    """Slim horizontal gauge with zone ticks."""

    def __init__(self, ticks=(40, 70), parent=None):
        super().__init__(parent)
        self.value = float("nan")
        self.color = QColor(T.FAINT)
        self.ticks = ticks
        self.setFixedHeight(32)     # track and tick labels are drawn at fixed pixel offsets

    def set_value(self, v: float, color: str):
        self.value, self.color = v, QColor(color)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        track = QRectF(0, 4, w, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.SURFACE_2))
        p.drawRoundedRect(track, 4, 4)
        if self.value == self.value:
            p.setBrush(self.color)
            p.drawRoundedRect(QRectF(0, 4, max(8.0, w * min(1.0, self.value / 100)), 8), 4, 4)
        p.setPen(QPen(QColor(T.MUTED), 1.2))
        p.setFont(T.font(10))
        for t in self.ticks:
            x = w * t / 100
            p.drawLine(QPointF(x, 1), QPointF(x, 15))
            p.drawText(QRectF(x - 20, 15, 40, 14), Qt.AlignmentFlag.AlignCenter, str(t))
        p.end()


class FatigueMeter(QFrame):
    LABELS = {"": "Measuring…", "green": "Low", "amber": "Getting tired", "red": "Please rest"}
    ICONS = {"": "info", "green": "check", "amber": "warning", "red": "stop"}
    COLORS = {"": T.FAINT, "green": T.GOOD, "amber": T.WARN, "red": T.BAD}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)
        lay.addWidget(section_label("Fatigue"))
        row = QHBoxLayout()
        row.setSpacing(12)
        self.icon = Icon("info", T.FAINT, T.pt(32))
        self.label = QLabel()
        self.label.setFont(T.font(T.M, bold=True))
        row.addWidget(self.icon)
        row.addWidget(self.label, 1)
        lay.addLayout(row)
        self.gauge = GaugeBar()
        lay.addWidget(self.gauge)
        self.set_score(float("nan"), "")

    def set_ticks(self, amber: float, red: float):
        self.gauge.ticks = (amber, red)

    def set_score(self, score: float, zone: str):
        color = self.COLORS.get(zone, T.FAINT)
        self.icon.set_icon(self.ICONS.get(zone, "info"), color)
        self.label.setText(self.LABELS.get(zone, ""))
        self.gauge.set_value(score, color)
