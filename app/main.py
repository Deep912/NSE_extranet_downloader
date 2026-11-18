
import os
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings, DOWNLOAD_ROOTS
from app.database import init_db, get_connection
from app.services.downloader import DownloaderService
from app.services.folder_catalogue_service import FolderCatalogueService
from app.services.nse_activity import NSEBusyError, activity
from app.services.nse_client import NSEExtranetClient, NSEAPIError
from app.services.scheduler import (
    DEFAULT_SCHEDULE,
    ScheduleStore,
    Scheduler,
    server_clock,
)


BASE_DIR = Path(__file__).resolve().parent

downloader = DownloaderService()
catalogue_service = FolderCatalogueService()
schedule_store = ScheduleStore()
scheduler = Scheduler(schedule_store, downloader, activity)

SUPPORTED_SEGMENTS = {"CM", "FO", "CD", "CO", "SLB"}

DOWNLOAD_STATUSES = {"downloaded", "already_downloaded", "failed"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # Set NSE_SCHEDULER=off to run without the background scheduler
    # (for example in tests). Run uvicorn with a single worker, otherwise
    # each worker process would run its own scheduler.
    scheduler_enabled = os.getenv("NSE_SCHEDULER", "on").lower() != "off"

    if scheduler_enabled:
        scheduler.start()

    yield

    if scheduler_enabled:
        scheduler.stop()


@contextmanager
def nse_session(description: str):
    """
    Hold the single NSE session for a manual request.

    Returns HTTP 409 if a scheduled run or another request is using NSE.
    """

    try:
        with activity.try_hold(description):
            yield
    except NSEBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    """
    Serve the page with versioned asset links (?v=<file modified time>),
    so the browser fetches new CSS/JS after an update instead of using
    an old cached copy.
    """

    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    for asset in ("style.css", "app.js"):
        version = int((BASE_DIR / "static" / asset).stat().st_mtime)
        html = html.replace(f"/static/{asset}\"", f"/static/{asset}?v={version}\"")

    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


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
def list_downloads(
    limit: int = 100,
    status: str | None = None,
    run_id: int | None = None,
):
    """Recorded files, newest first. Optionally filter by status or run."""

    if limit < 1 or limit > 500:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 500",
        )

    if status is not None and status not in DOWNLOAD_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: {', '.join(sorted(DOWNLOAD_STATUSES))}",
        )

    conditions = []
    params = []

    if status is not None:
        conditions.append("status = ?")
        params.append(status)

    if run_id is not None:
        conditions.append("run_id = ?")
        params.append(run_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
                id,
                run_id,
                segment,
                exchange_date,
                file_name,
                folder_path,
                status,
                file_size,
                downloaded_at,
                error_message
            FROM downloads
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, limit),
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
                exchange_date,
                roots,
                started_at,
                finished_at,
                status,
                files_found,
                files_downloaded,
                files_skipped,
                files_failed,
                folders_failed,
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
        with nse_session(f"Browsing {segment}{folder_path}"), NSEExtranetClient() as client:
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
        with nse_session(f"Scanning {segment}{root_path}"), NSEExtranetClient() as client:
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

        with nse_session(f"Catalogue scan {segment}"), NSEExtranetClient() as client:
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

    except HTTPException:
        raise  # e.g. 409 when NSE is busy

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
        with nse_session(f"Manual download {segment} {date}"):
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
        where = segment if folder_path == "/" else f"{segment}{folder_path}"

        with nse_session(f"Manual download {where} {date}"):
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
        with nse_session(f"Manual download {', '.join(validated_segments)} {date}"):
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


# --------------------------------------------------
# STATUS (what is using NSE right now)
# --------------------------------------------------

@app.get("/api/status")
def app_status():
    current = activity.status()

    return {
        "busy": current is not None,
        "current": current,
        "scheduler_running": scheduler.running,
        **server_clock(),
    }


# --------------------------------------------------
# SCHEDULES
# --------------------------------------------------

def _schedule_or_404(schedule_id: int) -> dict:
    schedule = schedule_store.get(schedule_id)

    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    return schedule


@app.get("/api/schedules")
def list_schedules():
    return {
        "schedules": [scheduler.describe(s) for s in schedule_store.list()],
        "defaults": DEFAULT_SCHEDULE,
        "scheduler_running": scheduler.running,
        **server_clock(),
    }


@app.post("/api/schedules", status_code=201)
def create_schedule(payload: dict):
    try:
        schedule = schedule_store.create(payload, datetime.now())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return scheduler.describe(schedule)


@app.put("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: int, payload: dict):
    _schedule_or_404(schedule_id)

    try:
        schedule = schedule_store.update(schedule_id, payload, datetime.now())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return scheduler.describe(schedule)


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    _schedule_or_404(schedule_id)
    schedule_store.delete(schedule_id)
    return {"deleted": schedule_id}


@app.post("/api/schedules/{schedule_id}/run", status_code=202)
def run_schedule_now(schedule_id: int):
    """Queue a schedule to run now. It waits for any running NSE session."""

    _schedule_or_404(schedule_id)

    if not scheduler.running:
        raise HTTPException(
            status_code=503,
            detail="The scheduler is not running (NSE_SCHEDULER=off).",
        )

    if not scheduler.run_now(schedule_id):
        raise HTTPException(
            status_code=409,
            detail="This schedule is already queued or running.",
        )

    return scheduler.describe(schedule_store.get(schedule_id))
