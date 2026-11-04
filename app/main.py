
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

SUPPORTED_SEGMENTS = {"CM", "FO", "CD", "CO", "SLB"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="NSE Extranet Downloader",
    version="0.2.0",
    description="Local application for segment-wise NSE Extranet file downloads.",
    lifespan=lifespan,
)

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)



def validate_folder_request(payload: dict):
    segment = str(payload.get("segment", "CM")).upper().strip()
    folder_path = str(payload.get("folder_path", "/")).strip() or "/"

    if segment not in SUPPORTED_SEGMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported segment: {segment}",
        )

    if (
        not folder_path.startswith("/")
        or "\\" in folder_path
        or ".." in folder_path.split("/")
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "folder_path must be an absolute NSE path "
                "using forward slashes."
            ),
        )

    return segment, folder_path



@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (
        BASE_DIR / "templates" / "index.html"
    ).read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "application": settings.app_name,
        "version": "0.2.0",
    }


@app.get("/api/segments")
def list_segments():
    return {"segments": sorted(SUPPORTED_SEGMENTS)}


@app.get("/api/downloads")
def list_downloads(limit: int = 100):
    if limit < 1 or limit > 500:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 500",
        )

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, segment, exchange_date, file_name, folder_path,
                      status, downloaded_at, error_message
               FROM downloads ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    return {"downloads": [dict(row) for row in rows]}


@app.get("/api/runs")
def list_runs(limit: int = 50):
    if limit < 1 or limit > 200:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 200",
        )

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, segment, mode, started_at, finished_at, status,
                      files_found, files_downloaded, files_skipped, error_message
               FROM download_runs ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    return {"runs": [dict(row) for row in rows]}


@app.post("/api/downloads/start")
def start_download(payload: dict):
    segment = str(payload.get("segment", "")).upper()

    if segment not in SUPPORTED_SEGMENTS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported segment",
        )

    try:
        result = downloader.start_segment_download(segment=segment)
        return result

    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail=str(exc),
        ) from exc


@app.post("/api/nse/list")
def nse_list(payload: dict):
    """List one NSE folder without recursively scanning subfolders."""

    segment, folder_path = validate_folder_request(payload)

    try:
        with NSEExtranetClient() as client:
            result = client.list_content(
                segment=segment,
                folder_path=folder_path,
            )

        return {
            "segment": segment,
            "folder_path": folder_path,
            "response_code": result.get("responseCode"),
            "response_description": (
                result.get("responseDesc")
                or result.get("reponseDesc")
            ),
            "items": result.get("data", []),
        }

    except NSEAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@app.post("/api/nse/scan")
def nse_scan(payload: dict):
    """Recursively scan accessible folders without downloading files."""

    segment, root_path = validate_folder_request(payload)

    try:
        with NSEExtranetClient() as client:
            result = client.list_all_content(
                segment=segment,
                root_path=root_path,
            )

        return result

    except NSEAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

@app.post("/api/nse/download-folder")
def nse_download_folder(payload: dict):
    """Download all files for one date from one NSE folder."""

    segment, folder_path = validate_folder_request(payload)
    date = str(payload.get("date", "")).strip()

    if not date:
        raise HTTPException(
            status_code=400,
            detail="date is required in DD-MM-YYYY format.",
        )

    try:
        result = downloader.download_folder(
            segment=segment,
            folder_path=folder_path,
            date=date,
        )
        return result

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except NSEAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not save the downloaded file: {exc}",
        ) from exc



@app.post("/api/nse/download-segments")
def nse_download_segments(payload: dict):
    """
    Scan and download all accessible folders
    from the selected NSE segments.
    """

    segments = payload.get("segments", [])
    date = str(payload.get("date", "")).strip()

    if not isinstance(segments, list) or not segments:
        raise HTTPException(
            status_code=400,
            detail="Select at least one segment.",
        )

    if not date:
        raise HTTPException(
            status_code=400,
            detail="date is required in DD-MM-YYYY format.",
        )

    try:
        return downloader.download_segments(
            segments=segments,
            date=date,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except NSEAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
