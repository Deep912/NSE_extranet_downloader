
import json
import queue
import re
import threading
import time as time_module
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.database import get_connection
from app.services.download_history import DownloadHistory
from app.services.nse_activity import NSEActivity


SUPPORTED_SEGMENTS = ("CM", "FO", "CD", "CO", "SLB")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

MAX_FOLDERS = 20
MAX_DATES_PER_RUN = 10

DEFAULT_SCHEDULE = {
    "name": "Nightly",
    "enabled": True,
    "time": "23:00",
    "days": [0, 1, 2, 3, 4],
    "segments": ["CM"],
    "scope": "segment",
    "folders": [],
    "days_back": 1,
    "retry_count": 2,
    "retry_delay_minutes": 15,
    "catch_up": True,
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _nse_date(day: date) -> str:
    return day.strftime("%d-%m-%Y")


# --------------------------------------------------
# STORE
# --------------------------------------------------

def _retry_steps(schedule: dict) -> list[tuple[date, str]]:
    """
    The (date, segment) steps a retry should repeat.

    Saved as [date, segment] pairs; older rows saved plain dates, which
    mean every segment of the schedule on that date.
    """

    steps = []

    for entry in schedule.get("retry_dates") or []:
        if isinstance(entry, str):
            day = date.fromisoformat(entry)
            steps.extend((day, segment) for segment in schedule["segments"])
        else:
            day, segment = entry
            steps.append((date.fromisoformat(day), segment))

    return steps


class ScheduleStore:
    """Validate and persist schedules (table: schedules)."""

    JSON_FIELDS = ("days", "segments", "folders", "last_run_ids", "retry_dates")
    BOOL_FIELDS = ("enabled", "catch_up")

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    @contextmanager
    def _connect(self):
        conn = get_connection(self.database_path)

        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ---------- validation ----------

    @staticmethod
    def _int(payload, key, low, high):
        value = payload.get(key, DEFAULT_SCHEDULE[key])

        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"{key} must be a whole number.")

        try:
            value = int(value)
        except ValueError as exc:
            raise ValueError(f"{key} must be a whole number.") from exc

        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}.")

        return value

    @staticmethod
    def _normalise_folder(path) -> str:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Folder paths cannot be empty.")

        if "\\" in path:
            raise ValueError("Folder paths must use forward slashes.")

        parts = [p.strip() for p in path.split("/") if p.strip()]

        if any(p in {".", ".."} for p in parts):
            raise ValueError(f"Invalid folder path: {path}")

        return "/" + "/".join(parts)

    def validate(self, payload: dict) -> dict:
        """Return a clean schedule dict or raise ValueError."""

        if not isinstance(payload, dict):
            raise ValueError("Schedule must be a JSON object.")

        name = str(payload.get("name", DEFAULT_SCHEDULE["name"])).strip()

        if not name or len(name) > 60:
            raise ValueError("Name must be 1 to 60 characters.")

        time_of_day = str(payload.get("time", DEFAULT_SCHEDULE["time"])).strip()

        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_of_day):
            raise ValueError("Time must use 24-hour HH:MM format.")

        days = payload.get("days", DEFAULT_SCHEDULE["days"])

        if (
            not isinstance(days, list)
            or not days
            or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days)
        ):
            raise ValueError("Pick at least one day (0 = Monday ... 6 = Sunday).")

        segments = payload.get("segments", DEFAULT_SCHEDULE["segments"])

        if not isinstance(segments, list) or not segments:
            raise ValueError("Pick at least one segment.")

        segments = [str(s).strip().upper() for s in segments]
        unknown = [s for s in segments if s not in SUPPORTED_SEGMENTS]

        if unknown:
            raise ValueError(f"Unsupported segments: {', '.join(unknown)}")

        scope = payload.get("scope", DEFAULT_SCHEDULE["scope"])

        # segment = whole segment, list = the segment's saved folder list,
        # folder = folders typed into this schedule.
        if scope not in ("segment", "list", "folder"):
            raise ValueError("Scope must be 'segment', 'list' or 'folder'.")

        folders = payload.get("folders", [])

        if not isinstance(folders, list):
            raise ValueError("Folders must be a list of paths.")

        folders = list(dict.fromkeys(self._normalise_folder(f) for f in folders))

        if scope == "folder" and not folders:
            raise ValueError("Add at least one folder path, or use 'Whole segment'.")

        if len(folders) > MAX_FOLDERS:
            raise ValueError(f"At most {MAX_FOLDERS} folders per schedule.")

        return {
            "name": name,
            "enabled": bool(payload.get("enabled", DEFAULT_SCHEDULE["enabled"])),
            "time": time_of_day,
            "days": sorted(set(days)),
            "segments": [s for s in SUPPORTED_SEGMENTS if s in segments],
            "scope": scope,
            "folders": folders if scope == "folder" else [],
            "days_back": self._int(payload, "days_back", 0, 7),
            "retry_count": self._int(payload, "retry_count", 0, 5),
            "retry_delay_minutes": self._int(payload, "retry_delay_minutes", 1, 240),
            "catch_up": bool(payload.get("catch_up", DEFAULT_SCHEDULE["catch_up"])),
        }

    # ---------- persistence ----------

    def _to_dict(self, row) -> dict:
        item = dict(row)
        item["time"] = item.pop("time_of_day")

        for field in self.JSON_FIELDS:
            item[field] = json.loads(item[field]) if item.get(field) else []

        for field in self.BOOL_FIELDS:
            item[field] = bool(item[field])

        return item

    def list(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM schedules ORDER BY time_of_day, id").fetchall()

        return [self._to_dict(row) for row in rows]

    def get(self, schedule_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()

        return self._to_dict(row) if row else None

    def create(self, payload: dict, now: datetime) -> dict:
        clean = self.validate(payload)

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO schedules (
                    name, enabled, time_of_day, days, segments, scope, folders,
                    days_back, retry_count, retry_delay_minutes, catch_up,
                    last_slot, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean["name"], int(clean["enabled"]), clean["time"],
                    json.dumps(clean["days"]), json.dumps(clean["segments"]),
                    clean["scope"], json.dumps(clean["folders"]),
                    clean["days_back"], clean["retry_count"],
                    clean["retry_delay_minutes"], int(clean["catch_up"]),
                    # Slots before now are treated as already handled, so a
                    # new schedule never fires for a time that has passed.
                    _iso(Scheduler.latest_slot(clean, now)),
                    _iso(now), _iso(now),
                ),
            )

        return self.get(cursor.lastrowid)

    def update(self, schedule_id: int, payload: dict, now: datetime) -> dict | None:
        if self.get(schedule_id) is None:
            return None

        clean = self.validate(payload)

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE schedules
                SET name = ?, enabled = ?, time_of_day = ?, days = ?,
                    segments = ?, scope = ?, folders = ?, days_back = ?,
                    retry_count = ?, retry_delay_minutes = ?, catch_up = ?,
                    last_slot = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    clean["name"], int(clean["enabled"]), clean["time"],
                    json.dumps(clean["days"]), json.dumps(clean["segments"]),
                    clean["scope"], json.dumps(clean["folders"]),
                    clean["days_back"], clean["retry_count"],
                    clean["retry_delay_minutes"], int(clean["catch_up"]),
                    _iso(Scheduler.latest_slot(clean, now)),
                    _iso(now), schedule_id,
                ),
            )

        return self.get(schedule_id)

    def delete(self, schedule_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))

        return cursor.rowcount > 0

    def set_fields(self, schedule_id: int, **fields) -> None:
        """Update run-state columns (last_*, retry_*)."""

        if not fields:
            return

        values = [
            json.dumps(value) if key in self.JSON_FIELDS and value is not None else value
            for key, value in fields.items()
        ]

        assignments = ", ".join(f"{key} = ?" for key in fields)

        with self._connect() as conn:
            conn.execute(
                f"UPDATE schedules SET {assignments} WHERE id = ?",
                (*values, schedule_id),
            )


# --------------------------------------------------
# SCHEDULER
# --------------------------------------------------

class Scheduler:
    """
    Background scheduler running inside the application process.

    A ticker thread checks schedules every few seconds and queues due runs;
    one worker thread executes queued runs one at a time, waiting for the
    shared NSE session lock so it never overlaps a manual download.
    """

    def __init__(
        self,
        store: ScheduleStore,
        downloader,
        activity: NSEActivity,
        history: DownloadHistory | None = None,
        now=datetime.now,
        tick_seconds: float = 15,
        due_window: timedelta = timedelta(minutes=5),
        folder_lists=None,
    ):
        self.store = store
        self.downloader = downloader
        self.activity = activity
        self.folder_lists = folder_lists
        self.history = history or DownloadHistory(store.database_path)
        self.now = now
        self.tick_seconds = tick_seconds
        self.due_window = due_window

        self._queue: queue.Queue = queue.Queue()
        self._queued: set[int] = set()
        self._queued_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ---------- time helpers ----------

    @staticmethod
    def _slot_time(schedule: dict) -> time:
        hour, minute = map(int, schedule["time"].split(":"))
        return time(hour, minute)

    @staticmethod
    def latest_slot(schedule: dict, now: datetime) -> datetime | None:
        """The most recent scheduled time at or before now."""

        slot_time = Scheduler._slot_time(schedule)

        for back in range(8):
            day = now.date() - timedelta(days=back)
            slot = datetime.combine(day, slot_time)

            if day.weekday() in schedule["days"] and slot <= now:
                return slot

        return None

    @staticmethod
    def next_slot(schedule: dict, now: datetime) -> datetime | None:
        """The next scheduled time after now."""

        slot_time = Scheduler._slot_time(schedule)

        for ahead in range(8):
            day = now.date() + timedelta(days=ahead)
            slot = datetime.combine(day, slot_time)

            if day.weekday() in schedule["days"] and slot > now:
                return slot

        return None

    @staticmethod
    def dates_for(schedule: dict, base_date: date, today: date) -> list[date]:
        """
        Dates a run covers: base_date minus days_back, up to today.

        For a normal run base_date is today. For a catch-up after the app
        was off, base_date is the missed day, so the gap is covered too.
        """

        start = min(base_date, today) - timedelta(days=schedule["days_back"])
        count = (today - start).days + 1
        dates = [start + timedelta(days=i) for i in range(count)]

        return dates[-MAX_DATES_PER_RUN:]

    # ---------- lifecycle ----------

    def start(self) -> None:
        interrupted = self.history.mark_interrupted_runs()

        if interrupted:
            print(f"[Scheduler] Marked {interrupted} unfinished run(s) as interrupted.", flush=True)

        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._tick_loop, name="schedule-ticker", daemon=True),
            threading.Thread(target=self._worker_loop, name="schedule-worker", daemon=True),
        ]

        for thread in self._threads:
            thread.start()

        print("[Scheduler] Started.", flush=True)

    def stop(self) -> None:
        self._stop.set()

        for thread in self._threads:
            thread.join(timeout=2)

    @property
    def running(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    def is_queued(self, schedule_id: int) -> bool:
        with self._queued_lock:
            return schedule_id in self._queued

    # ---------- deciding what is due ----------

    def tick(self) -> None:
        """Queue every schedule that is due now. Safe to call repeatedly."""

        now = self.now()

        for schedule in self.store.list():
            if not schedule["enabled"]:
                continue

            slot = self.latest_slot(schedule, now)
            last_slot = _parse(schedule["last_slot"])

            if slot is not None and (last_slot is None or slot > last_slot):
                self.store.set_fields(schedule["id"], last_slot=_iso(slot))

                if now - slot <= self.due_window:
                    self._enqueue({"schedule_id": schedule["id"], "kind": "scheduled", "base_date": slot.date()})

                elif schedule["catch_up"]:
                    self._enqueue({"schedule_id": schedule["id"], "kind": "catch-up", "base_date": slot.date()})

                else:
                    self.store.set_fields(
                        schedule["id"],
                        last_status="missed",
                        last_summary=f"Missed the {slot:%a %d %b %H:%M} run (the app was not running).",
                    )

            retry_at = _parse(schedule["retry_at"])

            if retry_at is not None and now >= retry_at:
                self.store.set_fields(schedule["id"], retry_at=None)
                steps = _retry_steps(schedule)

                if not steps:  # nothing recorded to retry
                    continue

                self._enqueue({
                    "schedule_id": schedule["id"],
                    "kind": "retry",
                    "steps": steps,
                    "retries_left": schedule["retries_left"],
                })

    def run_now(self, schedule_id: int) -> bool:
        """Queue a schedule immediately. False if it is already queued."""

        return self._enqueue({"schedule_id": schedule_id, "kind": "manual", "base_date": self.now().date()})

    def _enqueue(self, job: dict) -> bool:
        with self._queued_lock:
            if job["schedule_id"] in self._queued:
                return False

            self._queued.add(job["schedule_id"])

        self.store.set_fields(job["schedule_id"], last_status="queued")
        self._queue.put(job)
        return True

    # ---------- running ----------

    def _tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # keep the scheduler alive
                print(f"[Scheduler] Tick failed: {type(exc).__name__}: {exc}", flush=True)

            self._stop.wait(self.tick_seconds)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            try:
                self.run_job(job)
            except Exception as exc:
                print(f"[Scheduler] Run failed: {type(exc).__name__}: {exc}", flush=True)
                self.store.set_fields(
                    job["schedule_id"],
                    last_status="failed",
                    last_summary=f"{type(exc).__name__}: {exc}",
                    last_run_finished=_iso(self.now()),
                )
            finally:
                with self._queued_lock:
                    self._queued.discard(job["schedule_id"])

    def run_job(self, job: dict) -> dict | None:
        """Run one schedule pass synchronously. Returns the summary."""

        schedule = self.store.get(job["schedule_id"])

        if schedule is None:  # deleted while queued
            return None

        today = self.now().date()

        def folders_for(segment: str) -> list[str]:
            if schedule["scope"] == "folder":
                return schedule["folders"]
            if schedule["scope"] == "list":
                return self.folder_lists.get(segment) if self.folder_lists else []
            return ["/"]

        # One run per date and segment; all its folders go in that run.
        # A retry repeats only the steps that had problems last time.
        if job.get("steps"):
            steps = list(job["steps"])
        else:
            dates = self.dates_for(schedule, job["base_date"], today)
            steps = [(day, segment) for day in dates for segment in schedule["segments"]]

        name = schedule["name"]
        totals = {"downloaded": 0, "already": 0, "failed": 0, "errors": 0}
        errors: list[str] = []
        run_ids = []
        failed_steps = []
        stopped = False

        self.store.set_fields(schedule["id"], last_status="waiting")

        with self.activity.hold(f"{name}: starting", kind="scheduled"):
            self.store.set_fields(
                schedule["id"],
                last_status="running",
                last_run_started=_iso(self.now()),
            )

            for number, (day, segment) in enumerate(steps, start=1):
                if self.activity.stop_requested():
                    stopped = True
                    break

                self.activity.update(
                    f"{name}: {segment} {_nse_date(day)} ({number}/{len(steps)})"
                )

                try:
                    folders = folders_for(segment)

                    if not folders:
                        raise ValueError(f"The folder list for {segment} is empty.")

                    result = self.downloader.download_folders(
                        segment, folders, _nse_date(day), mode="scheduled"
                    )
                except Exception as exc:
                    totals["errors"] += 1
                    failed_steps.append((day, segment))
                    errors.append(f"{segment} {_nse_date(day)}: {exc}")
                    print(f"[Scheduler] {segment} {_nse_date(day)} failed: {exc}", flush=True)
                    continue

                run_ids.append(result.get("run_id"))
                totals["downloaded"] += result.get("downloaded", 0)
                totals["already"] += result.get("already_downloaded", 0)
                totals["failed"] += result.get("failed", 0)

                if result.get("failed", 0):
                    failed_steps.append((day, segment))

                if result.get("status") == "cancelled":
                    stopped = True
                    break

        if stopped:
            status = "cancelled"
        elif totals["errors"] == len(steps):
            status = "failed"
        elif totals["failed"] or totals["errors"]:
            status = "partial"
        else:
            status = "completed"

        prefix = {
            "catch-up": "Catch-up: ",
            "retry": "Retry: ",
            "manual": "Run now: ",
        }.get(job["kind"], "")

        if stopped:
            prefix = f"Stopped by user · {prefix}"

        if job.get("steps"):
            scope = f"{len(steps)} failed date/segment step(s)"
        else:
            scope = f"{len(dates)} date(s) × {len(schedule['segments'])} segment(s)"

        summary = (
            f"{prefix}{totals['downloaded']} new, {totals['already']} already had, "
            f"{totals['failed'] + totals['errors']} failed · {scope}"
        )

        if errors:
            more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
            summary += f" · {errors[0]}{more}"

        retries_left = (
            job["retries_left"] if job["kind"] == "retry" else schedule["retry_count"]
        )

        fields = {
            "last_status": status,
            "last_summary": summary,
            "last_run_finished": _iso(self.now()),
            "last_run_ids": [r for r in run_ids if r is not None],
            "retry_at": None,
            "retry_dates": None,
            "retries_left": 0,
        }

        # A stopped run is not retried: the user asked it to stop.
        if status not in ("completed", "cancelled") and retries_left > 0 and failed_steps:
            retry_at = self.now() + timedelta(minutes=schedule["retry_delay_minutes"])
            attempt = schedule["retry_count"] - retries_left + 1

            fields.update({
                "retry_at": _iso(retry_at),
                # Stored as [date, segment] pairs in the retry_dates column.
                "retry_dates": [[d.isoformat(), seg] for d, seg in failed_steps],
                "retries_left": retries_left - 1,
                "last_summary": f"{summary} · retry {attempt}/{schedule['retry_count']} at {retry_at:%H:%M}",
            })

        self.store.set_fields(schedule["id"], **fields)

        return {"status": status, "summary": fields["last_summary"], **totals}

    # ---------- for the API ----------

    def describe(self, schedule: dict) -> dict:
        """Add next run time and queue state for display."""

        now = self.now()
        next_slot = self.next_slot(schedule, now) if schedule["enabled"] else None
        retry_at = _parse(schedule["retry_at"])

        next_run = next_slot
        next_kind = "scheduled"

        if retry_at is not None and (next_run is None or retry_at < next_run):
            next_run, next_kind = retry_at, "retry"

        return {
            **schedule,
            "day_names": [DAY_NAMES[d] for d in schedule["days"]],
            "next_run_at": _iso(next_run),
            "next_run_kind": next_kind if next_run else None,
            "queued": self.is_queued(schedule["id"]),
        }


def server_clock() -> dict:
    """Current local time and time zone name, for the UI."""

    now = datetime.now()

    return {
        "server_time": _iso(now),
        "timezone": time_module.strftime("%Z") or "local time",
    }
