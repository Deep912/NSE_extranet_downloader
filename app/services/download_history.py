
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.database import get_connection


class DownloadHistory:
    """
    Record download runs and individual files in SQLite.

    Tables used (see app/database.py):
        download_runs - one row per run
        downloads     - one row per (segment, exchange date, folder, file),
                        updated each time a run processes that file
    """

    # File statuses that mean "we already have this file locally".
    HAVE_FILE_STATUSES = {"downloaded", "already_downloaded"}

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @contextmanager
    def _connect(self):
        conn = get_connection(self.database_path)

        try:
            with conn:  # commits on success, rolls back on error
                yield conn
        finally:
            conn.close()

    def start_run(
        self,
        segment: str,
        exchange_date: str,
        mode: str,
        roots: list[str],
    ) -> int:
        """Create a run record with status 'running' and return its id."""

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO download_runs (
                    segment, mode, exchange_date, roots,
                    started_at, status
                )
                VALUES (?, ?, ?, ?, ?, 'running')
                """,
                (
                    segment,
                    mode,
                    exchange_date,
                    json.dumps(roots),
                    self._now(),
                ),
            )

            return cursor.lastrowid

    def finish_run(
        self,
        run_id: int,
        status: str,
        files_found: int = 0,
        files_downloaded: int = 0,
        files_skipped: int = 0,
        files_failed: int = 0,
        folders_failed: int = 0,
        error_message: str | None = None,
    ) -> None:
        """Store the final counts and status of a run."""

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE download_runs
                SET finished_at = ?,
                    status = ?,
                    files_found = ?,
                    files_downloaded = ?,
                    files_skipped = ?,
                    files_failed = ?,
                    folders_failed = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (
                    self._now(),
                    status,
                    files_found,
                    files_downloaded,
                    files_skipped,
                    files_failed,
                    folders_failed,
                    error_message,
                    run_id,
                ),
            )

    def get_recorded_files(
        self,
        segment: str,
        exchange_date: str,
    ) -> dict:
        """
        Return recorded files for one segment and date.

        Keyed by (folder_path, file_name).
        """

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT folder_path, file_name, status,
                       nse_last_updated, local_path
                FROM downloads
                WHERE segment = ? AND exchange_date = ?
                """,
                (segment, exchange_date),
            ).fetchall()

        return {
            (row["folder_path"], row["file_name"]): dict(row)
            for row in rows
        }

    def is_already_downloaded(
        self,
        record: dict | None,
        nse_last_updated,
    ) -> bool:
        """
        True when a recorded file can be skipped without contacting NSE:
        it was saved before, NSE has not changed it since (same
        lastUpdated), and the local copy still exists.
        """

        if not record:
            return False

        if record.get("status") not in self.HAVE_FILE_STATUSES:
            return False

        if record.get("nse_last_updated") != nse_last_updated:
            return False

        local_path = record.get("local_path")

        return bool(local_path) and Path(local_path).is_file()

    def record_file(
        self,
        run_id: int,
        segment: str,
        exchange_date: str,
        folder_path: str,
        file_name: str,
        status: str,
        nse_last_updated=None,
        local_path: str | None = None,
        file_size: int | None = None,
        sha256: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """
        Insert or update the record for one file.

        A failed attempt never erases the details of an earlier successful
        download, and a later "already downloaded" pass keeps the original
        status and download time.
        """

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO downloads (
                    segment, exchange_date, folder_path, file_name,
                    status, downloaded_at, file_size, checksum_sha256,
                    error_message, run_id, nse_last_updated, local_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(segment, exchange_date, folder_path, file_name)
                DO UPDATE SET
                    status = CASE
                        WHEN excluded.status IN
                             ('failed', 'already_downloaded')
                         AND downloads.status IN
                             ('downloaded', 'already_downloaded')
                        THEN downloads.status
                        ELSE excluded.status
                    END,
                    downloaded_at = CASE
                        WHEN excluded.status = 'downloaded'
                        THEN excluded.downloaded_at
                        ELSE COALESCE(
                            downloads.downloaded_at, excluded.downloaded_at
                        )
                    END,
                    file_size = COALESCE(
                        excluded.file_size, downloads.file_size
                    ),
                    checksum_sha256 = COALESCE(
                        excluded.checksum_sha256, downloads.checksum_sha256
                    ),
                    local_path = COALESCE(
                        excluded.local_path, downloads.local_path
                    ),
                    nse_last_updated = CASE
                        WHEN excluded.status = 'failed'
                        THEN downloads.nse_last_updated
                        ELSE excluded.nse_last_updated
                    END,
                    error_message = excluded.error_message,
                    run_id = excluded.run_id
                """,
                (
                    segment,
                    exchange_date,
                    folder_path,
                    file_name,
                    status,
                    self._now() if status != "failed" else None,
                    file_size,
                    sha256,
                    error_message,
                    run_id,
                    nse_last_updated,
                    local_path,
                ),
            )
