
import sqlite3

import pytest

from app.database import init_db
from app.services.download_history import DownloadHistory
from app.services.downloader import DownloaderService
from app.services.nse_client import NSEAPIError


DATE = "13-11-2026"
ON_DATE = "2026-11-13T12:00:00.000+00:00"
OTHER_DATE = "2026-11-12T12:00:00.000+00:00"


class FakeNSE:
    """Stands in for NSEExtranetClient. Keys are NSE-style paths."""

    def __init__(self):
        self.tree = {
            "/Reports": [
                {"name": "a.csv", "type": "File", "lastUpdated": ON_DATE},
                {"name": "old.csv", "type": "File", "lastUpdated": OTHER_DATE},
                {"name": "Sub", "type": "Folder", "lastUpdated": OTHER_DATE},
            ],
            "/Reports/Sub": [
                {"name": "b.csv", "type": "File", "lastUpdated": ON_DATE},
            ],
        }
        self.contents = {}
        self.failing_files = set()
        self.failing_folders = set()
        self.download_calls = []

    def __call__(self):  # used as client_factory
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def list_content(self, segment, folder_path):
        if folder_path in self.failing_folders:
            raise NSEAPIError(f"NSE timed out for folder listing {folder_path}")
        return {"data": self.tree[folder_path]}

    def download_single_file(self, segment, folder_path, filename):
        self.download_calls.append(f"{folder_path}/{filename}")
        if filename in self.failing_files:
            raise NSEAPIError(f"NSE timed out for file download {filename}")
        return self.contents.get(filename, f"content of {filename}".encode())

    def set_last_updated(self, filename, value):
        for items in self.tree.values():
            for item in items:
                if item["name"] == filename:
                    item["lastUpdated"] = value


@pytest.fixture
def setup(tmp_path):
    database_path = tmp_path / "test.db"
    init_db(database_path)

    nse = FakeNSE()
    downloader = DownloaderService(
        download_root=tmp_path / "downloads",
        catalogue_service=object(),
        client_factory=nse,
        history=DownloadHistory(database_path),
    )
    return downloader, nse, database_path


def rows(database_path, sql):
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql)]
    finally:
        conn.close()


def run(downloader):
    return downloader.download_root_folders("CM", ["Reports"], DATE)


def test_first_run_downloads_and_records(setup):
    downloader, nse, database_path = setup

    result = run(downloader)

    assert result["downloaded"] == 2
    assert result["other_date"] == 1
    assert result["already_downloaded"] == 0
    assert result["skipped"] == 1
    assert len(nse.download_calls) == 2

    files = rows(database_path, "SELECT * FROM downloads ORDER BY file_name")
    assert [f["file_name"] for f in files] == ["a.csv", "b.csv"]
    assert [f["folder_path"] for f in files] == ["Reports/", "Reports/Sub/"]
    assert all(f["status"] == "downloaded" for f in files)
    assert all(f["run_id"] == result["run_id"] for f in files)
    assert all(f["nse_last_updated"] == ON_DATE for f in files)

    [run_row] = rows(database_path, "SELECT * FROM download_runs")
    assert run_row["status"] == "completed"
    assert run_row["mode"] == "root"
    assert run_row["exchange_date"] == DATE
    assert run_row["files_found"] == 2
    assert run_row["files_downloaded"] == 2
    assert run_row["finished_at"] is not None


def test_repeat_run_skips_without_contacting_nse_for_files(setup):
    downloader, nse, database_path = setup
    run(downloader)
    nse.download_calls.clear()

    result = run(downloader)

    assert nse.download_calls == []
    assert result["downloaded"] == 0
    assert result["already_downloaded"] == 2
    assert result["other_date"] == 1

    files = rows(database_path, "SELECT * FROM downloads")
    assert all(f["status"] == "downloaded" for f in files)
    assert all(f["run_id"] == result["run_id"] for f in files)


def test_file_changed_on_nse_is_downloaded_again(setup):
    downloader, nse, _ = setup
    run(downloader)
    nse.download_calls.clear()

    nse.set_last_updated("a.csv", "2026-11-13T20:00:00.000+00:00")
    nse.contents["a.csv"] = b"new version"

    result = run(downloader)

    assert nse.download_calls == ["/Reports/a.csv"]
    assert result["downloaded"] == 1
    assert result["already_downloaded"] == 1

    # The earlier copy is kept; the new one gets a hash suffix.
    saved = [d for d in result["downloads"] if d.get("file_name", "").startswith("a_")]
    assert len(saved) == 1


def test_deleted_local_file_is_downloaded_again(setup):
    downloader, nse, _ = setup
    first = run(downloader)
    nse.download_calls.clear()

    a_path = next(
        d["file_path"] for d in first["downloads"] if d["file_name"] == "a.csv"
    )
    import os
    os.remove(a_path)

    result = run(downloader)

    assert nse.download_calls == ["/Reports/a.csv"]
    assert result["downloaded"] == 1


def test_failed_file_is_recorded_and_retried_next_run(setup):
    downloader, nse, database_path = setup
    nse.failing_files = {"b.csv"}

    result = run(downloader)

    assert result["failed"] == 1
    assert result["failed_files"] == 1
    assert result["status"] == "partial"

    [b] = rows(database_path, "SELECT * FROM downloads WHERE file_name = 'b.csv'")
    assert b["status"] == "failed"
    assert "timed out" in b["error_message"]

    nse.failing_files = set()
    nse.download_calls.clear()

    result = run(downloader)

    assert nse.download_calls == ["/Reports/Sub/b.csv"]
    [b] = rows(database_path, "SELECT * FROM downloads WHERE file_name = 'b.csv'")
    assert b["status"] == "downloaded"
    assert b["error_message"] is None


def test_failure_after_success_keeps_downloaded_status(setup):
    downloader, nse, database_path = setup
    run(downloader)

    # NSE republishes a.csv, and the new download fails.
    nse.set_last_updated("a.csv", "2026-11-13T20:00:00.000+00:00")
    nse.failing_files = {"a.csv"}
    run(downloader)

    [a] = rows(database_path, "SELECT * FROM downloads WHERE file_name = 'a.csv'")
    assert a["status"] == "downloaded"
    assert a["local_path"] is not None
    # Old lastUpdated kept, so the next run tries the new version again.
    assert a["nse_last_updated"] == ON_DATE


def test_failed_folder_is_counted_in_run(setup):
    downloader, nse, database_path = setup
    nse.failing_folders = {"/Reports/Sub"}

    result = run(downloader)

    assert result["downloaded"] == 1
    assert result["failed_files"] == 0
    assert len(result["failed_folders"]) == 1

    [run_row] = rows(database_path, "SELECT * FROM download_runs")
    assert run_row["status"] == "partial"
    assert run_row["folders_failed"] == 1
    assert "Reports/Sub/" in run_row["error_message"]


def test_crashed_run_is_not_left_running(setup):
    downloader, _, database_path = setup

    def no_credentials():
        raise NSEAPIError("NSE credentials are incomplete.")

    downloader.client_factory = no_credentials

    with pytest.raises(NSEAPIError):
        run(downloader)

    [run_row] = rows(database_path, "SELECT * FROM download_runs")
    assert run_row["status"] == "failed"
    assert run_row["finished_at"] is not None
    assert "credentials" in run_row["error_message"]


def test_init_db_adds_new_columns_to_existing_database(tmp_path):
    database_path = tmp_path / "old.db"

    conn = sqlite3.connect(database_path)
    conn.execute("""
        CREATE TABLE downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            segment TEXT NOT NULL,
            exchange_date TEXT,
            file_name TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            status TEXT NOT NULL,
            downloaded_at TEXT,
            file_size INTEGER,
            checksum_sha256 TEXT,
            error_message TEXT,
            UNIQUE(segment, exchange_date, folder_path, file_name)
        )
    """)
    conn.execute(
        "INSERT INTO downloads (segment, exchange_date, file_name, folder_path, status) "
        "VALUES ('CM', '01-11-2026', 'x.csv', 'Reports/', 'downloaded')"
    )
    conn.commit()
    conn.close()

    init_db(database_path)
    init_db(database_path)  # running twice must be safe

    columns = {
        row["name"] for row in rows(database_path, "PRAGMA table_info(downloads)")
    }
    assert {"run_id", "nse_last_updated", "local_path"} <= columns

    run_columns = {
        row["name"] for row in rows(database_path, "PRAGMA table_info(download_runs)")
    }
    assert {"exchange_date", "roots", "files_failed", "folders_failed"} <= run_columns

    [old] = rows(database_path, "SELECT * FROM downloads")
    assert old["file_name"] == "x.csv"
