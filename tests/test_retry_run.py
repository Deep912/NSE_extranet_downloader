import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.database import init_db
from app.services.download_history import DownloadHistory
from app.services.downloader import DownloaderService

from test_download_history import DATE, ON_DATE, FakeNSE


class ListingFakeNSE(FakeNSE):
    """FakeNSE that also remembers which folders were listed."""

    def __init__(self):
        super().__init__()
        self.tree["/"] = [
            {"name": "Reports", "type": "Folder", "lastUpdated": ON_DATE},
            {"name": "Accounts", "type": "Folder", "lastUpdated": ON_DATE},
        ]
        self.tree["/Accounts"] = [{"name": "c.csv", "type": "File", "lastUpdated": ON_DATE}]
        self.list_calls = []

    def list_content(self, segment, folder_path):
        self.list_calls.append(folder_path)
        return super().list_content(segment, folder_path)


@pytest.fixture
def setup(tmp_path):
    database_path = tmp_path / "t.db"
    init_db(database_path)
    nse = ListingFakeNSE()
    downloader = DownloaderService(
        download_root=tmp_path / "downloads",
        catalogue_service=object(),
        client_factory=nse,
        history=DownloadHistory(database_path),
    )
    return downloader, nse, database_path


def run_row(database_path, run_id):
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM download_runs WHERE id = ?", (run_id,)).fetchone())
    finally:
        conn.close()


def first_run_with_failures(downloader, nse):
    nse.failing_files = {"a.csv"}
    nse.failing_folders = {"/Reports/Sub"}

    result = downloader.download_folders("CM", ["/Reports", "/Accounts"], DATE)

    assert result["downloaded"] == 1  # c.csv
    assert result["failed_files"] == 1  # a.csv
    assert [f["folder_path"] for f in result["failed_folders"]] == ["Reports/Sub/"]
    return result


def test_failed_folders_are_saved_with_their_reason(setup):
    downloader, nse, database_path = setup
    result = first_run_with_failures(downloader, nse)

    saved = json.loads(run_row(database_path, result["run_id"])["failed_folders"])
    assert saved == [{
        "folder_path": "Reports/Sub/",
        "error": "NSE timed out for folder listing /Reports/Sub",
    }]


def test_retry_fetches_only_what_failed(setup):
    downloader, nse, database_path = setup
    first = first_run_with_failures(downloader, nse)

    nse.failing_files = set()
    nse.failing_folders = set()
    nse.list_calls.clear()
    nse.download_calls.clear()

    retry = downloader.retry_run(first["run_id"])

    # /Reports is listed only for a.csv (not walked again); /Accounts is untouched.
    assert sorted(nse.list_calls) == ["/Reports", "/Reports/Sub"]
    assert sorted(nse.download_calls) == ["/Reports/Sub/b.csv", "/Reports/a.csv"]
    assert retry["status"] == "completed"
    assert retry["downloaded"] == 2
    assert retry["retry_of"] == first["run_id"]

    row = run_row(database_path, retry["run_id"])
    assert row["mode"] == "retry"
    assert json.loads(row["roots"]) == ["Reports/Sub", "Reports: 1 file(s)"]

    # Nothing failed in the retry, so there is nothing more to retry.
    with pytest.raises(ValueError, match="nothing left to retry"):
        downloader.retry_run(retry["run_id"])


def test_failed_folder_covers_failed_files_inside_it(setup):
    downloader, nse, _ = setup
    nse.failing_files = {"b.csv"}
    nse.failing_folders = {"/Accounts"}
    first = downloader.download_folders("CM", ["/"], DATE)

    nse.failing_files = set()
    nse.failing_folders = set()
    nse.list_calls.clear()

    downloader.retry_run(first["run_id"])

    # b.csv's folder is listed for the file, /Accounts is walked again.
    assert sorted(nse.list_calls) == ["/Accounts", "/Reports/Sub"]


def test_older_runs_are_read_from_the_summary_message():
    run = {"failed_folders": None,
           "error_message": "3 folder(s) failed: Accounts/; Onlinebackup/; /"}

    assert DownloadHistory.failed_folder_paths(run) == ["Accounts/", "Onlinebackup/", ""]
    assert DownloadHistory.failed_folder_paths({"error_message": "Stopped by user."}) == []


def test_running_or_missing_runs_cannot_be_retried(setup):
    downloader, _, _ = setup
    run_id = downloader.history.start_run("CM", DATE, "list", ["Reports"])

    with pytest.raises(ValueError, match="still running"):
        downloader.retry_run(run_id)

    with pytest.raises(LookupError):
        downloader.retry_run(9999)


def test_retry_endpoint(setup, monkeypatch):
    import app.main as main

    downloader, nse, _ = setup
    monkeypatch.setattr(main, "downloader", downloader)
    http = TestClient(main.app)

    first = first_run_with_failures(downloader, nse)
    nse.failing_files = set()
    nse.failing_folders = set()

    response = http.post(f"/api/runs/{first['run_id']}/retry")
    assert response.status_code == 200
    assert response.json()["downloaded"] == 2

    assert http.post(f"/api/runs/{response.json()['run_id']}/retry").status_code == 409
    assert http.post("/api/runs/9999/retry").status_code == 404

    with main.activity.try_hold("Manual download FO"):
        assert http.post(f"/api/runs/{first['run_id']}/retry").status_code == 409
