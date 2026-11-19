import logging

import pytest
from fastapi.testclient import TestClient

from app.database import init_db
from app.services.app_log import (
    QuietAccessLog,
    RecentLines,
    format_seconds,
    format_size,
    log,
    recent,
)
from app.services.download_history import DownloadHistory
from app.services.downloader import DownloaderService

from test_download_history import FakeNSE


def access_record(method, status):
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", method, "/api/status", "1.1", status), None,
    )


def test_quiet_access_log_hides_routine_gets_only():
    quiet = QuietAccessLog()

    assert quiet.filter(access_record("GET", 200)) is False
    assert quiet.filter(access_record("GET", 304)) is False
    assert quiet.filter(access_record("GET", 404)) is True
    assert quiet.filter(access_record("POST", 200)) is True
    assert quiet.filter(access_record("DELETE", 200)) is True


def test_recent_lines_numbers_and_filters():
    lines = RecentLines(capacity=3)
    logger = logging.getLogger("test.recent")
    logger.propagate = False
    logger.addHandler(lines)
    logger.setLevel(logging.INFO)

    for n in range(4):
        logger.info("line %s", n)
    logger.warning("careful")

    everything = lines.since()
    assert everything["last"] == 5
    assert [l["message"] for l in everything["lines"]] == ["line 2", "line 3", "careful"]

    assert [l["message"] for l in lines.since(after=4)["lines"]] == ["careful"]
    assert [l["level"] for l in lines.since(problems_only=True)["lines"]] == ["WARNING"]


def test_formatting():
    assert format_size(512) == "512 B"
    assert format_size(2048) == "2.0 KB"
    assert format_size(5 * 1024 * 1024) == "5.0 MB"
    assert format_seconds(3.25) == "3.2s"
    assert format_seconds(125) == "2m 05s"
    assert format_seconds(3725) == "1h 02m"


@pytest.fixture
def captured():
    """Collect lines written to the 'nse' logger during a test."""

    lines = RecentLines()
    old_level = log.level
    log.addHandler(lines)
    log.setLevel(logging.INFO)
    yield lines
    log.removeHandler(lines)
    log.setLevel(old_level)


def test_download_run_is_logged(tmp_path, captured):
    database_path = tmp_path / "t.db"
    init_db(database_path)
    downloader = DownloaderService(
        download_root=tmp_path / "downloads",
        catalogue_service=object(),
        client_factory=FakeNSE(),
        history=DownloadHistory(database_path),
    )

    result = downloader.download_root_folders("CM", ["Reports"], "13-11-2026")
    messages = [l["message"] for l in captured.since()["lines"]]

    run = result["run_id"]
    assert messages[0].startswith(f"Run #{run} started: CM 13-11-2026")
    assert sum(m.startswith("Downloaded CM Reports/") for m in messages) == result["downloaded"]
    assert any(" in " in m and m.startswith("Downloaded") for m in messages)
    assert messages[-1].startswith(f"Run #{run} completed in ")
    assert "file(s) seen were for 13-11-2026" in messages[-1]


def test_logs_endpoint(monkeypatch):
    import app.main as main

    # setup_logging() attaches `recent` at app start; do it by hand here.
    recent.clear()
    log.addHandler(recent)
    try:
        log.warning("something to see")
    finally:
        log.removeHandler(recent)

    http = TestClient(main.app)
    data = http.get("/api/logs").json()

    assert data["lines"][-1]["message"] == "something to see"
    assert data["lines"][-1]["level"] == "WARNING"
    assert http.get(f"/api/logs?after={data['last']}").json()["lines"] == []
