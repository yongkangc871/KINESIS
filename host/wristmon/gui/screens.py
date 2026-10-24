"""Full-screen pages. One instruction per screen, large text, and every state shown with an
icon + words (never colour alone)."""
from __future__ import annotations

import csv
import math
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout,
                             QLabel, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .. import exercises as EX
from .. import faults as F
from ..calibration import emg_driven
from ..datatypes import RepRecord
from . import branding as B
from . import theme as T
from .icons import Icon, fault_icon, icon_pixmap, qicon
from .plots import FatigueChart, LivePlots, TrendChart
from .widgets import (FatigueMeter, MessageBanner, PacerClock, PacerWidget, limb_view, placement_diagram,
                      section_label)

DISCLAIMER = ("This program gives exercise guidance only. It does not diagnose or treat any condition. "
              "Stop if you feel pain or discomfort and ask your therapist.")

LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
CENTER = Qt.AlignmentFlag.AlignCenter
GAP = 16           # 8 px grid
MARGIN = 40
# Centred pages: side : body stretch, so content clears the backdrop's edge bars.
SIDE, BODY = 2, 7


def label(text="", size=T.S, bold=False, color=None, wrap=True, align=CENTER, light=False,
          tabular=False) -> QLabel:
    lab = QLabel(text)
    lab.setFont(T.font(size, bold, light=light, tabular=tabular))
    lab.setWordWrap(wrap)
    lab.setAlignment(align)
    if color:
        lab.setStyleSheet(f"color: {color};")
    return lab


def button(text, kind="", min_h=52) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setMinimumHeight(T.pt(min_h))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    # No keyboard focus: the spacebar is reserved for the emergency stop.
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


def back_button(min_h=56) -> QPushButton:
    """Left-arrow Back, at the left of a page's action row."""
    b = button("Back", "secondary", min_h)
    b.setIcon(qicon("arrow_left", T.pt(24), T.TEXT))
    b.setIconSize(QSize(T.pt(24), T.pt(24)))
    b.setMinimumWidth(T.pt(150))
    return b


def skip_button(min_h=56) -> QPushButton:
    """Skip exercise: straight on to the next exercise of the routine."""
    b = button("Skip exercise", "secondary", min_h)
    b.setIcon(qicon("skip", T.pt(24), T.TEXT))
    b.setIconSize(QSize(T.pt(24), T.pt(24)))
    return b


class AutoAdvance:
    """One-second countdown that calls `done` at zero, so the routine runs hands-free. It only
    counts while its page is on screen and stops when the page is left."""

    def __init__(self, owner: QWidget, done, tick=None):
        self.owner, self.done, self.tick = owner, done, tick
        self.left = 0
        self.timer = QTimer(owner)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._step)

    @property
    def active(self) -> bool:
        return self.timer.isActive()

    def start(self, seconds: int):
        self.left = int(seconds)
        self.timer.start()
        if self.tick:
            self.tick(self.left)

    def stop(self):
        was = self.timer.isActive()
        self.timer.stop()
        self.left = 0
        if was and self.tick:
            self.tick(0)

    def _step(self):
        if not self.owner.isVisible():
            self.stop()
            return
        self.left -= 1
        if self.tick:
            self.tick(self.left)
        if self.left <= 0:
            self.timer.stop()
            self.done()


def card(layout_cls=QVBoxLayout, margins=(24, 20, 24, 20), spacing=12, kind="card"):
    f = QFrame()
    f.setObjectName(kind)
    lay = layout_cls(f)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return f, lay


def lift(w: QWidget, blur=24, dy=4, alpha=40):
    """Soft drop shadow so a card or button sits above the page (light theme)."""
    fx = QGraphicsDropShadowEffect(w)
    fx.setBlurRadius(blur)
    fx.setOffset(0, dy)
    fx.setColor(T.qcolor(T.TEXT, alpha))
    w.setGraphicsEffect(fx)


class IconText(QWidget):
    """Icon + text on one line (status lines, results, warnings)."""

    def __init__(self, size=T.M, icon_px=36, bold=True, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.icon = Icon("", T.TEXT, T.pt(icon_px))
        self.text = label("", size, bold, wrap=True, align=LEFT)
        lay.addStretch(1)
        lay.addWidget(self.icon)
        lay.addWidget(self.text)
        lay.addStretch(1)

    def set(self, icon: str, text: str, color: str):
        self.icon.set_icon(icon, color)
        self.icon.setVisible(bool(icon))
        self.text.setText(text)
        self.text.setStyleSheet(f"color: {color};")

    def clear(self):
        self.set("", "", T.TEXT)


class StepDots(QWidget):
    """Wizard progress: completed, current and remaining steps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.n, self.i = 1, 0
        self.setFixedHeight(18)

    def set(self, n, i):
        self.n, self.i = n, i
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        seg, gap = 48.0, 8.0
        x0 = (self.width() - (self.n * seg + (self.n - 1) * gap)) / 2
        for k in range(self.n):
            color = QColor(T.ACCENT) if k <= self.i else QColor(T.SURFACE_2)
            if k < self.i:
                color.setAlpha(140)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(int(x0 + k * (seg + gap)), 6, int(seg), 6, 3, 3)
        p.end()


# ------------------------------------------------------------------ start

def name_field_style() -> str:
    """White borderless name field with a large chevron, sitting inside the gradient frame."""
    path = Path(tempfile.gettempdir()) / "kinesis_chevron.png"
    icon_pixmap("chevron_down", 32, T.MUTED).save(str(path))
    return f"""
    QComboBox {{ background: {T.SURFACE}; border: none; border-radius: 6px; padding: 0 {T.pt(20)}px;
                 font-size: {T.pt(T.L)}pt; color: {T.TEXT}; }}
    QComboBox:focus {{ border: none; }}
    QComboBox QLineEdit {{ background: transparent; border: none; padding: 0; font-size: {T.pt(T.L)}pt; }}
    QComboBox::drop-down {{ border: none; width: {T.pt(56)}px; }}
    QComboBox::down-arrow {{ image: url({path.as_posix()}); width: {T.pt(28)}px; height: {T.pt(28)}px; }}
    """


class StartPage(QWidget):
    start = pyqtSignal(str)

    def __init__(self, users: list[str]):
        super().__init__()
        outer = QHBoxLayout(self)
        outer.addStretch(SIDE)
        col = QVBoxLayout()
        col.setSpacing(GAP + 4)
        outer.addLayout(col, BODY)
        outer.addStretch(SIDE)
        col.addStretch(2)

        # Logo + gradient wordmark, as tall as each other.
        head = QHBoxLayout()
        head.setSpacing(GAP * 2)
        title = B.GradientTitle(B.APP_NAME, T.pt(80))
        head.addWidget(B.HeartLogo(int(title.sizeHint().height() * 1.15)), 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        col.addLayout(head)
        col.addWidget(label("Guided hand and leg rehab exercises with muscle and movement feedback.", T.L,
                            color=T.MUTED, align=LEFT))
        col.addSpacing(GAP)

        frame = B.GradientFrame()
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(24, 22, 24, 22)
        self.user = QComboBox()
        self.user.setEditable(True)
        self.user.addItems(users)
        self.user.setCurrentIndex(-1)        # show the prompt; saved names are in the list
        self.user.setMinimumHeight(T.pt(64))
        self.user.lineEdit().setPlaceholderText("Type your name...")
        self.user.setStyleSheet(name_field_style())
        fl.addWidget(self.user)
        col.addWidget(frame)
        info, il = card(QHBoxLayout, margins=(40, 24, 40, 24), spacing=GAP * 2, kind="info")
        il.addWidget(Icon("info", T.ACCENT, T.pt(32)), 0, Qt.AlignmentFlag.AlignTop)
        il.addWidget(label(DISCLAIMER, T.M, color=T.TEXT, align=LEFT), 1)
        col.addWidget(info)
        col.addSpacing(GAP)
        self.go = button("I understand, start", "good", 84)
        self.go.setStyleSheet(f"font-size: {T.pt(T.XL)}pt; border-radius: 12px;")
        self.go.clicked.connect(self._go)
        lift(self.go, blur=28, dy=8, alpha=70)
        col.addWidget(self.go)
        col.addStretch(3)

    def set_users(self, users: list[str]):
        self.user.clear()
        self.user.addItems(users)
        self.user.setCurrentIndex(-1)

    def _go(self):
        name = self.user.currentText().strip()
        if name:
            self.start.emit(name)
        else:
            self.user.setFocus()


# ------------------------------------------------------------------ exercise choice

IMAGES = Path(__file__).with_name("images")


class Photo(QWidget):
    """A photo scaled to fit (never cropped), centred, with rounded corners."""

    def __init__(self, path: Path):
        super().__init__()
        self.pixmap = QPixmap(str(path))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(1, 1)

    def paintEvent(self, ev):
        if self.pixmap.isNull():
            return
        dpr = self.devicePixelRatioF()
        img = self.pixmap.scaled(self.size() * dpr, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        img.setDevicePixelRatio(dpr)
        sz = img.deviceIndependentSize()
        r = QRectF((self.width() - sz.width()) / 2, (self.height() - sz.height()) / 2, sz.width(), sz.height())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(r, 10, 10)
        p.setClipPath(clip)
        p.drawPixmap(r.topLeft(), img)
        p.end()


def sensor_picture(ex: EX.Exercise) -> QWidget:
    """Photo of the device worn for this exercise: images/sensor_<key>.png if there is one
    (e.g. sensor_calf_raise.png), else the limb's photo (sensor_arm.png, sensor_leg.png), else
    the drawn placement diagram."""
    limb = IMAGES / ("sensor_leg.png" if ex.limb in ("legs", "legacy") else "sensor_arm.png")
    for path in (IMAGES / f"sensor_{ex.key}.png", limb):
        if path.exists():
            return Photo(path)
    return placement_diagram(ex.placement)


class ChoiceImage(QWidget):
    """Picture at the top of a choice card: images/<name>.png when that file exists, otherwise a
    still of the exercise's own side-view drawing (limb_view) at angle `theta`."""

    # Part of each 1000 x 600 scene worth showing (x, y, w, h in scene units).
    FRAME = {"arm": (80, 120, 700, 300), "knee": (60, 110, 800, 480), "hip": (60, 50, 640, 540),
             "scapula": (230, 100, 760, 420), "curl": (300, 40, 560, 520), "pendulum": (160, 140, 840, 450),
             "quad_set": (220, 300, 590, 200), "leg_raise": (220, 220, 600, 280), "knee_emg": (90, 140, 720, 450),
             "calf_raise": (300, 140, 400, 440)}

    def __init__(self, name: str, view: str = "arm", theta: float = 0.0, weight: bool = False):
        super().__init__()
        path = IMAGES / f"{name}.png"
        self.photo = QPixmap(str(path)) if path.exists() else QPixmap()
        self.scene = limb_view(view)
        self.scene.setMinimumSize(0, 0)
        self.scene.weight = weight
        self.scene.load_kg = 2.0 if weight else 0.0
        self.scene.compact = True          # a small picture: leave out in-scene captions
        self.frame = QRectF(*self.FRAME.get(view, self.FRAME["arm"]))
        self.theta = theta
        self._cache: QPixmap | None = None
        self.setFixedHeight(T.pt(190))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)    # clicks go to the card

    def _render(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        if self._cache is not None and self._cache.size() == self.size() * dpr:
            return self._cache
        pm = QPixmap(self.size() * dpr)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        plate = QPainterPath()
        plate.addRoundedRect(QRectF(self.rect()), 10, 10)
        p.setClipPath(plate)
        if not self.photo.isNull():
            img = self.photo.scaled(self.size() * dpr, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
            img.setDevicePixelRatio(dpr)
            sz = img.deviceIndependentSize()
            p.drawPixmap(QPointF((self.width() - sz.width()) / 2, (self.height() - sz.height()) / 2), img)
        else:
            # The scene paints on white (its fades blend into white), so it sits on a white plate.
            p.fillPath(plate, QColor(T.SURFACE))
            v, fr = self.scene, self.frame
            pad = 12
            s = min((self.width() - 2 * pad) / fr.width(), (self.height() - 2 * pad) / fr.height())
            # Size the scene so its own scale (hand pixmap, label fonts) matches this one.
            v.resize(int(v.W * s) + 1, int(v.H * s) + 1)
            p.translate((self.width() - fr.width() * s) / 2, (self.height() - fr.height() * s) / 2)
            p.scale(s, s)
            p.translate(-fr.left(), -fr.top())
            v._transform()
            v._paint_static(p)
            v._flexors(p, self.theta)
            v._draw_limb(p, self.theta, ghost=False)
        p.end()
        self._cache = pm
        return pm

    def paintEvent(self, ev):
        p = QPainter(self)
        p.drawPixmap(0, 0, self._render())
        p.end()


class ChoiceCard(QFrame):
    """A large clickable card: picture, display-font title and one line of explanation."""
    clicked = pyqtSignal()

    def __init__(self, image: QWidget, title: str, text: str, clickable: bool = True,
                 title_pt: float = 40, text_size: float = T.L):
        super().__init__()
        self.setObjectName("choice" if clickable else "choice_static")
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 28)
        lay.setSpacing(10)
        lay.addWidget(image)
        head = label(title, color=T.INK)
        head.setFont(B.title_font(T.pt(title_pt)))
        lay.addWidget(head)
        lay.addWidget(label(text, text_size, color=T.HEADING))
        lay.addStretch(1)
        lift(self, blur=32, dy=8, alpha=60)

    def mouseReleaseEvent(self, ev):
        if self.rect().contains(ev.position().toPoint()):
            self.clicked.emit()


class ChoicePage(QWidget):
    """Display-font question, two or more large picture cards, and Back. Emits the chosen key."""
    chosen = pyqtSignal(str)
    back = pyqtSignal()

    def __init__(self, question: str, options: list[tuple[str, QWidget, str, str]]):
        """options: (key, picture, title, text)."""
        super().__init__()
        outer = QHBoxLayout(self)
        outer.addStretch(SIDE)
        col = QVBoxLayout()
        col.setSpacing(GAP * 2)
        outer.addLayout(col, BODY)
        outer.addStretch(SIDE)
        col.addStretch(2)
        self.question = label(question, color=T.HEADING)
        self.question.setFont(B.title_font(T.pt(44)))
        col.addWidget(self.question)
        row = QHBoxLayout()
        row.setSpacing(GAP * 6)
        self.cards = {}
        for key, image, title, text in options:
            c = ChoiceCard(image, title, text)
            c.clicked.connect(lambda k=key: self.chosen.emit(k))
            self.cards[key] = c
            row.addWidget(c, 1)
        col.addLayout(row)
        # Back sits under the first card, inset from its edge and about 60 % of its width.
        brow = QHBoxLayout()
        brow.setSpacing(GAP * 6)
        cell = QHBoxLayout()
        cell.setContentsMargins(T.pt(24), 0, 0, 0)
        back = button("Back", "outline", 72)
        back.clicked.connect(self.back.emit)
        cell.addWidget(back, 3)
        cell.addStretch(2)
        brow.addLayout(cell, 1)
        brow.addStretch(len(options) - 1)
        col.addLayout(brow)
        col.addStretch(3)


NUMBER_WORDS = {2: "Two", 3: "Three", 4: "Four", 5: "Five"}


def limb_page() -> ChoicePage:
    arms, legs = EX.ROUTINES["arms"], EX.ROUTINES["legs"]
    return ChoicePage("What would you like to exercise today?",
                      [("arms", ChoiceImage("arms", "curl", 70, weight=True), "Arms",
                        f"An arm routine of {len(arms.keys)} exercises."),
                       ("legs", ChoiceImage("legs", "leg_raise", 12), "Legs",
                        f"A leg routine of {len(legs.keys)} exercises.")])


# Card pictures for the routine exercises: (view, pose shown: degrees, or 0..1 effort).
ROUTINE_POSES = {"scapular_retraction": ("scapula", 15), "bicep_curl": ("curl", 70), "pendulum": ("pendulum", 18),
                 "quad_set": ("quad_set", 1.0), "straight_leg_raise": ("leg_raise", 12),
                 "seated_knee_extension": ("knee_emg", 85), "calf_raise": ("calf_raise", 1.0)}


class RoutinePage(QWidget):
    """A routine (arms or legs): its exercises in order with their rep counts, Start and Back."""
    start = pyqtSignal()
    back = pyqtSignal()

    def __init__(self, routine: EX.Routine):
        super().__init__()
        outer = QHBoxLayout(self)
        outer.addStretch(SIDE)
        col = QVBoxLayout()
        col.setSpacing(GAP * 2)
        outer.addLayout(col, BODY)
        outer.addStretch(SIDE)
        col.addStretch(2)
        q = label(f"Your {routine.title.lower()}", color=T.HEADING)
        q.setFont(B.title_font(T.pt(44)))
        col.addWidget(q)
        n = len(routine.keys)
        col.addWidget(label(f"{NUMBER_WORDS.get(n, n)} exercises, one after another. The guide shows each "
                            "movement and counts your good reps.", T.L, color=T.HEADING))
        row = QHBoxLayout()
        row.setSpacing(GAP * 3 if n <= 3 else GAP * 2)
        for i, key in enumerate(routine.keys, 1):
            ex = EX.EXERCISES[key]
            view, theta = ROUTINE_POSES.get(key, (ex.view, 0))
            count, sets, per_set = EX.session_reps(key)
            row.addWidget(ChoiceCard(ChoiceImage(key, view, theta, weight=ex.uses_weight),
                                     f"{i}. {ex.title}", f"{count} reps\n{sets} sets of {per_set}",
                                     clickable=False, title_pt=26 if n <= 3 else 22), 1)
        col.addLayout(row)
        brow = QHBoxLayout()
        brow.setSpacing(GAP * 3)
        # Same size and the same type for both buttons; only the colours differ.
        same = f"font-size: {T.pt(T.L)}pt; font-weight: 600; border-radius: 12px;"
        back = button("Back", "outline", 72)
        back.setStyleSheet(same)
        back.clicked.connect(self.back.emit)
        go = button("Start the routine", "good", 72)
        go.setStyleSheet(same)
        go.clicked.connect(self.start.emit)
        lift(go, blur=28, dy=8, alpha=70)
        for b in (back, go):
            b.setFixedHeight(T.pt(72))
        brow.addWidget(back, 1)
        brow.addWidget(go, 1)
        col.addLayout(brow)
        col.addStretch(3)


# ------------------------------------------------------------------ get ready / load confirmation

class LoadPage(QWidget):
    """Safety check before calibration. With a weight: confirm the load. Without: how to sit."""
    confirmed = pyqtSignal(float, bool)     # load kg, full calibration requested
    back = pyqtSignal()
    skip = pyqtSignal()
    AUTO_S = 10                             # starts by itself after this long on screen

    def __init__(self, cfg: dict, ex: EX.Exercise | None = None):
        super().__init__()
        ex = ex or EX.get(None)
        words = EX.texts(ex)
        self.uses_weight = ex.uses_weight
        self.min_kg = 0.0 if ex.assisted else 0.5     # 0 kg = assisted by the other hand
        self.warn_kg = cfg["exercise"]["load_warning_kg"]
        self.kg = float(cfg["exercise"]["load_kg"])
        outer = QHBoxLayout(self)
        outer.addStretch(SIDE)
        col = QVBoxLayout()
        col.setSpacing(GAP + 8)
        outer.addLayout(col, BODY)
        outer.addStretch(SIDE)
        col.addStretch(2)
        col.addWidget(section_label(f"Safety check · {ex.title}"))
        col.addWidget(label(words["ready_title"], T.XL, True, align=LEFT))
        col.addWidget(label(words["ready_text"], T.M, color=T.MUTED, align=LEFT))
        box, bl = card(QHBoxLayout, margins=(24, 16, 24, 16), spacing=GAP)
        box.setVisible(ex.uses_weight)
        minus, plus = button("−", "stepper", 72), button("+", "stepper", 72)
        for b in (minus, plus):
            b.setFixedWidth(T.pt(88))
        minus.clicked.connect(lambda: self._change(-0.5))
        plus.clicked.connect(lambda: self._change(0.5))
        self.value = label("", T.XXL, light=True, tabular=True)
        bl.addWidget(minus)
        bl.addWidget(self.value, 1)
        bl.addWidget(plus)
        col.addWidget(box)
        self.warning = IconText(T.S, 30, bold=False)
        col.addWidget(self.warning)
        col.addSpacing(GAP)
        # Returning users get the short calibration; this note and button offer the full one.
        self.quick_note = label("Short calibration: sensors, relax, start position and practice. "
                                "Choose Full calibration to redo the lift test.", T.S, color=T.MUTED, align=LEFT)
        col.addWidget(self.quick_note)
        row = QHBoxLayout()
        row.setSpacing(GAP)
        self.full_btn = button("Full calibration", "secondary", 56)
        self.full_btn.clicked.connect(lambda: self.confirmed.emit(self.kg, True))
        ok = button(words["ready_button"], "good", 56)
        ok.clicked.connect(lambda: self.confirmed.emit(self.kg, False))
        back = back_button()
        back.clicked.connect(self.back.emit)
        skip = skip_button()
        skip.clicked.connect(self.skip.emit)
        row.addWidget(back)
        row.addWidget(skip)
        row.addWidget(self.full_btn, 1)
        row.addWidget(ok, 2)
        col.addLayout(row)
        self.auto_note = label("", T.S, color=T.ACCENT, align=LEFT)
        col.addWidget(self.auto_note)
        col.addStretch(3)
        self.auto = AutoAdvance(self, lambda: self.confirmed.emit(self.kg, False), self._auto_tick)
        self._change(0)
        self.set_returning(False)

    def _auto_tick(self, left: int):
        self.auto_note.setText(f"Starting automatically in {left} s." if left > 0 else "")

    def showEvent(self, ev):
        super().showEvent(ev)
        self.auto.start(self.AUTO_S)

    def hideEvent(self, ev):
        self.auto.stop()
        super().hideEvent(ev)

    def set_kg(self, kg: float):
        self.kg = kg
        self._change(0)

    def set_returning(self, returning: bool):
        """Show the full-calibration choice only when a saved calibration would be reused."""
        self.quick_note.setVisible(returning)
        self.full_btn.setVisible(returning)

    def _change(self, d):
        if d and self.auto.active:
            self.auto.start(self.AUTO_S)     # a weight change gives the full time again
        if not self.uses_weight:
            self.kg = 0.0
            return
        self.kg = max(self.min_kg, min(10.0, self.kg + d))
        self.value.setText(f"{self.kg:g} kg" if self.kg > 0 else "No weight · assisted")
        if self.kg == 0:
            self.warning.set("info", "Hold your wrist with your other hand and help the lift.", T.ACCENT)
        elif self.kg > self.warn_kg:
            self.warning.set("warning", f"{self.kg:g} kg is heavy for this exercise for many people. "
                             "If you are not sure, use a lighter weight and ask your therapist.", T.WARN)
        else:
            self.warning.clear()


# ------------------------------------------------------------------ calibration wizard

class WizardPage(QWidget):
    finished = pyqtSignal()
    back = pyqtSignal()             # Back on the first step: leave calibration
    skip = pyqtSignal()
    READ_S = 5                      # time to read a step before it starts by itself
    RETRY_S = 5                     # a failed step is tried again by itself after this long

    def __init__(self, cfg: dict, worker, clock: PacerClock, sim: bool, ex: EX.Exercise | None = None):
        super().__init__()
        ex = ex or EX.get(None)
        self.ex = ex
        self.step_info = EX.steps(ex)
        self.captions = EX.captions(ex)
        self.cfg = cfg
        self.cal = cfg["calibration"]
        self.worker = worker
        self.clock = clock
        self.sim = sim
        self.steps: list[str] = []
        self.idx = 0
        self.phase = "instruct"
        self.t_phase = 0.0
        self.target = cfg["exercise"]["target_deg"]
        self.practice_count = 0
        self.live_theta = 0.0
        self.live_pct = float("nan")
        self.connected = False

        # Every row has a fixed height and only the visual card stretches, so showing the pacer,
        # a countdown or a result never pushes the buttons off a short screen.
        lay = QVBoxLayout(self)
        lay.setContentsMargins(MARGIN, 16, MARGIN, 16)
        lay.setSpacing(12)
        self.dots = StepDots()
        lay.addWidget(self.dots)
        self.header = label("", T.XS, color=T.MUTED)
        self.header.setFont(T.font(T.XS, caps=True))
        lay.addWidget(self.header)
        self.title = label("", T.XL, True)
        self.instruction = label("", T.M, color=T.TEXT)
        lay.addWidget(self.title)
        lay.addWidget(self.instruction)
        vis, vl = card(QHBoxLayout, margins=(8, 8, 24, 8), spacing=GAP)
        self.visual = QStackedWidget()
        self.visual.setStyleSheet("background: transparent;")    # let the card colour show through
        self.diagram = sensor_picture(ex)
        self.arm = limb_view(ex.view)
        self.arm.weight = ex.uses_weight
        words = EX.texts(ex)
        self.arm.angle_label, self.arm.muscle_label = words["angle_label"], words["muscle_label"]
        self.visual.addWidget(self.diagram)
        self.visual.addWidget(self.arm)
        vl.addWidget(self.visual, 1)
        # The pacer sits beside the arm (the card has spare width), not below it.
        self.pacer = PacerWidget(clock)
        self.pacer.setFixedWidth(T.pt(440))
        vl.addWidget(self.pacer, 0, Qt.AlignmentFlag.AlignVCenter)
        vis.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(vis, 1)
        self.countdown = label("", T.XL, light=True, tabular=True, color=T.ACCENT, wrap=False)
        self.countdown.setFixedHeight(QFontMetrics(self.countdown.font()).height() + 4)
        lay.addWidget(self.countdown)
        self.result_row = IconText(T.L, 32)
        self.result = self.result_row.text
        self.result.setWordWrap(False)
        self.result_row.setFixedHeight(max(QFontMetrics(self.result.font()).height(), T.pt(32)) + 4)
        lay.addWidget(self.result_row)
        # Fixed-height button bar: it keeps its space even while practice hides every button.
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(GAP)
        self.back_btn = back_button()
        self.skip_btn = skip_button()
        self.start_btn = button("Start now", "good", 56)
        self.retry_btn = button("Try again now", "secondary", 56)
        self.next_btn = button("Next", "good", 56)
        row.addWidget(self.back_btn)
        row.addWidget(self.skip_btn)
        for b in (self.start_btn, self.retry_btn, self.next_btn):
            row.addWidget(b, 1)
        bar.setFixedHeight(self.start_btn.sizeHint().height())
        lay.addWidget(bar)
        self.back_btn.clicked.connect(self._back)
        self.skip_btn.clicked.connect(self.skip.emit)
        # Hands-free: each step starts by itself, and a failed one is retried by itself.
        self.auto = AutoAdvance(self, self._start_step, self._auto_tick)
        self.start_btn.clicked.connect(self._start_step)
        self.retry_btn.clicked.connect(self._start_step)
        self.next_btn.clicked.connect(self._next)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)
        self.advance = QTimer(self)
        self.advance.setSingleShot(True)
        self.advance.timeout.connect(self._next)

    # ------------------------------------------------------------ flow
    def begin(self, steps: list[str], target: float, load_kg: float):
        self.steps = steps
        self.target = target
        self.arm.load_kg = load_kg
        if emg_driven(self.cfg):
            self.arm.min_activation = target      # the muscle bar marks the effort target
        self.idx = 0
        self._show_step()

    @property
    def step(self) -> str:
        return self.steps[self.idx]

    def _duration(self, step) -> float:
        c = self.cal
        return {"sensor_check": c["sensor_check_s"], "rest": c["rest_s"], "zero": c["zero_s"],
                "top": c["top_move_s"] + c["top_hold_s"], "rvc": c["rvc_s"]}.get(step, 0)

    def _show_step(self):
        step = self.step
        self.phase = "instruct"
        self.t_phase = time.monotonic()
        title, text = self.step_info[step]
        if step == "zero" and self.cal["start_position"] == "extension" and self.ex.view == "arm":
            text = "Hold the weight, palm up. Let your hand drop back a little (about 15°). Hold still."
        self.dots.set(len(self.steps), self.idx)
        self.header.setText(f"Calibration · step {self.idx + 1} of {len(self.steps)}")
        self.title.setText(title)
        self.instruction.setText(text)
        self.visual.setCurrentWidget(self.diagram if step == "sensor_check" else self.arm)
        self.pacer.setVisible(step == "practice")
        self.countdown.setText("")
        self.result_row.clear()
        self.start_btn.setVisible(True)
        self.retry_btn.setVisible(False)
        self.next_btn.setVisible(False)
        self.worker.call("hint", step)
        self.auto.start(self.READ_S)

    def _auto_tick(self, left: int):
        if self.phase in ("instruct", "result"):
            word = "Trying again" if self.phase == "result" else "Starting"
            self.countdown.setText(f"{word} in {left}…" if left > 0 else "")

    def _start_step(self):
        self.auto.stop()
        self.start_btn.setVisible(False)
        self.retry_btn.setVisible(False)
        self.result_row.clear()
        self.worker.call("hint", self.step)
        if self.step == "practice":
            self.practice_count = 0
            self.phase = "practice"
            self.worker.call("session_action", "practice")
            if self.sim:
                self.worker.call("queue_reps", ["good"] * self.cal["practice_reps"])
            self.countdown.setText(f"Practice rep 0 of {self.cal['practice_reps']}")
            return
        self.phase = "ready"
        self.t_phase = time.monotonic()

    def _begin_capture(self):
        self.phase = "capture"
        self.t_phase = time.monotonic()
        self.worker.call("start_capture", self.step, self._duration(self.step))

    def restart_step(self):
        self.advance.stop()
        self._show_step()

    def hideEvent(self, ev):
        self.auto.stop()
        super().hideEvent(ev)

    def _back(self):
        """Previous step (redone from its instruction), or leave calibration from the first."""
        self.advance.stop()
        self.auto.stop()
        self.worker.call("cancel_capture")
        if self.phase == "practice":
            self.worker.call("session_action", "setup")
        if self.idx == 0:
            self.phase = "instruct"
            self.back.emit()
            return
        self.idx -= 1
        self._show_step()

    def _next(self):
        self.advance.stop()
        self.auto.stop()
        if self.idx + 1 >= len(self.steps):
            self.finished.emit()
            return
        self.idx += 1
        self._show_step()

    # ------------------------------------------------------------ worker signals
    def on_calibration(self, step, res):
        if not self.steps or step != self.step:
            return
        self.phase = "result"
        if step != "practice":       # keep "Practice rep 3 of 3" on screen with the result
            self.countdown.setText("")
        if res.passed:
            self.result_row.set("check", res.message, T.GOOD)
            if step == "top":
                self.target = res.values.get("target_deg", self.target)
            self.next_btn.setVisible(True)
            self.advance.start(1800)
        else:
            self.result_row.set("cross", res.message, T.BAD)
            self.retry_btn.setVisible(True)
            self.auto.start(self.RETRY_S)

    def on_frames(self, frames):
        if frames:
            f = frames[-1][0]
            self.live_theta, self.live_pct = f.theta, f.env_pct

    def on_rep(self, rep):
        if self.phase != "practice" or not rep.practice:
            return
        self.practice_count += 1
        if rep.valid:
            self.result_row.set("check", "Good", T.GOOD)
        else:
            self.result_row.set(fault_icon(rep.primary_fault), rep.message, T.WARN)
        self.countdown.setText(f"Practice rep {self.practice_count} of {self.cal['practice_reps']}")

    def on_status(self, st):
        self.connected = st.get("connected", False)

    # ------------------------------------------------------------ animation
    def _tick(self):
        if not self.steps or not self.isVisible():
            return
        now = time.monotonic()
        el = now - self.t_phase
        step = self.step
        if self.phase == "ready":
            left = 3 - int(el)
            self.countdown.setText(f"Get ready  {left}" if left > 0 else "")
            if el >= 3:
                self._begin_capture()
        elif self.phase == "capture":
            dur = self._duration(step)
            left = max(0, math.ceil(dur - el))
            if step == "top":
                cue = self.captions["top_cue"] if el < self.cal["top_move_s"] else "Hold still"
                self.countdown.setText(f"{cue}  {left}")
            else:
                self.countdown.setText(f"Hold still  {left}" if step != "sensor_check" else f"Checking  {left}")
        if step == "sensor_check":
            return
        # Demonstration pose for this step; the live limb appears once it is calibrated.
        arm = self.arm
        arm.target = self.target
        arm.dumbbell = step != "rest"     # hand: weight (or fist) in every step but relax
        arm.env_pct = self.live_pct
        cap = self.captions
        if step in ("rest", "zero"):
            arm.set_state(demo=True, demo_theta=0.0, ghost=None, caption=cap[step])
        elif step == "top":
            x = (now % 6.0)
            th = self.target * min(1.0, x / 2.0) if x < 5 else self.target * (1 - (x - 5))
            if self.phase == "capture":
                # The flexion direction is only learned from this very step, so show the size of
                # the movement rather than a possibly negative angle.
                arm.set_state(demo=False, theta=abs(self.live_theta), ghost=self.target, caption="")
            else:
                arm.set_state(demo=True, demo_theta=th, ghost=None, caption=cap["top"])
        elif step == "rvc":
            if self.phase == "capture":
                arm.set_state(demo=False, theta=self.live_theta, ghost=self.target / 2, caption="")
            else:
                arm.set_state(demo=True, demo_theta=self.target / 2, ghost=None, caption=cap["rvc"])
        elif step == "practice":
            self.clock.target = self.target
            name, _, ghost, _ = self.clock.phase(now)
            if self.phase == "practice":
                arm.set_state(demo=False, theta=self.live_theta, ghost=ghost, caption="")
            else:
                arm.set_state(demo=True, demo_theta=ghost, ghost=None, caption=self.clock.captions[name])
            self.pacer.update()


# ------------------------------------------------------------------ exercise

class ExercisePage(QWidget):
    stop_requested = pyqtSignal()
    back = pyqtSignal()
    skip = pyqtSignal()

    def __init__(self, cfg: dict, clock: PacerClock, ex: EX.Exercise | None = None):
        super().__init__()
        ex = ex or EX.get(None)
        self.cfg = cfg
        self.clock = clock
        self.reps_target = cfg["exercise"]["reps_per_set"]
        self.valid = self.attempts = 0
        self.last_move = time.monotonic()
        self.reps_this_set = 0
        self.prev_state = "IDLE"
        self.theta = 0.0
        self.pct = float("nan")
        self.frame_count = 0
        self._plot_tick = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 12, 24, 12)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(GAP)
        nav = QVBoxLayout()
        nav.setSpacing(8)
        back = back_button(44)
        back.clicked.connect(self.back.emit)
        skip = skip_button(44)
        skip.clicked.connect(self.skip.emit)
        nav.addWidget(back)
        nav.addWidget(skip)
        top.addLayout(nav)
        info, il = card(margins=(20, 14, 20, 14), spacing=4)
        il.addWidget(section_label("Set"))
        self.set_label = label("Set 1 of 3", T.L, True, align=LEFT)
        il.addWidget(self.set_label)
        self.small = label("Attempts 0  ·  Not counted 0", T.S, color=T.MUTED, align=LEFT, tabular=True)
        il.addWidget(self.small)
        self.routine_clock = label("", T.S, True, color=T.ACCENT, align=LEFT, tabular=True)
        self.routine_clock.setVisible(False)
        il.addWidget(self.routine_clock)
        top.addWidget(info, 3)
        cnt, cl = card(margins=(20, 8, 20, 8), spacing=0)
        cl.addWidget(section_label("Good reps"), 0, CENTER)
        self.count = label("0 / 12", 44, light=True, tabular=True)
        self.count.setStyleSheet(f"color: {T.TEXT};")
        cl.addWidget(self.count)
        top.addWidget(cnt, 3)
        self.stop_btn = button("STOP", "stop", 72)
        self.stop_btn.setMinimumWidth(T.pt(160))
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        top.addWidget(self.stop_btn, 2)
        root.addLayout(top)

        mid = QHBoxLayout()
        mid.setSpacing(GAP)
        arm_card, al = card(margins=(8, 8, 8, 8))
        self.arm = limb_view(ex.view)
        self.arm.weight = ex.uses_weight
        words = EX.texts(ex)
        self.arm.angle_label, self.arm.muscle_label = words["angle_label"], words["muscle_label"]
        self.arm.min_activation = cfg["faults"]["min_activation_pct"]
        al.addWidget(self.arm)
        mid.addWidget(arm_card, 3)
        side = QVBoxLayout()
        side.setSpacing(12)
        pc, pl = card(margins=(20, 10, 20, 10))
        self.pacer = PacerWidget(clock)
        pl.addWidget(self.pacer)
        side.addWidget(pc)
        self.banner = MessageBanner()
        side.addWidget(self.banner)
        self.meter = FatigueMeter()
        self.meter.set_ticks(cfg["fatigue"]["amber"], cfg["fatigue"]["red"])
        side.addWidget(self.meter)
        side.addStretch(1)
        mid.addLayout(side, 2)
        root.addLayout(mid, 5)

        self.graphs, gl = card(QHBoxLayout, margins=(12, 10, 12, 10), spacing=GAP)
        self.plots = LivePlots(cfg)
        gl.addWidget(self.plots, 3)
        charts = QVBoxLayout()
        self.fatigue_chart = FatigueChart(cfg)
        charts.addWidget(self.fatigue_chart)
        self.mdf_chart = TrendChart("MDF (Hz)")
        self.mdf_chart.setVisible(cfg["gui"]["show_mdf_trend"])
        charts.addWidget(self.mdf_chart)
        gl.addLayout(charts, 2)
        self.graphs.setVisible(cfg["gui"]["show_graphs"])
        root.addWidget(self.graphs, 3)
        self.mdf_pts = []

    def reset_for_set(self, set_no: int, sets: int, target_deg: float, load_kg: float):
        self.valid = self.attempts = self.reps_this_set = 0
        self.set_label.setText(f"Set {set_no} of {sets}")
        self.arm.target = target_deg
        if emg_driven(self.cfg):
            self.arm.min_activation = target_deg  # the muscle bar marks the effort target
        self.arm.load_kg = load_kg
        self.clock.target = target_deg
        self.plots.set_target(target_deg)
        self._update_counts()
        self.banner.neutral(MessageBanner.NEUTRAL)

    def set_routine_clock(self, text: str):
        """Routine time line in the set card ('' hides it outside a routine)."""
        self.routine_clock.setText(text)
        self.routine_clock.setVisible(bool(text))

    def _update_counts(self):
        self.count.setText(f"{self.valid} / {self.reps_target}")
        self.small.setText(f"Attempts {self.attempts}  ·  Not counted {self.attempts - self.valid}")

    def on_frames(self, frames):
        if not frames:
            return
        self.plots.add_frames(frames)
        now = time.monotonic()
        moving_dps = self.cfg["gui"]["idle_omega_dps"]
        for f, state in frames:
            if abs(f.omega) > moving_dps or state in ("LIFTING", "TOP_HOLD", "LOWERING"):
                self.last_move = now
            if state == "LIFTING" and self.prev_state != "LIFTING":
                # Pacer + ghost follow the patient's own start. The lift is detected some
                # degrees up, so place the guide where its lift reaches this angle.
                self.clock.sync(now - self.clock.lift_elapsed(f.theta))
            self.prev_state = state
        f = frames[-1][0]
        self.theta, self.pct = f.theta, f.env_pct
        self.frame_count += len(frames)

    def on_rep(self, rep: RepRecord):
        if rep.practice:
            return
        self.attempts += 1
        self.reps_this_set += 1
        secs = self.cfg["feedback"]["message_s"]
        if rep.valid:
            self.valid += 1
            self.banner.show_message("check", "Good", T.GOOD, max(1.0, secs * 0.6))
        else:
            self.banner.show_message(fault_icon(rep.primary_fault), rep.message, T.BAD, max(2.0, secs))
        self._update_counts()
        self.plots.add_rep(rep)
        if rep.valid:
            self.fatigue_chart.add(rep.index, rep.fatigue_score)
            self.meter.set_score(rep.fatigue_score, rep.fatigue_zone)
            self.mdf_pts.append((rep.index, rep.features.get("mdf", float("nan"))))
            self.mdf_chart.set_points(self.mdf_pts)

    def tick(self):
        now = time.monotonic()
        idle_s = self.cfg["gui"]["idle_demo_s"]
        still_for = now - self.last_move
        demo = still_for > idle_s or (self.reps_this_set == 0 and still_for > 0.7)
        name, _, ghost, _ = self.clock.phase(now)
        self.arm.phase_text = "Rest phase" if name == "pause" else "Active phase"
        if demo:
            self.arm.set_state(demo=True, demo_theta=ghost, caption=self.clock.captions[name],
                               env_pct=self.pct)
        else:
            self.arm.set_state(demo=False, theta=self.theta, ghost=ghost, caption="", env_pct=self.pct)
        self.pacer.update()
        if self.banner.expired() and self.banner.text.text() != MessageBanner.NEUTRAL:
            self.banner.neutral(MessageBanner.NEUTRAL)
        self._plot_tick += 1
        if self._plot_tick % 2 == 0 and self.graphs.isVisible():
            self.plots.redraw()


# ------------------------------------------------------------------ rest / pause

class _MessagePage(QWidget):
    """Centered icon, title, explanation, Back, Skip exercise and two actions."""
    back = pyqtSignal()
    skip = pyqtSignal()

    def __init__(self, secondary_text: str, primary_text: str):
        super().__init__()
        outer = QHBoxLayout(self)
        outer.addStretch(SIDE)
        col = QVBoxLayout()
        col.setSpacing(GAP + 8)
        outer.addLayout(col, BODY)
        outer.addStretch(SIDE)
        col.addStretch(2)
        self.icon = Icon("info", T.ACCENT, T.pt(56))
        col.addWidget(self.icon, 0, CENTER)
        self.title = label("", T.XL, True)
        self.text = label("", T.M, color=T.MUTED)
        col.addWidget(self.title)
        col.addWidget(self.text)
        self.body = QVBoxLayout()
        col.addLayout(self.body)
        col.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(GAP)
        back = back_button()
        back.clicked.connect(self.back.emit)
        skip = skip_button()
        skip.clicked.connect(self.skip.emit)
        self.secondary = button(secondary_text, "secondary", 56)
        self.primary = button(primary_text, "good", 56)
        row.addWidget(back)
        row.addWidget(skip)
        row.addWidget(self.secondary)
        row.addWidget(self.primary)
        col.addLayout(row)
        col.addStretch(2)


class RestPage(_MessagePage):
    next_set = pyqtSignal()
    end_session = pyqtSignal()

    def __init__(self, ex: EX.Exercise | None = None):
        super().__init__("End for today", "Start next set")
        self.words = EX.texts(ex or EX.get(None))
        self.timer_label = label("", T.XXL, light=True, tabular=True)
        self.body.addWidget(self.timer_label)
        self.end_btn, self.next_btn = self.secondary, self.primary
        self.end_btn.clicked.connect(self.end_session.emit)
        self.next_btn.clicked.connect(self.next_set.emit)
        self.until = 0.0
        self.forced = False
        self.can_continue = True
        self._auto_done = True
        self.t = QTimer(self)
        self.t.timeout.connect(self._tick)

    def begin(self, seconds: float, forced: bool, set_no: int, sets: int, can_continue: bool = True):
        self.forced = forced
        self.until = time.monotonic() + seconds
        self.can_continue = can_continue
        self._auto_done = False
        if forced:
            self.icon.set_icon("stop", T.BAD)
            self.title.setText("Please rest")
            self.text.setText("Your muscle is tired. Rest now. We suggest you end the exercise for today.")
            self.end_btn.setObjectName("good")
            self.next_btn.setObjectName("secondary")
        else:
            self.icon.set_icon("check", T.GOOD)
            self.title.setText(f"Set {set_no} of {sets} finished")
            self.text.setText(self.words["set_done"])
            self.end_btn.setObjectName("secondary")
            self.next_btn.setObjectName("good")
        for b in (self.end_btn, self.next_btn):
            b.style().unpolish(b)
            b.style().polish(b)
        self.next_btn.setVisible(can_continue)
        self._tick()
        self.t.start(250)

    def _tick(self):
        left = max(0, math.ceil(self.until - time.monotonic()))
        self.timer_label.setText(f"{left // 60}:{left % 60:02d}" if left else "Ready")
        # A forced (fatigue) rest cannot be skipped.
        self.next_btn.setEnabled(not self.forced or left == 0)
        # Hands-free: when the rest is over the next set starts by itself; after a fatigue rest
        # this exercise ends instead (the routine goes on with the next one).
        if left == 0 and not self._auto_done and self.isVisible() and self.can_continue:
            self._auto_done = True
            (self.end_session if self.forced else self.next_set).emit()


class PausePage(_MessagePage):
    resume = pyqtSignal()
    end_session = pyqtSignal()

    REASONS = {
        "emergency": ("hand", T.WARN, "Stopped",
                      "You pressed STOP. Put the weight down. Continue when you are ready."),
        "signal": ("plug", T.WARN, "Check sensor contact",
                   "The muscle signal is poor. Press the pads firmly on clean skin."),
        "disconnect": ("plug", T.BAD, "Sensor disconnected", "Check the USB cable and the sensor power."),
        "back": ("pause", T.ACCENT, "Leave this exercise?",
                 "Choose End for today to finish and see your summary, or Continue to carry on."),
    }

    def __init__(self, ex: EX.Exercise | None = None):
        super().__init__("End for today", "Continue")
        self.words = EX.texts(ex or EX.get(None))
        self.secondary.clicked.connect(self.end_session.emit)
        self.primary.clicked.connect(self.resume.emit)

    def show_reason(self, reason: str):
        icon, color, title, text = self.REASONS.get(reason, ("pause", T.ACCENT, "Paused", reason))
        if reason == "emergency":
            text = self.words["stopped"]
        self.icon.set_icon(icon, color)
        self.title.setText(title)
        self.text.setText(text)


# ------------------------------------------------------------------ summary

class SummaryPage(QWidget):
    done = pyqtSignal()
    back = pyqtSignal()             # choose another exercise

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.reps: list[RepRecord] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(MARGIN, 28, MARGIN, 28)
        lay.setSpacing(GAP)
        self.title = EX.get(cfg["exercise"].get("key")).title
        self.heading = section_label(f"Session complete · {self.title}")
        lay.addWidget(self.heading)
        lay.addWidget(label("Today's summary", T.XL, True, align=LEFT))
        grid = QGridLayout()
        grid.setSpacing(GAP)
        self.vals = {}
        self.fault_icon = Icon("", T.WARN, T.pt(40))
        for i, (key, name) in enumerate([("valid", "Good reps"), ("invalid", "Not counted"),
                                         ("fault", "Most common tip"), ("angle", "Average angle")]):
            box, bl = card(spacing=6)
            bl.addWidget(section_label(name))
            v = label("", T.XL if key in ("valid", "invalid", "angle") else T.L, True, align=LEFT, tabular=True)
            if key == "fault":
                row = QHBoxLayout()
                row.setSpacing(12)
                row.addWidget(self.fault_icon)
                row.addWidget(v, 1)
                bl.addLayout(row)
            else:
                bl.addWidget(v)
            self.vals[key] = v
            grid.addWidget(box, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        charts, cl = card(QHBoxLayout, margins=(12, 10, 12, 10), spacing=GAP)
        self.chart = FatigueChart(cfg)
        cl.addWidget(self.chart)
        self.mdf = TrendChart("MDF (Hz)")
        self.mdf.setVisible(cfg["gui"]["show_mdf_trend"])
        cl.addWidget(self.mdf)
        lay.addWidget(charts, 1)
        self.saved = label("", T.XS, color=T.MUTED, align=LEFT)
        lay.addWidget(self.saved)
        row = QHBoxLayout()
        row.setSpacing(GAP)
        back = back_button(52)
        back.clicked.connect(self.back.emit)
        exp = button("Save as CSV", "secondary")
        exp.clicked.connect(self._export)
        self.fin = button("Finish", "good")
        self.fin.clicked.connect(self.done.emit)
        row.addWidget(back)
        row.addWidget(exp)
        row.addWidget(self.fin)
        lay.addLayout(row)

    AUTO_S = 10         # between exercises the next one starts by itself after this long

    def set_routine(self, step: int, steps: int, next_title: str = "", note: str = ""):
        """Inside a routine: 'Exercise 1 of 3' and a button to the next exercise, which is
        also pressed automatically after AUTO_S seconds (the last summary stays)."""
        self.heading.setText(f"Exercise {step} of {steps} done · {self.title}")
        self.next_title = next_title
        self.fin.setText(f"Next: {next_title}" if next_title else "Finish the routine")
        self.saved.setText(note)
        if next_title:
            if not hasattr(self, "auto"):
                self.auto = AutoAdvance(self, self.done.emit, self._auto_tick)
            self.auto.start(self.AUTO_S)

    def _auto_tick(self, left: int):
        if self.next_title:
            self.fin.setText(f"Next: {self.next_title} ({left})" if left > 0 else f"Next: {self.next_title}")

    def hideEvent(self, ev):
        if hasattr(self, "auto"):
            self.auto.stop()
        super().hideEvent(ev)

    def show_summary(self, summary: dict, reps: list[RepRecord]):
        self.reps = reps
        self.vals["valid"].setText(str(summary.get("valid", 0)))
        self.vals["invalid"].setText(str(summary.get("invalid", 0)))
        fault = summary.get("most_common_fault", "")
        if fault:
            self.fault_icon.set_icon(fault_icon(fault), T.WARN)
            self.vals["fault"].setText(F.message_for(fault, self.cfg["faults"].get("messages")))
        else:
            self.fault_icon.set_icon("check", T.GOOD)
            self.vals["fault"].setText("None, great work")
        ang = summary.get("avg_peak_angle", float("nan"))
        self.vals["angle"].setText(f"{ang:.0f}°" if ang == ang else "–")
        self.chart.set_points(summary.get("fatigue_curve", []))
        self.mdf.set_points(summary.get("mdf_curve", []))

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save reps as CSV", "reps.csv", "CSV files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, RepRecord.CSV_FIELDS + RepRecord.FEATURE_FIELDS)
            w.writeheader()
            for r in self.reps:
                w.writerow(r.csv_row())
        self.saved.setText(f"Saved to {path}")
