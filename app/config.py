import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "NSE Extranet Downloader")
    database_path: Path = Path(os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "nse_downloader.db")))
    download_dir: Path = Path(os.getenv("DOWNLOAD_DIR", str(PROJECT_ROOT / "storage")))
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    nse_environment: str = os.getenv("NSE_ENVIRONMENT", "UAT").upper()

settings = Settings()
