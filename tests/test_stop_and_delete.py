
import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.database import init_db
from app.services.admin_auth import AdminAuth
from app.services.download_history import DownloadHistory
from app.services.downloader import DownloaderService
from app.services.folder_lists import FolderListStore
from app.services.nse_activity import NSEActivity
from app.services.scheduler import ScheduleStore, Scheduler

from test_download_history import FakeNSE


def rows(database_path, sql, params=()):
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


@pytest.fixture
def setup(tmp_path):
    database_path = tmp_path / "t.db"
    init_db(database_path)
    nse = FakeNSE()
    downloader = DownloaderService(
        download_root=tmp_path / "downloads",
        catalogue_service=object(),
        client_factory=nse,
        history=DownloadHistory(database_path),
    )
    return downloader, nse, database_path, tmp_path


# ---------- stopping ----------

def test_activity_stop_flag_is_per_session():
    activity = NSEActivity()

    assert activity.request_stop() is None  # nothing running

    with activity.try_hold("Manual download CM"):
        assert activity.request_stop()["stopping"] is True
        assert activity.stop_requested()

    # The next session starts clean.
    with activity.try_hold("Manual download FO"):
        assert not activity.stop_requested()


def test_stop_keeps_files_so_far_and_marks_run_cancelled(setup):
    downloader, nse, database_path, _ = setup

    # Ask to stop as soon as the first file has been downloaded.
    downloader.should_stop = lambda: len(nse.download_calls) >= 1

    result = downloader.download_root_folders("CM", ["Reports"], "13-11-2026")

    assert result["status"] == "cancelled"
    assert result["downloaded"] == 1
    assert len(nse.download_calls) == 1  # the second file was never started

    [run] = rows(database_path, "SELECT * FROM download_runs")
    assert run["status"] == "cancelled"
    assert run["files_downloaded"] == 1
    assert run["error_message"] == "Stopped by user."

    # Running again continues where it left off.
    downloader.should_stop = lambda: False
    nse.download_calls.clear()
    again = downloader.download_root_folders("CM", ["Reports"], "13-11-2026")
    assert again["downloaded"] == 1
    assert again["already_downloaded"] == 1


def test_stopped_scheduled_run_is_not_retried(setup):
    downloader, nse, database_path, _ = setup
    activity = NSEActivity()
    downloader.should_stop = activity.stop_requested

    store = ScheduleStore(database_path)
    clock = {"now": datetime(2026, 11, 13, 22, 0)}  # Friday; fake files are dated 13-11
    scheduler = Scheduler(store, downloader, activity, now=lambda: clock["now"],
                          history=DownloadHistory(database_path),
                          folder_lists=FolderListStore(database_path))

    schedule = store.create({
        "name": "Nightly", "time": "23:00", "days": [0, 1, 2, 3, 4],
        "segments": ["CM", "FO"], "scope": "segment", "days_back": 0,
        "retry_count": 2,
    }, clock["now"])

    # Press Stop as soon as the first file starts downloading.
    original = nse.download_single_file

    def download_then_stop(*args, **kwargs):
        activity.request_stop()
        return original(*args, **kwargs)

    nse.download_single_file = download_then_stop
    nse.tree["/"] = [{"name": "Reports", "type": "Folder", "lastUpdated": "2024-01-01T00:00:00+00:00"}]

    result = scheduler.run_job({"schedule_id": schedule["id"], "kind": "manual",
                                "base_date": clock["now"].date()})

    assert result["status"] == "cancelled"
    saved = store.get(schedule["id"])
    assert saved["last_status"] == "cancelled"
    assert saved["retry_at"] is None
    assert saved["last_summary"].startswith("Stopped by user")

    # FO was never started.
    segments = [r["segment"] for r in rows(database_path, "SELECT segment FROM download_runs")]
    assert segments == ["CM"]


# ---------- deleting history ----------

def test_delete_run_and_files(setup):
    downloader, nse, database_path, tmp_path = setup
    result = downloader.download_root_folders("CM", ["Reports"], "13-11-2026")
    paths = [d["file_path"] for d in result["downloads"] if "file_path" in d]
    assert len(paths) == 2

    removed = downloader.history.delete_run(result["run_id"])
    assert removed["runs"] == 1
    assert removed["file_records"] == 2

    files = downloader.remove_local_files(removed["local_paths"])
    assert files["deleted"] == 2
    assert not (tmp_path / "downloads" / "CM").exists()  # empty folders removed

    assert rows(database_path, "SELECT * FROM downloads") == []
    assert rows(database_path, "SELECT * FROM download_runs") == []


def test_running_run_cannot_be_deleted(setup):
    downloader, _, _, _ = setup
    run_id = downloader.history.start_run("CM", "13-11-2026", "folder", ["/"])

    with pytest.raises(ValueError, match="still running"):
        downloader.history.delete_run(run_id)


def test_remove_local_files_never_leaves_download_root(setup, tmp_path):
    downloader, _, _, _ = setup
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")

    counts = downloader.remove_local_files([str(outside), str(tmp_path / "downloads" / "x.csv")])

    assert counts == {"deleted": 0, "missing": 1, "skipped": 1, "bytes": 0}
    assert outside.exists()


def test_cleanup_preview_then_delete_by_date_range(setup):
    downloader, nse, database_path, _ = setup
    downloader.download_root_folders("CM", ["Reports"], "13-11-2026")
    downloader.download_root_folders("CM", ["Reports"], "12-11-2026")

    preview = downloader.history.cleanup("CM", "2026-11-13", "2026-11-13", dry_run=True)
    assert preview["runs"] == 1
    assert preview["file_records"] == 2
    assert len(rows(database_path, "SELECT * FROM download_runs")) == 2  # nothing deleted

    done = downloader.history.cleanup("CM", "2026-11-13", "2026-11-13", dry_run=False)
    assert done["runs"] == 1

    remaining = rows(database_path, "SELECT exchange_date FROM download_runs")
    assert remaining == [{"exchange_date": "12-11-2026"}]

    # Other segments and dates are untouched.
    assert downloader.history.cleanup("FO", "2026-01-01", "2026-12-31")["runs"] == 0


# ---------- API ----------

@pytest.fixture
def client(setup, monkeypatch):
    import app.main as main

    downloader, nse, database_path, _ = setup
    monkeypatch.setattr(main, "downloader", downloader)
    monkeypatch.setattr(main, "admin", AdminAuth("pw"))
    return TestClient(main.app), main, downloader


def login(http):
    assert http.post("/api/admin/login", json={"password": "pw"}).status_code == 200


def test_stop_endpoint(client):
    http, main, _ = client

    assert http.post("/api/stop").status_code == 409  # nothing running

    with main.activity.try_hold("Manual download CM 13-11-2026"):
        response = http.post("/api/stop")
        assert response.status_code == 200
        assert main.activity.stop_requested()
        assert http.get("/api/status").json()["current"]["stopping"] is True

    with main.activity.hold("Nightly: CM 13-11-2026 (1/1)", kind="scheduled"):
        assert http.post("/api/stop").status_code == 401  # scheduled run needs admin
        login(http)
        assert http.post("/api/stop").status_code == 200


def test_delete_endpoints_need_admin_and_idle(client):
    http, main, downloader = client
    result = downloader.download_root_folders("CM", ["Reports"], "13-11-2026")
    run_id = result["run_id"]

    assert http.delete(f"/api/runs/{run_id}").status_code == 401

    login(http)

    with main.activity.try_hold("Manual download FO"):
        assert http.delete(f"/api/runs/{run_id}").status_code == 409  # busy

    preview = http.post("/api/history/cleanup", json={
        "segment": "CM", "date_from": "2026-11-13", "date_to": "2026-11-13", "dry_run": True,
    }).json()
    assert preview == {"dry_run": True, "runs": 1, "file_records": 2, "files_on_record": 2,
                       "bytes": preview["bytes"]}

    deleted = http.delete(f"/api/runs/{run_id}?delete_files=true").json()
    assert deleted["runs"] == 1
    assert deleted["files"]["deleted"] == 2

    assert http.delete(f"/api/runs/{run_id}").status_code == 404
    assert http.post("/api/history/cleanup", json={
        "date_from": "2026-11-14", "date_to": "2026-11-13",
    }).status_code == 400
