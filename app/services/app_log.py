"""
Application log.

Everything the app does (downloads, retries, schedules, admin actions) is
written to the logger "nse" and goes to three places:

- the console, with a timestamp;
- logs/app.log, a new file every midnight, kept for 30 days;
- memory (the last 2000 lines), shown in the dashboard's Log tab.

Routine GET requests from the dashboard (status polling every few seconds,
lists, static files) are left out of uvicorn's console access log, so the
console shows what actually happens.
"""

import logging
import sys
import threading
from collections import deque
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


log = logging.getLogger("nse")

LINE_FORMAT = "%(asctime)s  %(levelname)-7s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class RecentLines(logging.Handler):
    """Keep the latest log lines in memory, numbered, for the dashboard."""

    def __init__(self, capacity: int = 2000):
        super().__init__()
        self._lines: deque = deque(maxlen=capacity)
        self._seq = 0
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()

            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)

            with self._guard:
                self._seq += 1
                self._lines.append({
                    "seq": self._seq,
                    "time": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
                    "level": record.levelname,
                    "message": message,
                })
        except Exception:
            self.handleError(record)

    def since(self, after: int = 0, limit: int = 500, problems_only: bool = False) -> dict:
        """Lines newer than `after` (oldest first), at most `limit` of the newest."""

        with self._guard:
            lines = [line for line in self._lines if line["seq"] > after]
            last = self._seq

        if problems_only:
            lines = [line for line in lines if line["level"] in ("WARNING", "ERROR", "CRITICAL")]

        return {"lines": lines[-limit:], "last": last}

    def clear(self) -> None:
        with self._guard:
            self._lines.clear()


class QuietAccessLog(logging.Filter):
    """Drop successful GET requests from uvicorn's access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args

        # uvicorn.access: (client, method, path, http_version, status_code)
        if isinstance(args, tuple) and len(args) == 5:
            method, status = args[1], args[4]

            try:
                if method == "GET" and int(status) < 400:
                    return False
            except (TypeError, ValueError):
                pass

        return True


recent = RecentLines()

_configured = False
_log_file: Path | None = None


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> Path | None:
    """Attach the console, file and memory handlers once. Returns the log file."""

    global _configured, _log_file

    if _configured:
        return _log_file

    _configured = True
    formatter = logging.Formatter(LINE_FORMAT, DATE_FORMAT)

    log.setLevel(getattr(logging, level.upper(), logging.INFO))
    log.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    log.addHandler(console)

    log.addHandler(recent)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            _log_file = log_dir / "app.log"

            to_file = TimedRotatingFileHandler(
                _log_file, when="midnight", backupCount=30, encoding="utf-8",
            )
            to_file.setFormatter(formatter)
            log.addHandler(to_file)
        except OSError as exc:
            _log_file = None
            log.warning("Could not open the log file in %s: %s", log_dir, exc)

    logging.getLogger("uvicorn.access").addFilter(QuietAccessLog())

    return _log_file


def log_file() -> Path | None:
    return _log_file


def format_size(size: int | None) -> str:
    if size is None:
        return "?"

    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024

    return f"{size:.1f} MB"


def format_seconds(seconds: float) -> str:
    seconds = max(0.0, seconds)

    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes, secs = divmod(int(round(seconds)), 60)

    if minutes < 60:
        return f"{minutes}m {secs:02d}s"

    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
