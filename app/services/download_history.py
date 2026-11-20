import json
import re
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

    def mark_interrupted_runs(self) -> int:
        """
        Mark runs still shown as 'running' as 'interrupted'.

        Called at startup: nothing can be running yet, so these runs were
        cut off when the application stopped.
        """

        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE download_runs
                SET status = 'interrupted',
                    finished_at = COALESCE(finished_at, ?),
                    error_message = COALESCE(
                        error_message,
                        'The application stopped during this run.'
                    )
                WHERE status = 'running'
                """,
                (self._now(),),
            )

            return cursor.rowcount

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
        failed_folders: list[dict] | None = None,
    ) -> None:
        """Store the final counts and status of a run."""

        failed_json = json.dumps([
            {"folder_path": item.get("folder_path", ""), "error": item.get("error", "")}
            for item in failed_folders
        ]) if failed_folders else None

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
                    error_message = ?,
                    failed_folders = ?
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
                    failed_json,
                    run_id,
                ),
            )

    def get_run(self, run_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM download_runs WHERE id = ?", (run_id,)
            ).fetchone()

        return dict(row) if row else None

    def failed_files(self, run_id: int) -> list[tuple[str, str]]:
        """(folder_path, file_name) of files whose latest attempt, in this run, failed."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT folder_path, file_name FROM downloads
                WHERE run_id = ? AND status = 'failed'
                ORDER BY folder_path, file_name
                """,
                (run_id,),
            ).fetchall()

        return [(row["folder_path"], row["file_name"]) for row in rows]

    @staticmethod
    def failed_folder_paths(run: dict) -> list[str]:
        """
        Folder paths (internal form, "" = root) that failed in a run.

        Older runs only have the summary "3 folder(s) failed: Accounts/;
        Onlinebackup/" in error_message, so that is read as a fallback.
        """

        raw = run.get("failed_folders")

        if raw:
            try:
                return [
                    str(item.get("folder_path") or "")
                    for item in json.loads(raw)
                    if isinstance(item, dict)
                ]
            except ValueError:
                pass

        match = re.match(r"\d+ folder\(s\) failed: (.*)$", run.get("error_message") or "")

        if not match:
            return []

        paths = [part.strip() for part in match.group(1).split("; ") if part.strip()]
        return ["" if path == "/" else path for path in paths]

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

    # ---------- deleting history ----------

    # exchange_date is stored as DD-MM-YYYY; this turns it into YYYY-MM-DD
    # so date ranges compare correctly.
    @staticmethod
    def _iso(column: str) -> str:
        return (
            f"(substr({column}, 7, 4) || '-' || substr({column}, 4, 2)"
            f" || '-' || substr({column}, 1, 2))"
        )

    def delete_run(self, run_id: int) -> dict | None:
        """
        Delete one run and the file records it last updated.

        Returns what was removed (including the local paths of those files,
        so the caller can delete them from disk), or None if not found.
        """

        with self._connect() as conn:
            run = conn.execute(
                "SELECT * FROM download_runs WHERE id = ?", (run_id,)
            ).fetchone()

            if run is None:
                return None

            if run["status"] == "running":
                raise ValueError("This run is still running. Stop it first.")

            files = conn.execute(
                "SELECT id, local_path FROM downloads WHERE run_id = ?", (run_id,)
            ).fetchall()

            conn.execute("DELETE FROM downloads WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM download_runs WHERE id = ?", (run_id,))

        return {
            "runs": 1,
            "file_records": len(files),
            "local_paths": [f["local_path"] for f in files if f["local_path"]],
        }

    def delete_file_record(self, download_id: int) -> dict | None:
        """Delete one file record. Returns it (with local_path) or None."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM downloads WHERE id = ?", (download_id,)
            ).fetchone()

            if row is None:
                return None

            conn.execute("DELETE FROM downloads WHERE id = ?", (download_id,))

        return {
            "runs": 0,
            "file_records": 1,
            "local_paths": [row["local_path"]] if row["local_path"] else [],
            "file_name": row["file_name"],
        }

    def cleanup(
        self,
        segment: str | None,
        date_from: str,
        date_to: str,
        dry_run: bool = True,
    ) -> dict:
        """
        Delete runs and file records for exchange dates in a range.

        date_from / date_to are YYYY-MM-DD (inclusive). segment None = all.
        With dry_run, nothing is deleted and the counts are a preview.
        Running runs are never deleted.
        """

        conditions = [f"{self._iso('exchange_date')} BETWEEN ? AND ?"]
        params: list = [date_from, date_to]

        if segment:
            conditions.append("segment = ?")
            params.append(segment)

        where = " AND ".join(conditions)

        with self._connect() as conn:
            runs = conn.execute(
                f"SELECT id FROM download_runs WHERE {where} AND status != 'running'",
                params,
            ).fetchall()

            files = conn.execute(
                f"SELECT id, local_path, file_size FROM downloads WHERE {where}",
                params,
            ).fetchall()

            if not dry_run:
                conn.execute(f"DELETE FROM downloads WHERE {where}", params)
                conn.execute(
                    f"DELETE FROM download_runs WHERE {where} AND status != 'running'",
                    params,
                )

        return {
            "dry_run": dry_run,
            "runs": len(runs),
            "file_records": len(files),
            "local_paths": [f["local_path"] for f in files if f["local_path"]],
            "bytes": sum(f["file_size"] or 0 for f in files),
        }

    def get_file_record(self, download_id: int) -> dict | None:
        """One row of the downloads table, or None."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM downloads WHERE id = ?", (download_id,)
            ).fetchone()

        return dict(row) if row else None

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
