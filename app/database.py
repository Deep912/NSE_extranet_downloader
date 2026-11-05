
import sqlite3
from app.config import settings


def get_connection():
    """Create and return a SQLite database connection."""

    settings.database_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row

    return conn


def init_db():
    """Initialise all application database tables."""

    with get_connection() as conn:

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
