import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

LIVE_BASE_URL = "https://www.connect2nse.com/extranet-api/"
UAT_BASE_URL = "https://www.devconnect2nse.com/extranet-api/"

@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "NSE Extranet Downloader")
    database_path: Path = Path(os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "nse_downloader.db")))
    download_dir: Path = Path(os.getenv("DOWNLOAD_DIR", str(PROJECT_ROOT / "storage")))
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    nse_environment: str = os.getenv("NSE_ENVIRONMENT", "LIVE").upper()
    nse_api_version: str = os.getenv("NSE_API_VERSION", "2.0")
    nse_member_code: str = os.getenv("NSE_MEMBER_CODE", "").strip()
    nse_login_id: str = os.getenv("NSE_LOGIN_ID", "").strip()
    nse_password: str = os.getenv("NSE_PASSWORD", "")
    nse_secret_key: str = os.getenv("NSE_SECRET_KEY", "")
    nse_timeout_seconds: float = float(os.getenv("NSE_TIMEOUT_SECONDS", "30"))

    @property
    def nse_api_base_url(self) -> str:
        if self.nse_environment == "LIVE":
            return LIVE_BASE_URL
        if self.nse_environment == "UAT":
            return UAT_BASE_URL
        raise ValueError("NSE_ENVIRONMENT must be LIVE or UAT")

    @property
    def nse_credentials_configured(self) -> bool:
        return all((self.nse_member_code, self.nse_login_id, self.nse_password, self.nse_secret_key))

settings = Settings()
