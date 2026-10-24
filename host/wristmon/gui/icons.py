"""Line icons drawn with QPainter (24-unit grid, 2-unit rounded strokes), replacing emoji.

Every fault code maps to an icon so a state is always shown as icon + text, never colour alone.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QWidget

from .. import faults as F
from . import theme as T

FAULT_ICONS = {
    F.INCOMPLETE_ROM: "arrow_up",
    F.EARLY_DOWN: "pause",
    F.NO_MUSCLE: "pulse",
    F.FOREARM_LIFT: "hand",
    F.DROP_FAST: "speed",
    F.SWING_FAST: "speed",
    F.WRIST_TWIST: "rotate",
    F.SIDE_BEND: "arrows_h",
    F.OVER_FLEX: "limit",
    F.NOT_RETURNED: "arrow_down_bar",
    F.JERKY: "wave",
    F.SIGNAL_POOR: "plug",
    F.TIMEOUT: "stopwatch",
}


def fault_icon(code: str) -> str:
    return FAULT_ICONS.get(code, "warning")


def _poly(path: QPainterPath, *pts):
    path.moveTo(*pts[0])
    for p in pts[1:]:
        path.lineTo(*p)


def _shape(name: str) -> tuple[QPainterPath, QPainterPath]:
    """(stroked path, filled path) on a 24 x 24 grid."""
    s = QPainterPath()
    f = QPainterPath()
    if name == "check":
        _poly(s, (5, 12.5), (10, 17.5), (19, 7))
    elif name == "cross":
        _poly(s, (6, 6), (18, 18))
        _poly(s, (18, 6), (6, 18))
    elif name == "arrow_up":
        _poly(s, (12, 19), (12, 5))
        _poly(s, (6, 11), (12, 5), (18, 11))
    elif name == "skip":
        _poly(s, (5, 6), (11, 12), (5, 18))
        _poly(s, (11, 6), (17, 12), (11, 18))
        _poly(s, (19.5, 6), (19.5, 18))
    elif name == "arrow_left":
        _poly(s, (19, 12), (5, 12))
        _poly(s, (11, 6), (5, 12), (11, 18))
    elif name == "arrow_down_bar":
        _poly(s, (12, 4), (12, 16))
        _poly(s, (7, 11), (12, 16), (17, 11))
        _poly(s, (5, 20), (19, 20))
    elif name == "limit":
        _poly(s, (5, 4), (19, 4))
        _poly(s, (12, 20), (12, 8))
        _poly(s, (7, 13), (12, 8), (17, 13))
    elif name == "pause":
        _poly(s, (9, 6), (9, 18))
        _poly(s, (15, 6), (15, 18))
    elif name == "pulse":
        _poly(s, (2, 12), (7, 12), (9, 6), (12, 18), (15, 9), (17, 12), (22, 12))
    elif name == "hand":
        s.addRoundedRect(QRectF(7, 11, 11, 9), 3, 3)
        for x, top in ((8.5, 5.5), (11, 4), (13.5, 4.5), (16, 6.5)):
            _poly(s, (x, 11), (x, top))
        _poly(s, (7, 15), (4.5, 12))
    elif name == "speed":
        s.arcMoveTo(QRectF(4, 6, 16, 16), 200)
        s.arcTo(QRectF(4, 6, 16, 16), 200, -220)
        _poly(s, (12, 14), (16, 9.5))
        f.addEllipse(QPointF(12, 14), 1.6, 1.6)
    elif name == "rotate":
        s.arcMoveTo(QRectF(5, 5, 14, 14), 60)
        s.arcTo(QRectF(5, 5, 14, 14), 60, 290)
        _poly(s, (15.5, 3.5), (15.6, 6.4), (18.6, 6.6))
    elif name == "arrows_h":
        _poly(s, (3, 12), (21, 12))
        _poly(s, (7, 8), (3, 12), (7, 16))
        _poly(s, (17, 8), (21, 12), (17, 16))
    elif name == "wave":
        s.moveTo(2, 12)
        for i in range(4):
            x = 2 + i * 5
            s.cubicTo(x + 1.25, 12 - 5 * (1 if i % 2 == 0 else -1), x + 3.75, 12 - 5 * (1 if i % 2 == 0 else -1),
                      x + 5, 12)
    elif name == "plug":
        s.addRoundedRect(QRectF(7, 8, 10, 7), 2, 2)
        _poly(s, (10, 4), (10, 8))
        _poly(s, (14, 4), (14, 8))
        _poly(s, (12, 15), (12, 20))
    elif name == "stopwatch":
        s.addEllipse(QPointF(12, 13.5), 7, 7)
        _poly(s, (10, 3), (14, 3))
        _poly(s, (12, 3), (12, 6.5))
        _poly(s, (12, 13.5), (12, 9.5))
    elif name == "warning":
        _poly(s, (12, 3.5), (21.5, 19.5), (2.5, 19.5), (12, 3.5))
        _poly(s, (12, 9.5), (12, 14))
        f.addEllipse(QPointF(12, 16.8), 1.2, 1.2)
    elif name == "battery":
        s.addRoundedRect(QRectF(2.5, 7.5, 17, 9), 2, 2)
        _poly(s, (21.5, 10.5), (21.5, 13.5))
    elif name == "info":
        s.addEllipse(QPointF(12, 12), 9, 9)
        _poly(s, (12, 11), (12, 16.5))
        f.addEllipse(QPointF(12, 7.8), 1.2, 1.2)
    elif name == "stop":
        pts = [(12 + 9 * math.cos(math.radians(22.5 + 45 * k)), 12 + 9 * math.sin(math.radians(22.5 + 45 * k)))
               for k in range(9)]
        _poly(s, *pts)
        _poly(s, (8, 12), (16, 12))
    elif name == "target":
        s.addEllipse(QPointF(12, 12), 8.5, 8.5)
        s.addEllipse(QPointF(12, 12), 4, 4)
        f.addEllipse(QPointF(12, 12), 1.2, 1.2)
    elif name == "volume":
        _poly(s, (4, 9.5), (8, 9.5), (13, 5), (13, 19), (8, 14.5), (4, 14.5), (4, 9.5))
        s.arcMoveTo(QRectF(11, 7, 10, 10), 50)
        s.arcTo(QRectF(11, 7, 10, 10), 50, -100)
    elif name == "dot":
        f.addEllipse(QPointF(12, 12), 5, 5)
    elif name == "chevron_down":
        _poly(s, (6, 9), (12, 15), (18, 9))
    elif name == "rest":
        # Pause inside a circle: "take a break".
        s.addEllipse(QPointF(12, 12), 9, 9)
        _poly(s, (10, 8.5), (10, 15.5))
        _poly(s, (14, 8.5), (14, 15.5))
    else:
        s.addEllipse(QPointF(12, 12), 8, 8)
    return s, f


def paint_icon(p: QPainter, rect: QRectF, name: str, color: QColor | str, stroke: float = 2.0):
    """Draws icon `name` scaled into `rect`."""
    sp, fp = _shape(name)
    color = QColor(color)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = min(rect.width(), rect.height())
    p.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    p.scale(side / 24.0, side / 24.0)
    pen = QPen(color, stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(sp)
    if not fp.isEmpty():
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(fp)
    p.restore()


def icon_pixmap(name: str, size: int, color: QColor | str) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_icon(p, QRectF(0, 0, size, size), name, color)
    p.end()
    return pm


def qicon(name: str, size: int, color: QColor | str) -> QIcon:
    return QIcon(icon_pixmap(name, size, color))


class Icon(QWidget):
    """A line icon as a widget; set_icon() changes glyph and colour."""

    def __init__(self, name: str = "dot", color: str = T.TEXT, size: int = 32, parent=None):
        super().__init__(parent)
        self.name = name
        self.color = QColor(color)
        self.setFixedSize(QSize(size, size))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_icon(self, name: str, color: str | None = None):
        self.name = name
        if color is not None:
            self.color = QColor(color)
        self.update()

    def paintEvent(self, ev):
        if not self.name:
            return
        p = QPainter(self)
        paint_icon(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), self.name, self.color,
                   stroke=2.0 if self.width() < 48 else 1.8)
        p.end()
