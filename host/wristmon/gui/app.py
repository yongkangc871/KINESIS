"""GUI entry point.

    python run_gui.py                     # live, over the link in config.yaml (Wi-Fi by default)
    python run_gui.py --wifi              # Wi-Fi, auto-discover the ESP32
    python run_gui.py --wifi 192.168.1.42 # Wi-Fi, fixed address
    python run_gui.py --port COM5         # USB serial
    python run_gui.py --sim               # virtual patient, no hardware
    python run_gui.py --sim --emg-mode envelope
    python run_gui.py --replay sessions/<user>/<stamp>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from ..calibration import Profile
from ..config import load_config
from ..sources import ReplaySource, SerialSource, SimSource, TcpSource
from . import theme as T


def make_live_source(cfg: dict, port: str | None = None, wifi: str | None = None):
    """--port forces USB serial, --wifi forces Wi-Fi; otherwise config link.transport decides."""
    link = cfg["link"]
    if port:
        return SerialSource(port, cfg["serial"]["baud"])
    if wifi or link["transport"] == "wifi":
        return TcpSource(wifi if wifi and wifi != "auto" else link["host"], link["port"],
                         link["discovery_port"], link["discovery_timeout_s"], link["connect_timeout_s"])
    return SerialSource(cfg["serial"]["port"], cfg["serial"]["baud"])


def build(argv=None):
    ap = argparse.ArgumentParser(description="Wrist Curl Coach")
    ap.add_argument("--config")
    ap.add_argument("--port", help="USB serial port (forces the serial link)")
    ap.add_argument("--wifi", nargs="?", const="auto", metavar="HOST",
                    help="Wi-Fi link; optional ESP32 address (default: auto-discover)")
    ap.add_argument("--sim", action="store_true", help="virtual patient instead of hardware")
    ap.add_argument("--emg-mode", choices=["raw", "envelope"], default="raw", help="simulated sEMG module type")
    ap.add_argument("--replay", help="session folder to replay")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--windowed", action="store_true")
    args = ap.parse_args(argv)

    config = args.config
    if args.replay and not config and (Path(args.replay) / "config.yaml").exists():
        config = Path(args.replay) / "config.yaml"     # the recorded exercise's thresholds
    cfg = load_config(config)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    # Shrink the whole layout on short screens (e.g. 1080p at 150 % Windows scaling) so nothing clips.
    avail = app.primaryScreen().availableGeometry().height()
    fit = max(0.7, min(1.0, avail / T.DESIGN_HEIGHT))
    T.set_scale(cfg["gui"]["font_scale"] * fit)
    app.setFont(T.app_font())          # Segoe UI everywhere, including pyqtgraph and QPainter text
    app.setStyleSheet(T.stylesheet())

    from .main_window import MainWindow
    profile = None
    if args.replay:
        mode = "replay"
        source = ReplaySource(args.replay, realtime=True, speed=args.speed)
        cal = Path(args.replay) / "calibration.json"
        profile = Profile.load(cal) if cal.exists() else Profile(user="replay")
    elif args.sim:
        mode = "sim"
        source = SimSource(emg_mode=args.emg_mode, realtime=True, forearm_imu=cfg["imu"]["forearm_imu"])
    else:
        mode = "live"
        source = make_live_source(cfg, args.port, args.wifi)
    win = MainWindow(cfg, source, mode, profile)
    if cfg["gui"]["fullscreen"] and not args.windowed:
        win.showFullScreen()
    else:
        win.resize(1600, 1000)
        win.show()
    return app, win


def main(argv=None) -> int:
    app, win = build(argv)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
