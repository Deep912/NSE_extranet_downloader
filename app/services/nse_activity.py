
import threading
from contextlib import contextmanager
from datetime import datetime


class NSEBusyError(RuntimeError):
    """Raised when another NSE session is already in progress."""

    def __init__(self, current: dict | None):
        self.current = current or {}

        description = self.current.get("description", "another NSE request")
        started = self.current.get("started_at", "")

        super().__init__(
            f"NSE is busy: {description}"
            + (f" (since {started})" if started else "")
            + ". Try again when it finishes."
        )


class NSEActivity:
    """
    Allow only one NSE session at a time.

    NSE permits one active login per API user, so a second login (for
    example browsing while a download runs) can end the first session.
    Manual requests use try_hold() and fail fast; the scheduler uses
    hold() and waits its turn.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._current: dict | None = None
        self._stop = threading.Event()

    def status(self) -> dict | None:
        current = self._current
        return dict(current) if current else None

    def request_stop(self) -> dict | None:
        """
        Ask the running activity to stop at its next safe point (after the
        current file or folder listing). Returns its status, or None if
        nothing is running.
        """

        current = self._current

        if current is None:
            return None

        self._stop.set()
        current["stopping"] = True
        return dict(current)

    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def update(self, description: str) -> None:
        """Change the description of the running activity (progress)."""

        if self._current is not None:
            self._current["description"] = description

    @contextmanager
    def _held(self, description: str, kind: str):
        self._stop.clear()
        self._current = {
            "description": description,
            "kind": kind,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "stopping": False,
        }

        try:
            yield self
        finally:
            self._current = None
            self._stop.clear()
            self._lock.release()

    @contextmanager
    def try_hold(self, description: str, kind: str = "manual"):
        """Hold the NSE session, or raise NSEBusyError immediately."""

        if not self._lock.acquire(blocking=False):
            raise NSEBusyError(self.status())

        with self._held(description, kind):
            yield self

    @contextmanager
    def hold(self, description: str, kind: str = "scheduled"):
        """Hold the NSE session, waiting for any current one to finish."""

        self._lock.acquire()

        with self._held(description, kind):
            yield self


# One shared instance for the whole application.
activity = NSEActivity()
