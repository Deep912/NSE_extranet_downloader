
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.database import init_db
from app.services.admin_auth import AdminAuth
from app.services.download_history import DownloadHistory
from app.services.downloader import DownloaderService
from app.services.folder_lists import FolderListStore, normalise_folder

from test_download_history import FakeNSE, ON_DATE


# ---------- folder lists ----------

@pytest.fixture
def lists(tmp_path):
    database_path = tmp_path / "lists.db"
    init_db(database_path)
    return FolderListStore(database_path)


def test_folder_list_add_remove_and_normalise(lists):
    assert lists.add("cm", "Reports/") == ["/Reports"]
    assert lists.add("CM", " /Accounts ") == ["/Accounts", "/Reports"]
    assert lists.add("CM", "/Reports") == ["/Accounts", "/Reports"]  # no duplicate

    assert lists.remove("CM", "/Reports") == ["/Accounts"]
    assert lists.all()["FO"] == []


@pytest.mark.parametrize("bad", ["", "/", "a/../b", "Reports\\Sub"])
def test_folder_list_rejects_bad_paths(bad):
    with pytest.raises(ValueError):
        normalise_folder(bad)


def test_folder_list_rejects_unknown_segment(lists):
    with pytest.raises(ValueError, match="Unsupported"):
        lists.add("XX", "/Reports")


def test_seed_runs_only_once(lists):
    assert lists.seed_once({"CM": ["Accounts", "Reports"], "FO": []}) is True
    assert lists.get("CM") == ["/Accounts", "/Reports"]

    lists.remove("CM", "/Accounts")

    # A restart must not bring back a folder the user removed.
    assert lists.seed_once({"CM": ["Accounts", "Reports"]}) is False
    assert lists.get("CM") == ["/Reports"]


# ---------- admin ----------

def test_admin_login_and_rights():
    clock = {"now": 1000.0}
    auth = AdminAuth("s3cret", clock=lambda: clock["now"])

    assert auth.configured
    assert auth.login("wrong") is None

    token = auth.login("s3cret")
    assert auth.is_admin(token)
    assert auth.allowed(token)
    assert not auth.allowed(None)

    clock["now"] += AdminAuth.SESSION_SECONDS + 1
    assert not auth.is_admin(token)  # expired


def test_no_admin_password_means_open():
    auth = AdminAuth("")

    assert not auth.configured
    assert auth.login("anything") is None
    assert auth.allowed(None)


def test_logout_ends_session():
    auth = AdminAuth("pw")
    token = auth.login("pw")
    auth.logout(token)
    assert not auth.is_admin(token)


# ---------- downloader: several folders, single file ----------

@pytest.fixture
def downloader(tmp_path):
    database_path = tmp_path / "dl.db"
    init_db(database_path)

    nse = FakeNSE()
    nse.tree["/Accounts"] = [{"name": "acc.csv", "type": "File", "lastUpdated": ON_DATE}]

    service = DownloaderService(
        download_root=tmp_path / "downloads",
        catalogue_service=object(),
        client_factory=nse,
        history=DownloadHistory(database_path),
    )
    return service, nse, database_path


def runs(database_path):
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM download_runs")]
    finally:
        conn.close()


def test_download_folders_is_one_run_and_skips_nested(downloader):
    service, nse, database_path = downloader

    result = service.download_folders(
        "CM", ["/Accounts", "/Reports", "/Reports/Sub"], "13-11-2026", mode="list"
    )

    # /Reports/Sub is inside /Reports, so it is walked only once.
    assert result["selected_roots"] == ["Accounts", "Reports"]
    assert result["downloaded"] == 3  # acc.csv, a.csv, Sub/b.csv
    assert sorted(nse.download_calls) == [
        "/Accounts/acc.csv", "/Reports/Sub/b.csv", "/Reports/a.csv",
    ]

    [run] = runs(database_path)
    assert run["mode"] == "list"


def test_download_single_file_dates_and_records_it(downloader):
    service, nse, database_path = downloader

    result = service.download_file("CM", "/Reports/Sub", "b.csv")

    assert result["status"] == "downloaded"
    assert result["exchange_date"] == "13-11-2026"
    assert result["file_path"].endswith("CM\\13112026\\Reports\\Sub\\b.csv") or \
        result["file_path"].endswith("CM/13112026/Reports/Sub/b.csv")
    assert nse.download_calls == ["/Reports/Sub/b.csv"]

    [run] = runs(database_path)
    assert run["mode"] == "file"
    assert run["status"] == "completed"
    assert run["files_downloaded"] == 1


def test_download_single_file_not_listed(downloader):
    service, nse, _ = downloader

    with pytest.raises(FileNotFoundError):
        service.download_file("CM", "/Reports", "missing.csv")

    assert nse.download_calls == []


def test_download_single_file_failure_is_recorded(downloader):
    service, nse, database_path = downloader
    nse.failing_files = {"a.csv"}

    with pytest.raises(Exception, match="timed out"):
        service.download_file("CM", "/Reports", "a.csv")

    [run] = runs(database_path)
    assert run["status"] == "failed"
    assert run["files_failed"] == 1


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main

    database_path = tmp_path / "api.db"
    init_db(database_path)

    nse = FakeNSE()
    service = DownloaderService(
        download_root=tmp_path / "downloads",
        catalogue_service=object(),
        client_factory=nse,
        history=DownloadHistory(database_path),
    )

    monkeypatch.setattr(main, "downloader", service)
    monkeypatch.setattr(main.schedule_store, "database_path", database_path)
    monkeypatch.setattr(main, "folder_lists", FolderListStore(database_path))
    monkeypatch.setattr(main, "admin", AdminAuth("pw"))

    return TestClient(main.app), main


SCHEDULE = {
    "name": "Nightly", "time": "23:00", "days": [0, 1, 2, 3, 4],
    "segments": ["CM"], "scope": "segment",
}


def test_schedule_changes_need_admin(client):
    http, _ = client

    assert http.get("/api/admin").json() == {"configured": True, "is_admin": False, "can_edit": False}
    assert http.post("/api/schedules", json=SCHEDULE).status_code == 401
    assert http.post("/api/folder-lists", json={"segment": "CM", "folder_path": "/Reports"}).status_code == 401

    assert http.post("/api/admin/login", json={"password": "nope"}).status_code == 401
    assert http.post("/api/admin/login", json={"password": "pw"}).status_code == 200
    assert http.get("/api/admin").json()["is_admin"] is True

    assert http.post("/api/schedules", json=SCHEDULE).status_code == 201
    assert http.post("/api/folder-lists", json={"segment": "CM", "folder_path": "/Reports"}).status_code == 200

    http.post("/api/admin/logout")
    assert http.post("/api/schedules", json=SCHEDULE).status_code == 401

    # Viewing stays open to everyone.
    assert http.get("/api/schedules").status_code == 200
    assert http.get("/api/folder-lists").json()["lists"]["CM"] == ["/Reports"]


def test_download_list_and_single_file_endpoints(client):
    http, main = client

    empty = http.post("/api/nse/download-list", json={"segment": "CM", "date": "13-11-2026"})
    assert empty.status_code == 400
    assert "empty" in empty.json()["detail"]

    main.folder_lists.add("CM", "/Reports")
    listed = http.post("/api/nse/download-list", json={"segment": "CM", "date": "13-11-2026"})
    assert listed.status_code == 200
    assert listed.json()["downloaded"] == 2

    single = http.post("/api/nse/download-file",
                       json={"segment": "CM", "folder_path": "/Reports", "file_name": "old.csv"})
    assert single.status_code == 200
    assert single.json()["exchange_date"] == "12-11-2026"  # its own upload date

    missing = http.post("/api/nse/download-file",
                        json={"segment": "CM", "folder_path": "/Reports", "file_name": "nope.csv"})
    assert missing.status_code == 404

    record = main.downloader.history.get_recorded_files("CM", "12-11-2026")[("Reports/", "old.csv")]
    assert record["status"] == "downloaded"


def test_save_copy_serves_only_saved_files(client):
    http, main = client

    main.downloader.download_file("CM", "/Reports", "a.csv")
    conn = sqlite3.connect(main.downloader.history.database_path)
    download_id = conn.execute("SELECT id FROM downloads WHERE file_name = 'a.csv'").fetchone()[0]
    conn.execute("INSERT INTO downloads (segment, exchange_date, file_name, folder_path, status, local_path) "
                 "VALUES ('CM', '01-01-2026', 'evil', 'x/', 'downloaded', 'C:/Windows/win.ini')")
    evil_id = conn.execute("SELECT id FROM downloads WHERE file_name = 'evil'").fetchone()[0]
    conn.commit()
    conn.close()

    response = http.get(f"/api/downloads/{download_id}/file")
    assert response.status_code == 200
    assert response.content == b"content of a.csv"

    assert http.get(f"/api/downloads/{evil_id}/file").status_code == 404
    assert http.get("/api/downloads/99999/file").status_code == 404
