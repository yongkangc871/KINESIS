"""Hands a finished session to the specialist dashboard (kinesis-public-demo).

Every summary (also "Skip to results") copies the session into

    <publish.dir>/<patient>/se<session log>/
        manifest.json    who, what, when, which files, review status
        report.json      the machine-readable summary (also what an AI model reads)
        report.html      the same report for people
        emg.csv imu.csv reps.csv events.csv calibration.json meta.json config.yaml

<session log> is the logger's folder name (YYYYmmdd_HHMMSS), so a session keeps one id in
both places. The dashboard reads this folder and writes the specialist's review and the
patient-facing log next to these files; nothing here is ever overwritten by it.

The way back: when the specialist releases a review, the dashboard writes patient_log.json
(released: true). The app shows those to the patient (released_reviews) and records when one
was opened in patient_read.json, which the dashboard shows as "read by the patient".
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .calibration import safe_name
from .config import HOST_DIR

SCHEMA = "kinesis.published_session/1"
SESSION_FILES = ("emg.csv", "imu.csv", "reps.csv", "events.csv", "calibration.json", "meta.json",
                 "config.yaml")


def publish_root(cfg: dict) -> Path | None:
    """The dashboard's report folder, or None when publishing is off."""
    pub = cfg.get("publish") or {}
    if not pub.get("enabled", False) or not pub.get("dir"):
        return None
    p = Path(pub["dir"])
    return p if p.is_absolute() else (HOST_DIR / p).resolve()


def publish_session(root: str | Path, patient: str, report_path: str | Path, session_dir: str | Path | None,
                    exercise: str, exercise_key: str, summary: dict) -> Path:
    """Copies the report (and the raw session files when there are any) and returns the folder."""
    report_path = Path(report_path)
    src = Path(session_dir) if session_dir else None
    log_id = src.name if src is not None else datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(root) / safe_name(patient) / f"se{log_id}"
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for p in (report_path, report_path.with_suffix(".json")):
        if p.exists():
            shutil.copyfile(p, dest / f"report{p.suffix}")
            copied.append(f"report{p.suffix}")
    if src is not None:
        for name in SESSION_FILES:
            if (src / name).exists():
                shutil.copyfile(src / name, dest / name)
                copied.append(name)
    manifest_path = dest / "manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest = {
        "schema": SCHEMA,
        "patient": patient,
        "session_id": f"se{log_id}",
        "session_log": log_id,
        "exercise": exercise,
        "exercise_key": exercise_key,
        "published_at": datetime.now().isoformat(timespec="seconds"),
        "first_published_at": old.get("first_published_at") or datetime.now().isoformat(timespec="seconds"),
        "totals": {k: summary.get(k) for k in ("sets", "valid", "invalid", "attempts")},
        "raw_data": src is not None,
        "files": copied,
        # The dashboard moves this on: awaiting_review -> reviewed -> released (to the patient).
        "review_status": old.get("review_status", "awaiting_review"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


def released_reviews(root: str | Path, patient: str) -> list[dict]:
    """The specialist's released reviews for this patient, newest session first.

    Each: session_id, exercise, when (datetime or None), log (patient_log.json), read_at, and
    unread (never opened, or released again since it was opened)."""
    folder = Path(root) / safe_name(patient)
    out = []
    for d in sorted(folder.glob("se*"), reverse=True) if folder.is_dir() else []:
        log = _json(d / "patient_log.json")
        if not log or not log.get("released"):
            continue
        manifest = _json(d / "manifest.json") or {}
        try:
            when = datetime.strptime(d.name[2:], "%Y%m%d_%H%M%S")
        except ValueError:
            when = None
        read = _json(d / "patient_read.json") or {}
        out.append({"session_id": d.name, "exercise": manifest.get("exercise", ""), "when": when, "log": log,
                    "read_at": read.get("read_at"),
                    "unread": read.get("released_at") != log.get("released_at") or not read.get("read_at")})
    return out


def mark_review_read(root: str | Path, patient: str, review: dict) -> None:
    """Records that the patient opened this release of a review (a later release is unread again)."""
    path = Path(root) / safe_name(patient) / review["session_id"] / "patient_read.json"
    if path.parent.is_dir() and review.get("unread", True):
        path.write_text(json.dumps({"read_at": datetime.now().isoformat(timespec="seconds"),
                                    "released_at": review["log"].get("released_at")}), encoding="utf-8")
        review.update(unread=False)


def _json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
