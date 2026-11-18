
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import init_db
from app.services.download_history import DownloadHistory
from app.services.nse_activity import NSEActivity, NSEBusyError
from app.services.scheduler import ScheduleStore, Scheduler


TUESDAY_2200 = datetime(2026, 11, 17, 22, 0)  # 17-11-2026 is a Tuesday


class FakeDownloader:
    def __init__(self):
        self.calls = []
        self.failing_segments = set()

    def download_folder(self, segment, folder_path, date, mode="folder"):
        self.calls.append((segment, folder_path, date, mode))

        if segment in self.failing_segments:
            return {"run_id": len(self.calls), "downloaded": 0,
                    "already_downloaded": 0, "failed": 1, "status": "partial"}

        return {"run_id": len(self.calls), "downloaded": 1,
                "already_downloaded": 2, "failed": 0, "status": "completed"}


class Clock:
    def __init__(self, now):
        self.value = now

    def __call__(self):
        return self.value


@pytest.fixture
def env(tmp_path):
    database_path = tmp_path / "test.db"
    init_db(database_path)

    clock = Clock(TUESDAY_2200)
    store = ScheduleStore(database_path)
    downloader = FakeDownloader()
    scheduler = Scheduler(
        store, downloader, NSEActivity(),
        history=DownloadHistory(database_path), now=clock,
    )
    return store, scheduler, downloader, clock, database_path


def nightly(**overrides):
    schedule = {
        "name": "Nightly", "enabled": True, "time": "23:00",
        "days": [0, 1, 2, 3, 4], "segments": ["CM"], "scope": "segment",
        "folders": [], "days_back": 1, "retry_count": 2,
        "retry_delay_minutes": 15, "catch_up": True,
    }
    schedule.update(overrides)
    return schedule


def drain(scheduler):
    """Run queued jobs synchronously, like the worker thread does."""

    results = []

    while not scheduler._queue.empty():
        job = scheduler._queue.get_nowait()
        results.append((job, scheduler.run_job(job)))
        scheduler._queued.discard(job["schedule_id"])

    return results


# ---------- validation ----------

@pytest.mark.parametrize("overrides, message", [
    ({"time": "25:00"}, "HH:MM"),
    ({"days": []}, "day"),
    ({"segments": ["XX"]}, "Unsupported"),
    ({"scope": "folder", "folders": []}, "folder"),
    ({"scope": "folder", "folders": ["/Reports/../x"]}, "Invalid"),
    ({"days_back": 9}, "days_back"),
    ({"retry_count": -1}, "retry_count"),
])
def test_validation_rejects_bad_input(env, overrides, message):
    store = env[0]

    with pytest.raises(ValueError, match=message):
        store.validate(nightly(**overrides))


def test_validation_normalises_folders_and_segment_order(env):
    store = env[0]

    clean = store.validate(nightly(
        scope="folder", folders=["Reports/", "/Reports", " /Onlinebackup "],
        segments=["fo", "CM"],
    ))

    assert clean["folders"] == ["/Reports", "/Onlinebackup"]
    assert clean["segments"] == ["CM", "FO"]


# ---------- timing ----------

def test_slots_respect_days():
    schedule = nightly(days=[0, 1, 2, 3, 4])
    saturday_noon = datetime(2026, 11, 21, 12, 0)

    assert Scheduler.latest_slot(schedule, saturday_noon) == datetime(2026, 11, 20, 23, 0)
    assert Scheduler.next_slot(schedule, saturday_noon) == datetime(2026, 11, 23, 23, 0)


def test_new_schedule_never_fires_for_a_time_already_passed(env):
    store, scheduler, downloader, clock, _ = env
    clock.value = TUESDAY_2200.replace(hour=23, minute=30)

    store.create(nightly(), clock())
    scheduler.tick()

    assert scheduler._queue.empty()


def test_due_schedule_is_queued_once_and_covers_days_back(env):
    store, scheduler, downloader, clock, _ = env
    schedule = store.create(nightly(segments=["CM", "FO"]), clock())

    clock.value = TUESDAY_2200.replace(hour=23, minute=1)
    scheduler.tick()
    scheduler.tick()  # same slot again: must not queue twice

    assert scheduler._queue.qsize() == 1

    [(job, result)] = drain(scheduler)

    assert job["kind"] == "scheduled"
    assert downloader.calls == [
        ("CM", "/", "16-11-2026", "scheduled"),
        ("FO", "/", "16-11-2026", "scheduled"),
        ("CM", "/", "17-11-2026", "scheduled"),
        ("FO", "/", "17-11-2026", "scheduled"),
    ]
    assert result["status"] == "completed"

    saved = store.get(schedule["id"])
    assert saved["last_status"] == "completed"
    assert saved["last_run_ids"] == [1, 2, 3, 4]
    assert saved["retry_at"] is None
    assert "4 new" in saved["last_summary"]


def test_folder_scope_runs_each_folder(env):
    store, scheduler, downloader, clock, _ = env
    store.create(nightly(scope="folder", folders=["/Reports", "/Onlinebackup"], days_back=0), clock())

    clock.value = TUESDAY_2200.replace(hour=23)
    scheduler.tick()
    drain(scheduler)

    assert [(c[1], c[2]) for c in downloader.calls] == [
        ("/Reports", "17-11-2026"), ("/Onlinebackup", "17-11-2026"),
    ]


def test_missed_run_without_catch_up_is_recorded_as_missed(env):
    store, scheduler, downloader, clock, _ = env
    schedule = store.create(nightly(catch_up=False), clock())

    clock.value = datetime(2026, 11, 18, 9, 0)  # next morning
    scheduler.tick()

    assert scheduler._queue.empty()
    assert store.get(schedule["id"])["last_status"] == "missed"


def test_missed_run_with_catch_up_covers_the_gap(env):
    store, scheduler, downloader, clock, _ = env
    store.create(nightly(days_back=1), clock())

    clock.value = datetime(2026, 11, 18, 9, 0)  # app back next morning
    scheduler.tick()
    [(job, _)] = drain(scheduler)

    assert job["kind"] == "catch-up"
    # Missed Tuesday 23:00 slot: Monday and Tuesday, plus today (Wednesday).
    assert [c[2] for c in downloader.calls] == ["16-11-2026", "17-11-2026", "18-11-2026"]


def test_disabled_schedule_does_not_run(env):
    store, scheduler, downloader, clock, _ = env
    store.create(nightly(enabled=False), clock())

    clock.value = TUESDAY_2200.replace(hour=23)
    scheduler.tick()

    assert scheduler._queue.empty()


# ---------- retries ----------

def test_failed_run_is_retried_then_gives_up(env):
    store, scheduler, downloader, clock, _ = env
    schedule = store.create(nightly(retry_count=2, retry_delay_minutes=15, days_back=0), clock())
    downloader.failing_segments = {"CM"}

    clock.value = TUESDAY_2200.replace(hour=23)
    scheduler.tick()
    drain(scheduler)

    saved = store.get(schedule["id"])
    assert saved["last_status"] == "partial"
    assert saved["retry_at"] == "2026-11-17T23:15:00"
    assert saved["retries_left"] == 1
    assert "retry 1/2" in saved["last_summary"]

    clock.value = datetime(2026, 11, 17, 23, 15)
    scheduler.tick()
    [(job, _)] = drain(scheduler)
    assert job["kind"] == "retry"
    assert store.get(schedule["id"])["retries_left"] == 0

    clock.value = datetime(2026, 11, 17, 23, 30)
    scheduler.tick()
    drain(scheduler)

    saved = store.get(schedule["id"])
    assert saved["retry_at"] is None  # no more retries
    assert len(downloader.calls) == 3


def test_retry_stops_when_a_retry_succeeds(env):
    store, scheduler, downloader, clock, _ = env
    schedule = store.create(nightly(days_back=0), clock())
    downloader.failing_segments = {"CM"}

    clock.value = TUESDAY_2200.replace(hour=23)
    scheduler.tick()
    drain(scheduler)

    downloader.failing_segments = set()
    clock.value = datetime(2026, 11, 17, 23, 15)
    scheduler.tick()
    drain(scheduler)

    saved = store.get(schedule["id"])
    assert saved["last_status"] == "completed"
    assert saved["retry_at"] is None


def test_run_now_queues_only_once(env):
    store, scheduler, downloader, clock, _ = env
    schedule = store.create(nightly(), clock())

    assert scheduler.run_now(schedule["id"]) is True
    assert scheduler.run_now(schedule["id"]) is False

    [(job, _)] = drain(scheduler)
    assert job["kind"] == "manual"


def test_describe_shows_next_run(env):
    store, scheduler, _, clock, _ = env
    schedule = store.create(nightly(), clock())

    described = scheduler.describe(schedule)

    assert described["next_run_at"] == "2026-11-17T23:00:00"
    assert described["day_names"] == ["Mon", "Tue", "Wed", "Thu", "Fri"]


# ---------- session lock + startup ----------

def test_activity_allows_one_session_at_a_time():
    activity = NSEActivity()

    with activity.hold("Nightly: CM 17-11-2026 (1/2)"):
        with pytest.raises(NSEBusyError, match="Nightly"):
            with activity.try_hold("Browsing CM/"):
                pass

    with activity.try_hold("Browsing CM/"):
        assert activity.status()["description"] == "Browsing CM/"

    assert activity.status() is None


def test_unfinished_runs_are_marked_interrupted(env):
    _, _, _, _, database_path = env
    history = DownloadHistory(database_path)
    run_id = history.start_run("CM", "17-11-2026", "scheduled", ["/"])

    assert history.mark_interrupted_runs() == 1

    conn = sqlite3.connect(database_path)
    status = conn.execute("SELECT status FROM download_runs WHERE id = ?", (run_id,)).fetchone()[0]
    conn.close()
    assert status == "interrupted"


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main

    database_path = tmp_path / "api.db"
    init_db(database_path)
    monkeypatch.setattr(main.schedule_store, "database_path", database_path)

    # No "with": the lifespan (and the scheduler threads) do not start.
    return TestClient(main.app), main


def test_schedule_api_crud(client):
    http, _ = client

    created = http.post("/api/schedules", json=nightly(name="Evening", time="21:30"))
    assert created.status_code == 201
    schedule_id = created.json()["id"]

    assert http.get("/api/schedules").json()["schedules"][0]["name"] == "Evening"

    updated = http.put(f"/api/schedules/{schedule_id}", json=nightly(name="Late", enabled=False))
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["next_run_at"] is None

    assert http.post("/api/schedules", json=nightly(time="9pm")).status_code == 400
    assert http.put("/api/schedules/999", json=nightly()).status_code == 404

    assert http.delete(f"/api/schedules/{schedule_id}").status_code == 200
    assert http.get("/api/schedules").json()["schedules"] == []


def test_manual_request_gets_409_while_a_schedule_runs(client):
    http, main = client

    with main.activity.hold("Nightly: CM 17-11-2026 (1/1)"):
        response = http.post("/api/nse/list", json={"segment": "CM", "folder_path": "/"})
        status = http.get("/api/status").json()

    assert response.status_code == 409
    assert "Nightly" in response.json()["detail"]
    assert status["busy"] is True
    assert status["current"]["kind"] == "scheduled"
