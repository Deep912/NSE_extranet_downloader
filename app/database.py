
import sqlite3
from pathlib import Path

from app.config import settings


def get_connection(database_path: Path | None = None):
    """
    Create and return a SQLite database connection.

    database_path defaults to the configured database; tests pass a
    temporary path instead.
    """

    database_path = Path(database_path or settings.database_path)

    database_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row

    return conn


def _add_missing_columns(conn, table: str, columns: dict) -> None:
    """
    Add columns that older databases do not have yet.

    CREATE TABLE IF NOT EXISTS never changes an existing table, so new
    columns are added here. Existing rows get NULL or the default.
    """

    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})")
    }

    for name, definition in columns.items():
        if name not in existing:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
            )


def init_db(database_path: Path | None = None):
    """Initialise all application database tables."""

    with get_connection(database_path) as conn:

        # -------------------------------------------------
        # 1. Individual download history
        # -------------------------------------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segment TEXT NOT NULL,
                exchange_date TEXT,
                file_name TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                status TEXT NOT NULL,
                downloaded_at TEXT,
                file_size INTEGER,
                checksum_sha256 TEXT,
                error_message TEXT,
                UNIQUE(
                    segment,
                    exchange_date,
                    folder_path,
                    file_name
                )
            )
        """)

        # -------------------------------------------------
        # 2. Download run history
        # -------------------------------------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS download_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segment TEXT NOT NULL,
                mode TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                files_found INTEGER DEFAULT 0,
                files_downloaded INTEGER DEFAULT 0,
                files_skipped INTEGER DEFAULT 0,
                error_message TEXT
            )
        """)

        # -------------------------------------------------
        # 3. NSE folder catalogue
        # -------------------------------------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS folder_catalogue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                segment TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                parent_path TEXT,
                folder_name TEXT NOT NULL,
                last_scanned TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,

                UNIQUE(
                    service,
                    segment,
                    folder_path
                )
            )
        """)

        # -------------------------------------------------
        # 4. NSE file catalogue
        # -------------------------------------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_catalogue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                segment TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                last_updated TEXT,
                last_scanned TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,

                UNIQUE(
                    service,
                    segment,
                    folder_path,
                    file_name
                )
            )
        """)

        # -------------------------------------------------
        # 5. Folder scan history
        # -------------------------------------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                segment TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                folders_found INTEGER DEFAULT 0,
                files_found INTEGER DEFAULT 0,
                error_message TEXT
            )
        """)

        # -------------------------------------------------
        # 6. Columns added after the first release
        # -------------------------------------------------

        # Links each file to the run that last processed it, and keeps
        # what is needed to skip it next time without asking NSE.
        _add_missing_columns(conn, "downloads", {
            "run_id": "INTEGER",
            "nse_last_updated": "TEXT",
            "local_path": "TEXT",
        })

        _add_missing_columns(conn, "download_runs", {
            "exchange_date": "TEXT",
            "roots": "TEXT",
            "files_failed": "INTEGER DEFAULT 0",
            "folders_failed": "INTEGER DEFAULT 0",
            # JSON list of {"folder_path", "error"} for folders that failed.
            "failed_folders": "TEXT",
        })

        # -------------------------------------------------
        # 7. Download schedules
        # -------------------------------------------------
        # Times are this computer's local time. JSON columns hold lists.

        conn.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                time_of_day TEXT NOT NULL,
                days TEXT NOT NULL,
                segments TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'segment',
                folders TEXT NOT NULL DEFAULT '[]',
                days_back INTEGER NOT NULL DEFAULT 1,
                retry_count INTEGER NOT NULL DEFAULT 2,
                retry_delay_minutes INTEGER NOT NULL DEFAULT 15,
                catch_up INTEGER NOT NULL DEFAULT 1,

                last_slot TEXT,
                last_run_started TEXT,
                last_run_finished TEXT,
                last_status TEXT,
                last_summary TEXT,
                last_run_ids TEXT,
                retry_at TEXT,
                retry_dates TEXT,
                retries_left INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # -------------------------------------------------
        # 8. Saved folder lists (per segment)
        # -------------------------------------------------
        # Folders to download for the "Folder list" scope, e.g.
        # CM: /Accounts, /NSP, /Onlinebackup, /Reports.

        conn.execute("""
            CREATE TABLE IF NOT EXISTS folder_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segment TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                added_at TEXT NOT NULL,
                UNIQUE(segment, folder_path)
            )
        """)

        # Small key/value store for one-off flags (e.g. "lists seeded").
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
