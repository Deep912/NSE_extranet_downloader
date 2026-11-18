
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.database import get_connection


SUPPORTED_SEGMENTS = ("CM", "FO", "CD", "CO", "SLB")
MAX_FOLDERS_PER_SEGMENT = 50


def normalise_folder(path) -> str:
    """'Reports/' or ' /Reports ' -> '/Reports'. Rejects '..' and backslashes."""

    if not isinstance(path, str) or not path.strip():
        raise ValueError("Folder path cannot be empty.")

    if "\\" in path:
        raise ValueError("Folder paths must use forward slashes.")

    parts = [p.strip() for p in path.split("/") if p.strip()]

    if any(p in {".", ".."} for p in parts):
        raise ValueError(f"Invalid folder path: {path}")

    if not parts:
        raise ValueError("Use 'Whole segment' to download the segment root.")

    return "/" + "/".join(parts)


def validate_segment(segment) -> str:
    segment = str(segment or "").strip().upper()

    if segment not in SUPPORTED_SEGMENTS:
        raise ValueError(f"Unsupported segment: {segment}")

    return segment


class FolderListStore:
    """
    Saved folders per segment, used by the "Folder list" scope in manual
    downloads and schedules.
    """

    SEEDED_KEY = "folder_lists_seeded"

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

    def all(self) -> dict[str, list[str]]:
        """{segment: [folder, ...]} for every supported segment."""

        lists = {segment: [] for segment in SUPPORTED_SEGMENTS}

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT segment, folder_path FROM folder_lists ORDER BY segment, folder_path"
            ).fetchall()

        for row in rows:
            lists.setdefault(row["segment"], []).append(row["folder_path"])

        return lists

    def get(self, segment: str) -> list[str]:
        return self.all().get(validate_segment(segment), [])

    def add(self, segment: str, folder_path: str) -> list[str]:
        segment = validate_segment(segment)
        folder_path = normalise_folder(folder_path)

        current = self.get(segment)

        if folder_path not in current and len(current) >= MAX_FOLDERS_PER_SEGMENT:
            raise ValueError(f"At most {MAX_FOLDERS_PER_SEGMENT} folders per segment.")

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO folder_lists (segment, folder_path, added_at)
                VALUES (?, ?, ?)
                """,
                (segment, folder_path, datetime.now(timezone.utc).isoformat()),
            )

        return self.get(segment)

    def remove(self, segment: str, folder_path: str) -> list[str]:
        segment = validate_segment(segment)
        folder_path = normalise_folder(folder_path)

        with self._connect() as conn:
            conn.execute(
                "DELETE FROM folder_lists WHERE segment = ? AND folder_path = ?",
                (segment, folder_path),
            )

        return self.get(segment)

    def seed_once(self, roots: dict[str, list[str]]) -> bool:
        """
        Fill the lists from config (DOWNLOAD_ROOTS) the first time only,
        so folders a user removes later do not come back on restart.
        """

        with self._connect() as conn:
            seeded = conn.execute(
                "SELECT value FROM app_meta WHERE key = ?", (self.SEEDED_KEY,)
            ).fetchone()

            if seeded:
                return False

            now = datetime.now(timezone.utc).isoformat()

            for segment, folders in roots.items():
                if segment not in SUPPORTED_SEGMENTS:
                    continue

                for folder in folders:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO folder_lists (segment, folder_path, added_at)
                        VALUES (?, ?, ?)
                        """,
                        (segment, normalise_folder(folder), now),
                    )

            conn.execute(
                "INSERT INTO app_meta (key, value) VALUES (?, ?)",
                (self.SEEDED_KEY, now),
            )

        return True
