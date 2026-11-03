import sqlite3
from pathlib import Path
from app.config import settings

def get_connection():
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
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
                UNIQUE(segment, exchange_date, folder_path, file_name)
            )
        """)
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
