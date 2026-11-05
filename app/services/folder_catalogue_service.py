
import posixpath
from datetime import datetime, timezone

from app.database import get_connection


class FolderCatalogueService:
    """Save and manage NSE folder and file catalogues."""

    VALID_SERVICES = {"member", "common"}

    @staticmethod
    def _now():
        """Return the current UTC timestamp."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalise_folder_path(path):
        """
        Convert an NSE folder path into a relative path.

        Examples:
            /Reports              -> Reports/
            /Reports/Order_Reports -> Reports/Order_Reports/
            /                      -> 
        """
        if not isinstance(path, str):
            raise ValueError("Folder path must be a string")

        path = path.replace("\\", "/").strip()

        if ".." in path.split("/"):
            raise ValueError("Invalid folder path")

        normalised = posixpath.normpath("/" + path.strip("/"))

        if normalised == "/":
            return ""

        return normalised.lstrip("/") + "/"

    @staticmethod
    def _validate_scan(segment, service, scan_result):
        if not isinstance(segment, str) or not segment.strip():
            raise ValueError("Segment is required")

        if service not in FolderCatalogueService.VALID_SERVICES:
            raise ValueError("Service must be 'member' or 'common'")

        if not isinstance(scan_result, dict):
            raise ValueError("Invalid scan result")

        if not isinstance(scan_result.get("files"), list):
            raise ValueError("Scan result must contain a files list")

        if not isinstance(scan_result.get("folders"), list):
            raise ValueError("Scan result must contain a folders list")

    def save_scan_result(
        self,
        segment: str,
        scan_result: dict,
        service: str = "member",
    ) -> dict:
        """
        Save discovered folders and files into SQLite.

        Incomplete scans update discovered records but do not
        deactivate records that were not found.
        """

        self._validate_scan(segment, service, scan_result)

        segment = segment.strip().upper()
        now = self._now()

        folders = scan_result["folders"]
        files = scan_result["files"]

        scan_complete = bool(scan_result.get("scan_complete", False))

        failed_folders = scan_result.get("failed_folders", [])

        if not isinstance(failed_folders, list):
            failed_folders = []

        # Prepare folder records.
        folder_records = {}

        for item in folders:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            full_path = item.get("fullPath")

            if not isinstance(name, str) or not name.strip():
                continue

            if not isinstance(full_path, str):
                continue

            folder_path = self._normalise_folder_path(full_path)

            if not folder_path:
                continue

            parent_path = posixpath.dirname(
                folder_path.rstrip("/")
            )

            if parent_path:
                parent_path += "/"

            folder_records[folder_path] = (
                service,
                segment,
                folder_path,
                parent_path,
                name,
                now,
                1,
            )

        # Prepare file records.
        file_records = {}

        for item in files:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            parent = item.get("folderPath", "")

            if not isinstance(name, str) or not name.strip():
                continue

            if not isinstance(parent, str):
                continue

            folder_path = self._normalise_folder_path(parent)

            file_records[(folder_path, name)] = (
                service,
                segment,
                folder_path,
                name,
                item.get("lastUpdated"),
                now,
                1,
            )

        status = "completed" if scan_complete else "partial"

        # Save everything in one database transaction.
        with get_connection() as conn:

            # Create scan history record.
            cursor = conn.execute(
                """
                INSERT INTO scan_runs (
                    service,
                    segment,
                    started_at,
                    finished_at,
                    status,
                    folders_found,
                    files_found,
                    error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service,
                    segment,
                    now,
                    now,
                    status,
                    len(folder_records),
                    len(file_records),
                    (
                        f"{len(failed_folders)} folder(s) failed"
                        if failed_folders else None
                    ),
                ),
            )

            scan_id = cursor.lastrowid

            # Only a complete scan can deactivate old records.
            if scan_complete:
                conn.execute(
                    """
                    UPDATE folder_catalogue
                    SET is_active = 0
                    WHERE service = ? AND segment = ?
                    """,
                    (service, segment),
                )

                conn.execute(
                    """
                    UPDATE file_catalogue
                    SET is_active = 0
                    WHERE service = ? AND segment = ?
                    """,
                    (service, segment),
                )

            # Insert or update folders.
            if folder_records:
                conn.executemany(
                    """
                    INSERT INTO folder_catalogue (
                        service,
                        segment,
                        folder_path,
                        parent_path,
                        folder_name,
                        last_scanned,
                        is_active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)

                    ON CONFLICT(service, segment, folder_path)
                    DO UPDATE SET
                        parent_path = excluded.parent_path,
                        folder_name = excluded.folder_name,
                        last_scanned = excluded.last_scanned,
                        is_active = 1
                    """,
                    list(folder_records.values()),
                )

            # Insert or update files.
            if file_records:
                conn.executemany(
                    """
                    INSERT INTO file_catalogue (
                        service,
                        segment,
                        folder_path,
                        file_name,
                        last_updated,
                        last_scanned,
                        is_active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)

                    ON CONFLICT(
                        service,
                        segment,
                        folder_path,
                        file_name
                    )
                    DO UPDATE SET
                        last_updated = excluded.last_updated,
                        last_scanned = excluded.last_scanned,
                        is_active = 1
                    """,
                    list(file_records.values()),
                )

        return {
            "scan_id": scan_id,
            "service": service,
            "segment": segment,
            "status": status,
            "folders_saved": len(folder_records),
            "files_saved": len(file_records),
            "failed_folders": len(failed_folders),
        }

    def get_folders(
        self,
        segment: str,
        service: str = "member",
    ) -> list:
        """Return active cached folders for a segment."""

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM folder_catalogue
                WHERE service = ?
                  AND segment = ?
                  AND is_active = 1
                ORDER BY folder_path
                """,
                (service, segment.upper()),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_files(
        self,
        segment: str,
        service: str = "member",
    ) -> list:
        """Return active cached files for a segment."""

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM file_catalogue
                WHERE service = ?
                  AND segment = ?
                  AND is_active = 1
                ORDER BY folder_path, file_name
                """,
                (service, segment.upper()),
            ).fetchall()

        return [dict(row) for row in rows]
