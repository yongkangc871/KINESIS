"""Per-session logging. The files written here are exactly what replay mode reads back.

sessions/<user>/<YYYYmmdd_HHMMSS>/
    emg.csv          t, adc                     (raw synchronised stream, device clock)
    imu.csv          t, imu_id, roll, pitch, yaw, gx, gy, gz, ax, ay, az
    events.csv       t, event, arg              (mode changes / captures, for replay)
    reps.csv         one row per judged rep
    calibration.json profile used (rewritten whenever calibration changes)
    config.yaml      config snapshot
    meta.json        emg mode, rates, versions
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import yaml

from . import __version__
from .calibration import safe_name
from .datatypes import RepRecord

IMU_FIELDS = ["t", "imu_id", "roll", "pitch", "yaw", "gx", "gy", "gz", "ax", "ay", "az"]


class SessionLogger:
    def __init__(self, root: str | Path, user: str, cfg: dict, session_dir: str | Path | None = None):
        if session_dir is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_dir = Path(root) / safe_name(user) / stamp
        self.dir = Path(session_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._files = []
        self.emg = self._open("emg.csv", ["t", "adc"])
        self.imu = self._open("imu.csv", IMU_FIELDS)
        self.events = self._open("events.csv", ["t", "event", "arg"])
        self.reps_f = open(self.dir / "reps.csv", "w", newline="", encoding="utf-8")
        self._files.append(self.reps_f)
        self.reps = csv.DictWriter(self.reps_f, RepRecord.CSV_FIELDS + RepRecord.FEATURE_FIELDS)
        self.reps.writeheader()
        with open(self.dir / "config.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        self.meta = {"user": user, "created": datetime.now().isoformat(timespec="seconds"),
                     "host_version": __version__, "emg_fs_hz": cfg["emg"]["fs_hz"],
                     "imu_rate_hz": cfg["imu"]["rate_hz"]}
        self.write_meta()
        self.rows_since_flush = 0

    def _open(self, name, header):
        f = open(self.dir / name, "w", newline="", encoding="utf-8")
        self._files.append(f)
        w = csv.writer(f)
        w.writerow(header)
        return w

    def log_emg(self, t, adc):
        self.emg.writerows(zip((f"{x:.6f}" for x in t), (int(a) for a in adc)))
        self._maybe_flush(len(t))

    def log_imu(self, s):
        self.imu.writerow([f"{s.t:.6f}", s.imu_id, f"{s.roll:.3f}", f"{s.pitch:.3f}", f"{s.yaw:.3f}",
                           f"{s.gx:.3f}", f"{s.gy:.3f}", f"{s.gz:.3f}",
                           f"{s.ax:.5f}", f"{s.ay:.5f}", f"{s.az:.5f}"])
        self._maybe_flush(1)

    def log_event(self, t: float, event: str, arg=""):
        if isinstance(arg, (dict, list)):
            arg = json.dumps(arg)
        self.events.writerow([f"{t:.6f}", event, arg])

    def log_rep(self, rep: RepRecord):
        self.reps.writerow(rep.csv_row())
        self.reps_f.flush()

    def save_calibration(self, profile):
        (self.dir / "calibration.json").write_text(profile.to_json(), encoding="utf-8")

    def write_meta(self, **extra):
        self.meta.update(extra)
        (self.dir / "meta.json").write_text(json.dumps(self.meta, indent=2), encoding="utf-8")

    def _maybe_flush(self, n):
        self.rows_since_flush += n
        if self.rows_since_flush > 5000:
            self.flush()

    def flush(self):
        self.rows_since_flush = 0
        for f in self._files:
            f.flush()

    def close(self):
        for f in self._files:
            try:
                f.close()
            except ValueError:
                pass
        self._files = []
