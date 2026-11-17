
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings, DOWNLOAD_ROOTS
from app.database import init_db, get_connection
from app.services.downloader import DownloaderService
from app.services.folder_catalogue_service import FolderCatalogueService
from app.services.nse_client import NSEExtranetClient, NSEAPIError


BASE_DIR = Path(__file__).resolve().parent

downloader = DownloaderService()
catalogue_service = FolderCatalogueService()

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


# --------------------------------------------------
# VALIDATION HELPERS
# --------------------------------------------------

def validate_segment(segment):
    if not isinstance(segment, str):
        raise HTTPException(
            status_code=400,
            detail="Segment must be a string.",
        )

    segment = segment.strip().upper()

    if segment not in SUPPORTED_SEGMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported segment: {segment}",
        )

    return segment


def validate_date(date):
    if not isinstance(date, str) or not date.strip():
        raise HTTPException(
            status_code=400,
            detail="date is required in DD-MM-YYYY format.",
        )

    return date.strip()


def validate_folder_request(payload: dict):
    segment = validate_segment(payload.get("segment", "CM"))

    folder_path = payload.get("folder_path", "/")

    if not isinstance(folder_path, str):
        raise HTTPException(
            status_code=400,
            detail="folder_path must be a string.",
        )

    folder_path = folder_path.strip() or "/"

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


# --------------------------------------------------
# DASHBOARD AND HEALTH
# --------------------------------------------------

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
    return {
        "segments": sorted(SUPPORTED_SEGMENTS)
    }


# --------------------------------------------------
# DOWNLOAD HISTORY
# --------------------------------------------------

@app.get("/api/downloads")
def list_downloads(limit: int = 100):

    if limit < 1 or limit > 500:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 500",
        )

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                segment,
                exchange_date,
                file_name,
                folder_path,
                status,
                downloaded_at,
                error_message
            FROM downloads
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {
        "downloads": [dict(row) for row in rows]
    }


@app.get("/api/runs")
def list_runs(limit: int = 50):

    if limit < 1 or limit > 200:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 200",
        )

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                segment,
                mode,
                started_at,
                finished_at,
                status,
                files_found,
                files_downloaded,
                files_skipped,
                error_message
            FROM download_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {
        "runs": [dict(row) for row in rows]
    }


# --------------------------------------------------
# LEGACY DOWNLOAD START ENDPOINT
# --------------------------------------------------

@app.post("/api/downloads/start")
def start_download(payload: dict):

    validate_segment(payload.get("segment", ""))

    raise HTTPException(
        status_code=501,
        detail=(
            "This legacy endpoint is not connected to the "
            "date-based downloader. Use /api/nse/download-root "
            "or /api/nse/download-segments instead."
        ),
    )


# --------------------------------------------------
# NSE FOLDER LISTING
# --------------------------------------------------

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
            status_code=400,
            detail=str(exc),
        ) from exc


# --------------------------------------------------
# RECURSIVE NSE SCAN
# --------------------------------------------------

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
            status_code=400,
            detail=str(exc),
        ) from exc


# --------------------------------------------------
# NSE CATALOGUE SCAN
# --------------------------------------------------

@app.post("/api/nse/scan-catalogue")
def scan_nse_catalogue(segment: str = "CM"):
    """
    Scan NSE folders and save the discovered
    folder and file structure into SQLite.
    """

    segment = validate_segment(segment)

    try:
        print(
            f"[NSE] Starting catalogue scan: {segment}",
            flush=True,
        )

        with NSEExtranetClient() as client:
            scan_result = client.list_all_content(
                segment=segment,
                root_path="/",
            )

        saved_result = catalogue_service.save_scan_result(
            segment=segment,
            scan_result=scan_result,
            service="member",
        )

        print(
            f"[NSE] Catalogue saved: "
            f"{saved_result['folders_saved']} folders, "
            f"{saved_result['files_saved']} files",
            flush=True,
        )

        return {
            "success": True,
            "message": "NSE catalogue scan completed",
            "scan": {
                **saved_result,
                "folders_scanned": scan_result["folders_scanned"],
                "total_items": scan_result["total_items"],
                "scan_complete": scan_result["scan_complete"],
            },
        }

    except NSEAPIError as exc:
        print(
            f"[NSE] API error: {exc}",
            flush=True,
        )

        raise HTTPException(
            status_code=502,
            detail=f"NSE API error: {exc}",
        ) from exc

    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        print(
            f"[NSE] Catalogue scan failed: {exc}",
            flush=True,
        )

        raise HTTPException(
            status_code=500,
            detail="Catalogue scan failed. Check application logs.",
        ) from exc


# --------------------------------------------------
# DOWNLOAD CONFIGURED ROOT FOLDERS
# --------------------------------------------------

@app.post("/api/nse/download-root")
def nse_download_root(payload: dict):
    """
    Recursively scan configured NSE root folders
    and download matching individual files.
    """

    segment = validate_segment(
        payload.get("segment", "CM")
    )

    date = validate_date(
        payload.get("date", "")
    )

    root_folders = payload.get("root_folders")

    if root_folders is None:
        root_folders = DOWNLOAD_ROOTS.get(segment, [])

    if not isinstance(root_folders, list) or not root_folders:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No root folders configured for {segment}. "
                "Check DOWNLOAD_ROOTS in app/config.py."
            ),
        )

    try:
        return downloader.download_root_folders(
            segment=segment,
            root_folders=root_folders,
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

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not save downloaded files: {exc}",
        ) from exc


# --------------------------------------------------
# DOWNLOAD ONE FOLDER
# --------------------------------------------------

@app.post("/api/nse/download-folder")
def nse_download_folder(payload: dict):
    """Download matching files for one date from one NSE folder."""

    segment, folder_path = validate_folder_request(payload)

    date = validate_date(
        payload.get("date", "")
    )

    try:
        return downloader.download_folder(
            segment=segment,
            folder_path=folder_path,
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

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not save downloaded files: {exc}",
        ) from exc


# --------------------------------------------------
# DOWNLOAD MULTIPLE SEGMENTS
# --------------------------------------------------

@app.post("/api/nse/download-segments")
def nse_download_segments(payload: dict):
    """
    Recursively scan and download matching files
    from the selected NSE segments.
    """

    segments = payload.get("segments", [])

    date = validate_date(
        payload.get("date", "")
    )

    if not isinstance(segments, list) or not segments:
        raise HTTPException(
            status_code=400,
            detail="Select at least one segment.",
        )

    validated_segments = []

    for segment in segments:
        validated_segments.append(
            validate_segment(segment)
        )

    # Remove duplicate segment selections.
    validated_segments = list(
        dict.fromkeys(validated_segments)
    )

    try:
        return downloader.download_segments(
            segments=validated_segments,
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

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not save downloaded files: {exc}",
        ) from exc
