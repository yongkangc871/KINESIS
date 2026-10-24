"""Clinical theme: palette, Segoe UI type scale, stylesheet and the activation colour map."""
from __future__ import annotations

from PyQt6.QtGui import QColor, QFont

# ------------------------------------------------------------------ palette (light clinical)
BG = "#f4f6f9"
SURFACE = "#ffffff"        # cards
SURFACE_2 = "#eef1f5"      # inputs, tracks, secondary buttons
BORDER = "#dde3ea"
BORDER_STRONG = "#c3ccd6"
TEXT = "#26354a"           # dark slate navy
MUTED = "#5d6b7a"
FAINT = "#8e9aa7"
ACCENT = "#0796d3"         # medical blue
ACCENT_HOVER = "#1fa6e0"
ACCENT_DARK = "#0679ab"    # pressed state and the button's bottom edge
INFO_BG = "#eaf4fc"        # tinted information box
INFO_BORDER = "#b9dcf2"
GOOD = "#2e9d5b"           # green
WARN = "#d9822b"           # amber / orange
BAD = "#d23f3a"            # red
STOP = "#c9302c"
TARGET = "#2e9d5b"
CHOICE_BG = "#f5f0e6"      # warm cream choice cards
CHOICE_HOVER = "#efe7d8"
HEADING = "#505050"        # display-font page questions
INK = "#111111"            # display-font card titles and the outlined button border
TABLE_TOP =("#e6eaef", "#d3d9e0")    # arm-view table: lit top edge, front face (gradients)
TABLE_FACE = ("#d9dfe6", "#c6ced8")
SHADOW_ALPHA = 55

# Kept for code that still refers to the old names.
PANEL = SURFACE
PANEL_2 = SURFACE_2

# ------------------------------------------------------------------ typography
FONT_FAMILY = "Segoe UI"
FALLBACKS = ["Segoe UI", "Segoe UI Variable Text", "Arial", "Helvetica", "sans-serif"]

# Type scale (pt). Key information is never below M.
XS, S, M, L, XL, XXL = 11, 13, 16, 20, 30, 56

# Logical screen height the layout is designed for; smaller screens scale everything down.
DESIGN_HEIGHT = 900

_scale = 1.0


def set_scale(s: float):
    global _scale
    _scale = s


def pt(size: float) -> int:
    return int(round(size * _scale))


def font(size: float, bold: bool = False, weight: QFont.Weight | None = None, light: bool = False,
         caps: bool = False, tabular: bool = False) -> QFont:
    """Segoe UI at a type-scale size. bold -> Semibold; light -> Light (very large numerals)."""
    f = QFont()
    f.setFamilies(FALLBACKS)
    f.setPointSizeF(size * _scale)
    if weight is not None:
        f.setWeight(weight)
    elif bold:
        f.setWeight(QFont.Weight.DemiBold)
    elif light:
        f.setWeight(QFont.Weight.Light)
    if caps:
        f.setCapitalization(QFont.Capitalization.AllUppercase)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
    if tabular:
        # Fixed-width digits so counters and angles do not jitter (Qt >= 6.7).
        try:
            f.setFeature(QFont.Tag("tnum"), 1)
        except (AttributeError, TypeError):
            pass
    return f


def app_font() -> QFont:
    return font(S)


def stylesheet() -> str:
    s = _scale
    px = lambda v: int(round(v * s))
    fam = ", ".join(f"'{f}'" for f in FALLBACKS[:-1]) + ", sans-serif"
    # No font-size on the generic QWidget/QLabel rules: a style-sheet font would override the
    # per-label type scale set with setFont(). The base font comes from QApplication.setFont().
    return f"""
    QMainWindow {{ background: {BG}; }}
    QWidget {{ background: transparent; color: {TEXT}; }}
    QLabel {{ background: transparent; border: none; }}
    QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
    QFrame#choice {{ background: {CHOICE_BG}; border: 2px solid {CHOICE_BG}; border-radius: 14px; }}
    QFrame#choice:hover {{ border-color: {ACCENT}; background: {CHOICE_HOVER}; }}
    QFrame#choice_static {{ background: {CHOICE_BG}; border: 2px solid {CHOICE_BG}; border-radius: 14px; }}
    QFrame#panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
    QFrame#info {{ background: {INFO_BG}; border: 1px solid {INFO_BORDER}; border-radius: 10px; }}

    QPushButton {{ background: {ACCENT}; color: white; border: none; border-bottom: 4px solid {ACCENT_DARK};
                   border-radius: 8px; font-family: {fam};
                   padding: {px(8)}px {px(24)}px; font-size: {px(M)}pt; font-weight: 600; min-height: {px(36)}px; }}
    QPushButton:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton:pressed {{ background: {ACCENT_DARK}; }}
    QPushButton:disabled {{ background: {SURFACE_2}; color: {FAINT}; border-bottom-color: {BORDER}; }}
    QPushButton#good {{ background: {ACCENT}; color: white; }}
    QPushButton#good:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton#secondary {{ background: {SURFACE}; color: {TEXT}; border: 2px solid {BORDER_STRONG}; }}
    QPushButton#secondary:hover {{ border-color: {MUTED}; background: {SURFACE_2}; }}
    QPushButton#secondary:disabled {{ color: {FAINT}; border-color: {BORDER}; }}
    QPushButton#outline {{ background: {SURFACE}; color: {HEADING}; border: 2px solid {INK}; border-radius: 12px;
                           font-size: {px(XL)}pt; font-weight: 400; }}
    QPushButton#outline:hover {{ background: {SURFACE_2}; }}
    QPushButton#outline:pressed {{ background: {BORDER}; }}
    QPushButton#stepper {{ background: {SURFACE}; color: {TEXT}; border: 2px solid {BORDER_STRONG};
                           font-size: {px(XL)}pt; font-weight: 300; padding: 0px; }}
    QPushButton#stepper:hover {{ border-color: {MUTED}; background: {SURFACE_2}; }}
    QPushButton#stop {{ background: {STOP}; color: white; font-size: {px(L)}pt; letter-spacing: 2px;
                        border-bottom-color: #9e2420; }}
    QPushButton#stop:hover {{ background: #d9443f; }}

    QComboBox, QLineEdit {{ background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: 8px;
                            padding: {px(6)}px {px(12)}px; font-family: {fam}; font-size: {px(M)}pt; }}
    QComboBox:focus, QLineEdit:focus {{ border: 2px solid {ACCENT}; }}
    QComboBox QAbstractItemView {{ background: {SURFACE}; selection-background-color: {ACCENT};
                                   selection-color: white; }}

    QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
    QStatusBar QLabel {{ font-size: {px(10)}pt; padding: 2px 10px; color: {MUTED}; }}
    QStatusBar::item {{ border: none; }}


    QSlider::groove:horizontal {{ height: 4px; background: {BORDER_STRONG}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {SURFACE}; border: 1px solid {BORDER_STRONG}; width: 16px;
                                  margin: -7px 0; border-radius: 9px; }}
    QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; }}
    """


def activation_color(pct: float) -> QColor:
    """Cool -> warm for 0 -> 100+ %RVC (muted blue -> amber -> red). One colour per value."""
    if pct != pct:   # nan
        return QColor(FAINT)
    x = max(0.0, min(1.0, pct / 100.0))
    if x < 0.5:
        k = x / 0.5
        a, b = (58, 120, 200), (226, 178, 64)
    else:
        k = (x - 0.5) / 0.5
        a, b = (226, 178, 64), (214, 76, 70)
    return QColor(*(int(a[i] + (b[i] - a[i]) * k) for i in range(3)))


def qcolor(hex_color: str, alpha: int = 255) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c
