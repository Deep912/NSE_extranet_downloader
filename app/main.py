from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db, get_connection
from app.services.downloader import DownloaderService
from app.services.nse_client import NSEExtranetClient, NSEAPIError

BASE_DIR = Path(__file__).resolve().parent
downloader = DownloaderService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(
    title="NSE Extranet Downloader",
    version="0.1.0",
    description="Local application for segment-wise NSE Extranet file downloads."
    , lifespan=lifespan
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

@app.get("/health")
def health():
    return {"status": "ok", "application": settings.app_name, "version": "0.1.0"}

@app.get("/api/segments")
def list_segments():
    return {"segments": ["CM", "FO", "CD", "CO", "SLB", "DEBT"]}

@app.get("/api/downloads")
def list_downloads(limit: int = 100):
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, segment, exchange_date, file_name, folder_path,
                      status, downloaded_at, error_message
               FROM downloads ORDER BY id DESC LIMIT ?""",
            (limit,)
        ).fetchall()
    return {"downloads": [dict(row) for row in rows]}

@app.get("/api/runs")
def list_runs(limit: int = 50):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, segment, mode, started_at, finished_at, status,
                      files_found, files_downloaded, files_skipped, error_message
               FROM download_runs ORDER BY id DESC LIMIT ?""",
            (limit,)
        ).fetchall()
    return {"runs": [dict(row) for row in rows]}

@app.post("/api/downloads/start")
def start_download(payload: dict):
    segment = str(payload.get("segment", "")).upper()
    if segment not in {"CM", "FO", "CD", "CO", "SLB", "DEBT"}:
        raise HTTPException(status_code=400, detail="Unsupported segment")
    try:
        result = downloader.start_segment_download(segment=segment)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    return result


@app.post("/api/nse/list")
def nse_list(payload: dict):
    segment = str(payload.get("segment", "CM")).upper()
    folder_path = str(payload.get("folder_path", "/")).strip() or "/"
    if segment != "CM":
        raise HTTPException(status_code=400, detail="Initial LIVE integration is restricted to CM.")
    if not folder_path.startswith("/") or "\\" in folder_path or ".." in folder_path.split("/"):
        raise HTTPException(status_code=400, detail="folder_path must be an absolute NSE path using forward slashes.")
    try:
        with NSEExtranetClient() as client:
            result = client.list_content(segment=segment, folder_path=folder_path)
        return {
            "segment": segment,
            "folder_path": folder_path,
            "response_code": result.get("responseCode"),
            "response_description": result.get("responseDesc") or result.get("reponseDesc"),
            "items": result.get("data", []),
        }
    except NSEAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
