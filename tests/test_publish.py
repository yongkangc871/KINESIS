"""Hand-over of a finished session to the specialist dashboard (report/<patient>/se<log>/)."""
import json

from wristmon.publish import publish_root, publish_session


def _session(tmp_path):
    src = tmp_path / "sessions" / "yk" / "20261026_101500"
    src.mkdir(parents=True)
    for name in ("emg.csv", "imu.csv", "reps.csv", "events.csv", "calibration.json", "meta.json", "config.yaml"):
        (src / name).write_text("x", encoding="utf-8")
    (src / "report.html").write_text("<html>", encoding="utf-8")
    (src / "report.json").write_text("{}", encoding="utf-8")
    return src


def test_publish_copies_session_and_keeps_review_status(tmp_path):
    src = _session(tmp_path)
    root = tmp_path / "kinesis-public-demo" / "report"
    summary = {"sets": 1, "valid": 8, "invalid": 2, "attempts": 10}
    dest = publish_session(root, "y k", src / "report.html", src, "Bicep curls", "bicep_curl", summary)
    assert dest == root / "y_k" / "se20261026_101500"
    assert {p.name for p in dest.iterdir()} >= {"manifest.json", "report.html", "report.json", "emg.csv"}
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["review_status"] == "awaiting_review" and manifest["totals"]["valid"] == 8
    # The dashboard moved it on; publishing the same session again must not reset that.
    manifest["review_status"] = "reviewed"
    (dest / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    publish_session(root, "y k", src / "report.html", src, "Bicep curls", "bicep_curl", summary)
    again = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert again["review_status"] == "reviewed"
    assert again["first_published_at"] == manifest["first_published_at"]


def test_report_only_when_no_session_folder(tmp_path):
    report = tmp_path / "report_20261026_101500.html"
    report.write_text("<html>", encoding="utf-8")
    report.with_suffix(".json").write_text("{}", encoding="utf-8")
    dest = publish_session(tmp_path / "report", "yk", report, None, "Wrist curls", "wrist_weight", {})
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert dest.name.startswith("se") and manifest["raw_data"] is False
    assert sorted(manifest["files"]) == ["report.html", "report.json"]


def test_publish_root_is_relative_to_host_and_can_be_off(tmp_path):
    assert publish_root({"publish": {"enabled": False, "dir": "x"}}) is None
    assert publish_root({}) is None
    absolute = publish_root({"publish": {"enabled": True, "dir": str(tmp_path)}})
    assert absolute == tmp_path
    rel = publish_root({"publish": {"enabled": True, "dir": "../../kinesis-public-demo/report"}})
    assert rel.name == "report" and rel.parent.name == "kinesis-public-demo"


def _released(root, patient, session_id, released_at, title="Your Bicep curls session"):
    d = root / patient / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"exercise": "Bicep curls"}), encoding="utf-8")
    (d / "patient_log.json").write_text(json.dumps({
        "title": title, "summary": "You did well.", "tips": ["Lift slowly."], "specialist_note": "Keep going.",
        "status_label": "Good to continue", "level": "green", "released": True, "released_at": released_at,
    }), encoding="utf-8")
    return d


def test_patient_reads_released_reviews_and_a_read_receipt_is_left(tmp_path):
    from wristmon.publish import mark_review_read, released_reviews
    root = tmp_path / "report"
    _released(root, "yk", "se20261020_100000", "2026-10-21T08:00:00+00:00")
    _released(root, "yk", "se20261024_100000", "2026-10-25T08:00:00+00:00")
    draft = root / "yk" / "se20261025_100000"
    draft.mkdir()
    (draft / "patient_log.json").write_text(json.dumps({"title": "draft", "released": False}), encoding="utf-8")
    reviews = released_reviews(root, "yk")
    assert [r["session_id"] for r in reviews] == ["se20261024_100000", "se20261020_100000"]   # newest first
    assert all(r["unread"] for r in reviews) and reviews[0]["exercise"] == "Bicep curls"
    assert reviews[0]["when"].day == 24
    mark_review_read(root, "yk", reviews[0])
    receipt = json.loads((root / "yk" / "se20261024_100000" / "patient_read.json").read_text(encoding="utf-8"))
    assert receipt["released_at"] == "2026-10-25T08:00:00+00:00" and receipt["read_at"]
    assert [r["unread"] for r in released_reviews(root, "yk")] == [False, True]
    # The physiotherapist edits and releases it again: it is new for the patient again.
    _released(root, "yk", "se20261024_100000", "2026-10-26T09:00:00+00:00", title="Updated")
    assert released_reviews(root, "yk")[0]["unread"] is True
    assert released_reviews(root, "nobody") == []
