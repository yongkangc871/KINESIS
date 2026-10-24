"""pyqtgraph plots: rolling angle/EMG with rep boundaries and fault markers; per-rep fatigue."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from . import theme as T

# Antialiasing a 2000-point curve at 30 Hz costs ~25 ms per frame in software rendering.
pg.setConfigOptions(antialias=False, background=T.SURFACE, foreground=T.MUTED, segmentedLineMode="off")

EMG_COLOR = "#b97a1c"
LABEL_STYLE = {"color": T.MUTED, "font-size": f"{T.pt(11)}pt", "font-family": T.FONT_FAMILY}


def style_plot(w: pg.PlotWidget, left: str, bottom: str | None = None):
    """Same font and quiet axes on every plot."""
    w.setBackground(T.SURFACE)
    for name in ("left", "bottom"):
        ax = w.getAxis(name)
        ax.setTickFont(T.font(10))
        ax.setPen(pg.mkPen(T.BORDER_STRONG))
        ax.setTextPen(pg.mkPen(T.MUTED))
    w.setLabel("left", left, **LABEL_STYLE)
    if bottom:
        w.setLabel("bottom", bottom, **LABEL_STYLE)
    w.setMouseEnabled(False, False)
    w.hideButtons()


def text_item(text: str, color: str, anchor=(0, 0.5)) -> pg.TextItem:
    t = pg.TextItem(text, color=color, anchor=anchor)
    t.setFont(T.font(10, bold=True))
    return t


def fast_curve(plot, **kw):
    c = plot.plot(**kw)
    c.setDownsampling(auto=True, method="peak")
    c.setClipToView(True)
    c.setSkipFiniteCheck(True)
    return c


class RollingBuffer:
    def __init__(self, n: int, channels: int):
        self.data = np.full((n, channels), np.nan)
        self.t = np.full(n, np.nan)
        self.i = 0
        self.full = False

    def extend(self, t, rows):
        for ti, r in zip(t, rows):
            self.t[self.i] = ti
            self.data[self.i] = r
            self.i = (self.i + 1) % len(self.t)
            self.full = self.full or self.i == 0

    def ordered(self):
        if not self.full:
            return self.t[:self.i], self.data[:self.i]
        return np.roll(self.t, -self.i), np.roll(self.data, -self.i, axis=0)


class LivePlots(QWidget):
    """Rolling plots. The x axis is fixed at [-window, 0] s and data are shifted to "seconds
    ago", so the axes never re-layout; that keeps a redraw around 5 ms per plot."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.window = cfg["gui"]["plot_window_s"]
        self.buf = RollingBuffer(int(self.window * cfg["imu"]["rate_hz"] * 1.2), 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.angle = pg.PlotWidget()
        self.emg = pg.PlotWidget()
        for w, label, yr in ((self.angle, "Angle (°)", (-15, 90)), (self.emg, "Muscle %", (0, 160))):
            style_plot(w, label)
            w.setViewportUpdateMode(w.ViewportUpdateMode.MinimalViewportUpdate)
            w.getPlotItem().vb.disableAutoRange()
            w.setXRange(-self.window, 0, padding=0)
            w.setYRange(*yr, padding=0)
            w.getAxis("bottom").setStyle(showValues=False)
            lay.addWidget(w)
        self.c_angle = fast_curve(self.angle, pen=pg.mkPen(T.ACCENT, width=2))
        self.c_emg = fast_curve(self.emg, pen=pg.mkPen(EMG_COLOR, width=1.5))
        dash = pg.QtCore.Qt.PenStyle.DashLine
        self.target_line = pg.InfiniteLine(45, angle=0, pen=pg.mkPen(T.qcolor(T.TARGET, 170), width=1, style=dash))
        self.angle.addItem(self.target_line)
        self.act_line = pg.InfiniteLine(cfg["faults"]["min_activation_pct"], angle=0,
                                        pen=pg.mkPen(T.qcolor(T.TEXT, 120), width=1, style=dash))
        self.emg.addItem(self.act_line)
        self.markers: list[tuple[float, list]] = []
        self.now = 0.0

    def set_target(self, target: float):
        self.target_line.setValue(target)

    def add_frames(self, frames):
        if not frames:
            return
        self.buf.extend([f.t for f, _ in frames], [(f.theta, f.env_pct) for f, _ in frames])
        self.now = frames[-1][0].t

    def add_rep(self, rep):
        """Rep boundaries (dashed start, solid end) and a fault marker; colour + text label."""
        color = T.GOOD if rep.valid else T.BAD
        items = []    # (plot, item, absolute time, y or None)
        for t, style in ((rep.t_start, pg.QtCore.Qt.PenStyle.DashLine), (rep.t_end, pg.QtCore.Qt.PenStyle.SolidLine)):
            for plot in (self.angle, self.emg):
                line = pg.InfiniteLine(0, angle=90, pen=pg.mkPen(T.qcolor(color, 200), width=1, style=style))
                plot.addItem(line)
                items.append((plot, line, t, None))
        label = "ok" if rep.valid else rep.primary_fault.replace("_", " ").lower()
        txt = text_item(label, color, anchor=(1, 0))
        self.angle.addItem(txt)
        items.append((self.angle, txt, rep.t_end, 88))
        if not rep.valid:
            x = pg.ScatterPlotItem([0], [rep.peak_theta], symbol="x", size=12, pen=pg.mkPen(color, width=2))
            self.angle.addItem(x)
            items.append((self.angle, x, rep.t_end, rep.peak_theta))
        self.markers.append((rep.t_end, items))
        self._place_markers()

    def _place_markers(self):
        for _, items in self.markers:
            for _, it, t, y in items:
                x = t - self.now
                if isinstance(it, pg.InfiniteLine):
                    it.setValue(x)
                elif isinstance(it, pg.TextItem):
                    it.setPos(x, y)
                else:
                    it.setData([x], [y])

    def redraw(self):
        t, d = self.buf.ordered()
        if len(t) == 0:
            return
        self.c_angle.setData(t - self.now, np.nan_to_num(d[:, 0]))
        self.c_emg.setData(t - self.now, np.nan_to_num(d[:, 1]))
        while self.markers and self.markers[0][0] < self.now - self.window - 15:
            _, items = self.markers.pop(0)
            for plot, it, _, _ in items:
                plot.removeItem(it)
        self._place_markers()

    def clear(self):
        self.buf = RollingBuffer(len(self.buf.t), 2)
        for _, items in self.markers:
            for plot, it, _, _ in items:
                plot.removeItem(it)
        self.markers = []


class FatigueChart(pg.PlotWidget):
    """Per-rep fatigue score with green / amber / red zone bands (labelled, not colour-only)."""

    def __init__(self, cfg: dict, title="Fatigue per rep", parent=None):
        super().__init__(parent)
        f = cfg["fatigue"]
        self.setYRange(0, 100)
        style_plot(self, title, "rep")
        for lo, hi, col, name in ((0, f["amber"], T.GOOD, "low"), (f["amber"], f["red"], T.WARN, "tired"),
                                  (f["red"], 100, T.BAD, "rest")):
            band = pg.LinearRegionItem((lo, hi), orientation="horizontal", movable=False,
                                       brush=T.qcolor(col, 30), pen=pg.mkPen(None))
            self.addItem(band)
            lab = text_item(name, col)
            lab.setPos(0.2, (lo + hi) / 2)
            self.addItem(lab)
        self.curve = self.plot(pen=pg.mkPen(T.TEXT, width=1.5), symbol="o", symbolSize=8,
                               symbolBrush=T.ACCENT, symbolPen=pg.mkPen(T.SURFACE, width=1))
        self.getPlotItem().vb.disableAutoRange()
        self.setXRange(0, 12, padding=0)
        self.setYRange(0, 100, padding=0)
        self.x: list[float] = []
        self.y: list[float] = []

    def add(self, x, y):
        if y == y:
            self.x.append(x)
            self.y.append(y)
            self.curve.setData(self.x, self.y)
            self.setXRange(0, max(12, x + 1), padding=0)

    def set_points(self, pts):
        self.x = [p[0] for p in pts if p[1] == p[1]]
        self.y = [p[1] for p in pts if p[1] == p[1]]
        self.curve.setData(self.x, self.y)
        if self.x:
            self.setXRange(0, max(12, max(self.x) + 1), padding=0)

    def clear_points(self):
        self.set_points([])


class TrendChart(pg.PlotWidget):
    """Simple per-rep trend (used for the optional MDF trend)."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        style_plot(self, label, "rep")
        self.curve = self.plot(pen=pg.mkPen(EMG_COLOR, width=1.5), symbol="o", symbolSize=8,
                               symbolBrush=EMG_COLOR, symbolPen=pg.mkPen(T.SURFACE, width=1))

    def set_points(self, pts):
        pts = [p for p in pts if p[1] == p[1]]
        self.curve.setData([p[0] for p in pts], [p[1] for p in pts])
