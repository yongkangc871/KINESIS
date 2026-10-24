"""KINESIS branding: heart-and-cross logo, gradient wordmark and the page backdrop.

The backdrop is painted once behind every page (pages are transparent), so all screens share
the same look: soft blue vertical bars at the left and right edges and a pale sweep at the
lower right. It is drawn in 1920 x 1080 reference units and scaled to the window.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QLinearGradient, QPainter, QPainterPath,
                         QPen)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from . import theme as T

APP_NAME = "KINESIS Rehab Coach"

# Heavy condensed display faces, best match first; Impact ships with every Windows PC.
TITLE_FAMILIES = ["Bebas Neue", "Anton", "Impact", "Bahnschrift Condensed"]
TITLE_GRADIENT = ("#5ddbe6", "#2a9bd0", "#0a4cad")    # cyan -> blue -> deep blue
NAVY = "#1d2738"


def title_font(size_pt: float) -> QFont:
    have = set(QFontDatabase.families())
    f = QFont()
    f.setFamilies([n for n in TITLE_FAMILIES if n in have] or ["Impact"])
    f.setPointSizeF(size_pt)
    f.setItalic(False)
    return f


class HeartLogo(QWidget):
    """Outline heart with a medical cross inside, both with a small gap like a hand-drawn mark."""

    def __init__(self, size: int, color: str = NAVY, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(size, size)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width() / 100.0
        p.scale(s, s)
        pen = QPen(self.color, 4.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        # Complete heart, symmetric about x = 50: one closed outline from the centre dip.
        heart = QPainterPath()
        heart.moveTo(50, 26)
        heart.cubicTo(51, 19, 55, 13, 63, 12)                  # right lobe
        heart.cubicTo(78, 10, 92, 20, 92, 36)
        heart.cubicTo(92, 54, 74, 70, 50, 90)                  # right side down to the tip
        heart.cubicTo(26, 70, 8, 54, 8, 36)                    # left side up
        heart.cubicTo(8, 20, 22, 10, 37, 12)                   # left lobe
        heart.cubicTo(45, 13, 49, 19, 50, 26)                  # back into the dip
        heart.closeSubpath()
        p.drawPath(heart)
        # Medical cross centred inside the heart.
        cx, cy, a, w = 50, 50, 14, 5.5                         # centre, arm reach, arm half-width
        cross = QPainterPath()
        cross.moveTo(cx - w, cy - a)
        for x, y in ((cx + w, cy - a), (cx + w, cy - w), (cx + a, cy - w), (cx + a, cy + w),
                     (cx + w, cy + w), (cx + w, cy + a), (cx - w, cy + a), (cx - w, cy + w),
                     (cx - a, cy + w), (cx - a, cy - w), (cx - w, cy - w)):
            cross.lineTo(x, y)
        cross.closeSubpath()
        p.drawPath(cross)
        p.end()


class GradientTitle(QWidget):
    """Single-line wordmark filled with a left-to-right gradient."""

    def __init__(self, text: str, size_pt: float, parent=None):
        super().__init__(parent)
        self.text = text
        self.font_ = title_font(size_pt)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font_)
        return QSize(fm.horizontalAdvance(self.text) + 8, fm.ascent() + fm.descent() // 2)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = QFontMetrics(self.font_)
        w = fm.horizontalAdvance(self.text)
        path = QPainterPath()
        path.addText(QPointF(2, fm.ascent()), self.font_, self.text)
        g = QLinearGradient(0, 0, w, 0)
        for pos, c in zip((0.0, 0.5, 1.0), TITLE_GRADIENT):
            g.setColorAt(pos, QColor(c))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawPath(path)
        p.end()


class GradientFrame(QWidget):
    """Rounded panel with a soft mint-to-blue gradient; used to frame the name field."""

    def __init__(self, radius: float = 14, parent=None):
        super().__init__(parent)
        self.radius = radius

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, self.width(), 0)
        g.setColorAt(0.0, QColor("#e3fbee"))
        g.setColorAt(0.55, QColor("#d3e9f6"))
        g.setColorAt(1.0, QColor("#c3d6fb"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(self.rect()), self.radius, self.radius)
        p.end()


def _vbar(p: QPainter, x: float, y0: float, w: float, y1: float, stops):
    """Vertical bar from y0 to y1 filled with a top-to-bottom gradient [(pos, color, alpha)]."""
    g = QLinearGradient(0, y0, 0, y1)
    for pos, c, a in stops:
        g.setColorAt(pos, T.qcolor(c, a))
    p.fillRect(QRectF(x, y0, w, y1 - y0), g)


class Backdrop(QWidget):
    """Window background behind every page. `quiet` paints the same design much lighter, for
    the busy calibration / exercise / summary pages whose cards and text reach the edges."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.quiet = False

    def set_quiet(self, quiet: bool):
        if quiet != self.quiet:
            self.quiet = quiet
            self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, QColor("#ffffff"))
        g.setColorAt(1, QColor(T.BG))
        p.fillRect(self.rect(), g)
        p.setOpacity(0.28 if self.quiet else 1.0)
        p.scale(w / 1920.0, h / 1080.0)
        light, navy, pale = "#8fc3da", "#0b2d5c", "#dcedf6"

        # Lower-right sweep.
        sweep = QPainterPath()
        sweep.moveTo(1180, 1080)
        sweep.cubicTo(1300, 560, 1650, 360, 1920, 470)
        sweep.lineTo(1920, 1080)
        sweep.closeSubpath()
        p.fillPath(sweep, T.qcolor("#e9f3fa", 200))

        # Left group.
        _vbar(p, 0, 0, 245, 760, [(0, light, 230), (0.55, light, 90), (1, light, 0)])
        _vbar(p, 165, 297, 160, 1000, [(0, pale, 220), (1, pale, 0)])
        _vbar(p, 85, 205, 70, 700, [(0, navy, 255), (0.5, navy, 140), (1, navy, 0)])
        _vbar(p, 175, 0, 70, 420, [(0, navy, 255), (0.6, navy, 110), (1, navy, 0)])
        for x in (30, 150, 165):
            _vbar(p, x, 0, 4, 900, [(0, "#ffffff", 255), (1, "#ffffff", 0)])

        # Right group.
        _vbar(p, 1670, 615, 160, 1080, [(0, "#cfe6f1", 230), (1, "#cfe6f1", 120)])
        _vbar(p, 1790, 725, 130, 1080, [(0, navy, 255), (0.6, "#6c9fc4", 200), (1, light, 160)])
        _vbar(p, 1725, 900, 105, 1080, [(0, "#a7d3e3", 240), (1, "#a7d3e3", 200)])
        for x in (1868, 1884):
            _vbar(p, x, 445, 4, 1080, [(0, "#ffffff", 0), (0.3, "#ffffff", 255), (1, "#ffffff", 255)])
        p.end()
