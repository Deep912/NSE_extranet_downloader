
import hashlib
import re

from datetime import datetime, date
from pathlib import Path

from app.services.nse_client import NSEExtranetClient, NSEAPIError
from app.services.folder_catalogue_service import FolderCatalogueService
from app.services.download_history import DownloadHistory


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOWNLOAD_ROOT = PROJECT_ROOT / "data" / "downloads"

SUPPORTED_SEGMENTS = {"CM", "FO", "CD", "CO", "SLB"}

MAX_FOLDER_DEPTH = 20
MAX_FOLDERS_PER_ROOT = 2000


class DownloaderService:
    """Coordinates recursive, date-filtered NSE file downloads."""

    def __init__(
        self,
        download_root: Path = DOWNLOAD_ROOT,
        catalogue_service=None,
        client_factory=None,
        history=None,
    ):
        self.download_root = Path(download_root)
        self.catalogue_service = (
            catalogue_service or FolderCatalogueService()
        )
        self.client_factory = client_factory or NSEExtranetClient
        self.history = history or DownloadHistory()

    @staticmethod
    def _validate_date(exchange_date: str) -> str:
        try:
            parsed = datetime.strptime(exchange_date, "%d-%m-%Y")
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "Date must use DD-MM-YYYY format."
            ) from exc

        if parsed.strftime("%d-%m-%Y") != exchange_date:
            raise ValueError("Date must use DD-MM-YYYY format.")

        return exchange_date

    @staticmethod
    def _validate_segment(segment: str) -> str:
        segment = str(segment).upper().strip()

        if segment not in SUPPORTED_SEGMENTS:
            raise ValueError(f"Unsupported segment: {segment}")

        return segment

    @staticmethod
    def _validate_folder_path(folder_path: str) -> str:
        if not isinstance(folder_path, str) or not folder_path.strip():
            raise ValueError("Folder path cannot be empty.")

        folder_path = folder_path.strip()

        if "\\" in folder_path:
            raise ValueError(
                "Folder path must use forward slashes only."
            )

        parts = folder_path.strip("/").split("/")

        if any(part in {".", ".."} for part in parts):
            raise ValueError("Invalid folder path.")

        if not parts or parts == [""]:
            return ""

        return "/".join(parts) + "/"

    @staticmethod
    def _api_path(folder_path: str) -> str:
        """
        Convert an internal folder path into the form sent to NSE.

        Internally, folder paths have no leading slash and end with a
        slash ("Reports/Order_Reports/"); the segment root is "".
        NSE expects "/Reports/Order_Reports" and "/" for the root.
        """

        return "/" + folder_path.strip("/")

    @staticmethod
    def _normalise_root_name(root_name: str) -> str:
        """Accept one root folder name, not a nested path."""

        if not isinstance(root_name, str) or not root_name.strip():
            raise ValueError("Root folder name cannot be empty.")

        root_name = root_name.strip()

        if (
            "/" in root_name
            or "\\" in root_name
            or root_name in {".", ".."}
        ):
            raise ValueError(
                f"Invalid root folder name: {root_name}"
            )

        return root_name

    @staticmethod
    def _safe_component(value: str) -> str:
        """Make a filename or folder name safe for Windows and Linux."""

        value = str(value).strip()

        value = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]',
            "_",
            value,
        )

        value = value.rstrip(". ")

        if not value or value in {".", ".."}:
            return "_"

        return value

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _get_file_date(item: dict) -> date | None:
        """
        Extract the file's NSE lastUpdated date.

        NSE timestamps are documented as UTC. ISO timestamps containing
        timezone information are converted to UTC before comparison.
        """

        value = item.get("lastUpdated")

        if value is None:
            return None

        if not isinstance(value, str):
            return None

        value = value.strip()

        if not value:
            return None

        # Handle ISO timestamps, including a trailing Z.
        try:
            parsed = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )

            if parsed.tzinfo is not None:
                from datetime import timezone

                parsed = parsed.astimezone(timezone.utc)

            return parsed.date()

        except ValueError:
            pass

        # Handle a date without a time component.
        for date_format in (
            "%d-%m-%Y",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(
                    value,
                    date_format,
                ).date()
            except ValueError:
                continue

        return None

    def _save_file(
        self,
        segment: str,
        folder_path: str,
        filename: str,
        file_bytes: bytes,
        exchange_date: str,
    ) -> dict:
        """Save an individual NSE file without changing its contents."""

        if not file_bytes:
            raise NSEAPIError(
                f"NSE returned an empty file: {filename}"
            )

        date_for_folder = exchange_date.replace("-", "")

        safe_filename = self._safe_component(filename)

        relative_folder = folder_path.strip("/")

        safe_folder_parts = [
            self._safe_component(part)
            for part in relative_folder.split("/")
            if part
        ]

        destination_dir = (
            self.download_root
            / segment
            / date_for_folder
        )

        for part in safe_folder_parts:
            destination_dir = destination_dir / part

        destination_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        destination_path = destination_dir / safe_filename

        file_hash = self._sha256(file_bytes)

        # If the exact file already exists, do not download it again.
        if destination_path.exists():
            existing_hash = self._sha256(
                destination_path.read_bytes()
            )

            if existing_hash == file_hash:
                return {
                    "status": "already_downloaded",
                    "segment": segment,
                    "folder_path": folder_path,
                    "file_name": safe_filename,
                    "file_path": str(destination_path),
                    "file_size": destination_path.stat().st_size,
                    "sha256": file_hash,
                }

            # Preserve the existing file if the new content differs.
            destination_path = destination_dir / (
                f"{destination_path.stem}_"
                f"{file_hash[:8]}"
                f"{destination_path.suffix}"
            )

        if destination_path.exists():
            existing_hash = self._sha256(
                destination_path.read_bytes()
            )

            if existing_hash == file_hash:
                return {
                    "status": "already_downloaded",
                    "segment": segment,
                    "folder_path": folder_path,
                    "file_name": destination_path.name,
                    "file_path": str(destination_path),
                    "file_size": destination_path.stat().st_size,
                    "sha256": file_hash,
                }

            raise NSEAPIError(
                f"File collision detected: {destination_path.name}"
            )

        temporary_path = destination_path.with_name(
            destination_path.name + ".part"
        )

        try:
            temporary_path.write_bytes(file_bytes)
            temporary_path.replace(destination_path)

        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise

        return {
            "status": "downloaded",
            "segment": segment,
            "folder_path": folder_path,
            "file_name": destination_path.name,
            "file_path": str(destination_path),
            "file_size": destination_path.stat().st_size,
            "sha256": file_hash,
        }

    def _process_paths(
        self,
        segment: str,
        selected_paths: list[tuple[str, str]],
        exchange_date: str,
        mode: str = "folder",
    ) -> dict:
        """
        Recursively list selected folders and download matching files.

        Each selected_paths entry contains:
            (display_name, NSE folder path)

        The run and every matching file are recorded in the database.
        Files already saved by an earlier run, and unchanged on NSE, are
        skipped without downloading them again.

        "skipped" is the total of other_date + no_date + already_downloaded.
        """

        target_date = datetime.strptime(
            exchange_date,
            "%d-%m-%Y",
        ).date()

        result = {
            "segment": segment,
            "exchange_date": exchange_date,
            "selected_roots": [
                display_name
                for display_name, _ in selected_paths
            ],
            "roots_processed": 0,
            "folders_scanned": 0,
            "folders_with_files": 0,
            "total_files_seen": 0,
            "matching_files": 0,
            "downloaded": 0,
            "skipped": 0,
            "other_date": 0,
            "no_date": 0,
            "already_downloaded": 0,
            "failed": 0,
            "failed_files": 0,
            "failed_folders": [],
            "downloads": [],
            "status": "started",
        }

        run_id = self.history.start_run(
            segment=segment,
            exchange_date=exchange_date,
            mode=mode,
            roots=result["selected_roots"],
        )
        result["run_id"] = run_id

        recorded = self.history.get_recorded_files(
            segment,
            exchange_date,
        )

        try:
            with self.client_factory() as client:

                for display_name, root_path in selected_paths:
                    root_result = {
                        "root": display_name,
                        "folders_scanned": 0,
                        "status": "started",
                    }

                    pending = [(root_path, 0)]
                    visited = set()

                    print(
                        f"[NSE] Starting selected root: "
                        f"{segment}/{root_path}",
                        flush=True,
                    )

                    while pending:
                        current_path, depth = pending.pop()

                        if current_path in visited:
                            continue

                        if depth > MAX_FOLDER_DEPTH:
                            error = "Maximum folder depth exceeded."

                            result["failed"] += 1
                            result["failed_folders"].append({
                                "folder_path": current_path,
                                "root": display_name,
                                "error": error,
                            })
                            continue

                        if (
                            root_result["folders_scanned"]
                            >= MAX_FOLDERS_PER_ROOT
                        ):
                            error = (
                                "Maximum folder limit reached for root."
                            )

                            result["failed"] += 1
                            result["failed_folders"].append({
                                "folder_path": current_path,
                                "root": display_name,
                                "error": error,
                            })
                            break

                        visited.add(current_path)

                        root_result["folders_scanned"] += 1
                        result["folders_scanned"] += 1

                        print(
                            f"[NSE] Listing {segment}/{current_path}",
                            flush=True,
                        )

                        try:
                            listing = client.list_content(
                                segment=segment,
                                folder_path=self._api_path(current_path),
                            )

                            items = listing.get("data", [])

                            if not isinstance(items, list):
                                raise NSEAPIError(
                                    "Unexpected NSE folder listing format."
                                )

                        except Exception as exc:
                            result["failed"] += 1

                            result["failed_folders"].append({
                                "folder_path": current_path,
                                "root": display_name,
                                "error": str(exc),
                            })

                            print(
                                f"[NSE] Listing failed for "
                                f"{current_path}: {exc}",
                                flush=True,
                            )
                            continue

                        child_folders = []
                        files_in_folder = []

                        for item in items:
                            if not isinstance(item, dict):
                                continue

                            name = item.get("name")
                            item_type = str(
                                item.get("type", "")
                            ).lower()

                            if (
                                not isinstance(name, str)
                                or not name.strip()
                            ):
                                continue

                            name = name.strip()

                            # Prevent names from escaping the selected root.
                            if (
                                name in {".", ".."}
                                or "/" in name
                                or "\\" in name
                            ):
                                continue

                            if item_type == "folder":
                                # current_path is "" (root) or ends with "/".
                                child_path = f"{current_path}{name}/"

                                child_folders.append(child_path)

                            elif item_type == "file":
                                files_in_folder.append(item)

                        result["total_files_seen"] += len(
                            files_in_folder
                        )

                        if files_in_folder:
                            result["folders_with_files"] += 1

                        # Download matching individual files.
                        for file_item in files_in_folder:
                            filename = file_item["name"]

                            file_date = self._get_file_date(file_item)

                            if file_date is None:
                                result["skipped"] += 1
                                result["no_date"] += 1

                                result["downloads"].append({
                                    "status": "skipped",
                                    "segment": segment,
                                    "folder_path": current_path,
                                    "file_name": filename,
                                    "reason": (
                                        "Missing or unrecognised "
                                        "lastUpdated date."
                                    ),
                                })

                                print(
                                    f"[NSE] Skipping {filename}: "
                                    "lastUpdated date unavailable",
                                    flush=True,
                                )
                                continue

                            if file_date != target_date:
                                result["skipped"] += 1
                                result["other_date"] += 1
                                continue

                            result["matching_files"] += 1

                            nse_last_updated = file_item.get("lastUpdated")

                            # Already saved by an earlier run and unchanged on
                            # NSE: skip without downloading it again.
                            if self.history.is_already_downloaded(
                                recorded.get((current_path, filename)),
                                nse_last_updated,
                            ):
                                self.history.record_file(
                                    run_id=run_id,
                                    segment=segment,
                                    exchange_date=exchange_date,
                                    folder_path=current_path,
                                    file_name=filename,
                                    status="already_downloaded",
                                    nse_last_updated=nse_last_updated,
                                )

                                result["skipped"] += 1
                                result["already_downloaded"] += 1

                                result["downloads"].append({
                                    "status": "already_downloaded",
                                    "source": "history",
                                    "segment": segment,
                                    "folder_path": current_path,
                                    "file_name": filename,
                                })
                                continue

                            print(
                                f"[NSE] Downloading "
                                f"{segment}/{current_path}{filename}",
                                flush=True,
                            )

                            try:
                                file_bytes = client.download_single_file(
                                    segment=segment,
                                    folder_path=self._api_path(current_path),
                                    filename=filename,
                                )

                                saved = self._save_file(
                                    segment=segment,
                                    folder_path=current_path,
                                    filename=filename,
                                    file_bytes=file_bytes,
                                    exchange_date=exchange_date,
                                )

                                # Record before counting, so a database error
                                # is reported once, as a failed file.
                                self.history.record_file(
                                    run_id=run_id,
                                    segment=segment,
                                    exchange_date=exchange_date,
                                    folder_path=current_path,
                                    file_name=filename,
                                    status=saved["status"],
                                    nse_last_updated=nse_last_updated,
                                    local_path=saved["file_path"],
                                    file_size=saved["file_size"],
                                    sha256=saved["sha256"],
                                )

                                result["downloads"].append(saved)

                                if saved["status"] == "downloaded":
                                    result["downloaded"] += 1

                                elif saved["status"] == "already_downloaded":
                                    result["skipped"] += 1
                                    result["already_downloaded"] += 1

                            except Exception as exc:
                                result["failed"] += 1
                                result["failed_files"] += 1

                                failure = {
                                    "status": "failed",
                                    "segment": segment,
                                    "folder_path": current_path,
                                    "file_name": filename,
                                    "error": str(exc),
                                }

                                result["downloads"].append(failure)

                                self.history.record_file(
                                    run_id=run_id,
                                    segment=segment,
                                    exchange_date=exchange_date,
                                    folder_path=current_path,
                                    file_name=filename,
                                    status="failed",
                                    nse_last_updated=nse_last_updated,
                                    error_message=str(exc),
                                )

                                print(
                                    f"[NSE] Download failed for "
                                    f"{filename}: {exc}",
                                    flush=True,
                                )

                        # Always recurse into child folders.
                        # Do not filter folders by their own lastUpdated date.
                        for child_path in reversed(child_folders):
                            if child_path not in visited:
                                pending.append(
                                    (child_path, depth + 1)
                                )

                    root_result["status"] = (
                        "completed"
                        if not any(
                            failure["root"] == display_name
                            for failure in result["failed_folders"]
                        )
                        else "partial"
                    )

                    result["roots_processed"] += 1

                    result.setdefault(
                        "root_results",
                        [],
                    ).append(root_result)

                    print(
                        f"[NSE] Finished root {display_name}: "
                        f"{root_result['folders_scanned']} folders scanned",
                        flush=True,
                    )

        except Exception as exc:
            # Do not leave the run marked as 'running'.
            self.history.finish_run(
                run_id,
                status="failed",
                files_found=result["matching_files"],
                files_downloaded=result["downloaded"],
                files_skipped=result["already_downloaded"],
                files_failed=result["failed_files"],
                folders_failed=len(result["failed_folders"]),
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise

        if result["failed"] == 0:
            result["status"] = "completed"

        elif result["downloaded"] + result["skipped"] > 0:
            result["status"] = "partial"

        else:
            result["status"] = "failed"

        failed_folders = result["failed_folders"]

        self.history.finish_run(
            run_id,
            status=result["status"],
            files_found=result["matching_files"],
            files_downloaded=result["downloaded"],
            files_skipped=result["already_downloaded"],
            files_failed=result["failed_files"],
            folders_failed=len(failed_folders),
            error_message=(
                f"{len(failed_folders)} folder(s) failed: "
                + "; ".join(
                    item["folder_path"] or "/"
                    for item in failed_folders
                )
                if failed_folders else None
            ),
        )

        print(
            f"[NSE] Download run {run_id} finished: "
            f"{result['downloaded']} downloaded, "
            f"{result['already_downloaded']} already downloaded, "
            f"{result['other_date']} other dates, "
            f"{result['failed']} failed.",
            flush=True,
        )

        return result

    def download_folder(
        self,
        segment: str,
        folder_path: str,
        date: str,
    ) -> dict:
        """Recursively download matching files from one NSE folder."""

        segment = self._validate_segment(segment)
        date = self._validate_date(date)
        folder_path = self._validate_folder_path(folder_path)

        return self._process_paths(
            segment=segment,
            selected_paths=[
                (folder_path.strip("/") or "/", folder_path)
            ],
            exchange_date=date,
            mode="folder",
        )

    def download_root_folders(
        self,
        segment: str,
        root_folders: list[str],
        date: str,
    ) -> dict:
        """Recursively download matching files from selected root folders."""

        segment = self._validate_segment(segment)
        date = self._validate_date(date)

        if not isinstance(root_folders, list) or not root_folders:
            raise ValueError(
                "Select at least one root folder."
            )

        selected_roots = list(dict.fromkeys(
            self._normalise_root_name(root)
            for root in root_folders
        ))

        selected_paths = [
            (root_name, f"{root_name}/")
            for root_name in selected_roots
        ]

        return self._process_paths(
            segment=segment,
            selected_paths=selected_paths,
            exchange_date=date,
            mode="root",
        )

    def _get_cached_folders(self, segment: str) -> list:
        """Get unique folder paths from the saved SQLite catalogue."""

        files = self.catalogue_service.get_files(
            segment=segment,
            service="member",
        )

        folders = sorted({
            self._validate_folder_path(item["folder_path"])
            for item in files
            if item.get("folder_path") is not None
        })

        if not folders:
            raise ValueError(
                f"No cached folders containing files were found for "
                f"{segment}. Run a successful catalogue scan first."
            )

        return folders

    def download_segments(
        self,
        segments: list[str],
        date: str,
    ) -> dict:
        """Download matching individual files using cached folder paths."""

        date = self._validate_date(date)

        if not isinstance(segments, list) or not segments:
            raise ValueError("Select at least one segment.")

        selected_segments = list(dict.fromkeys(
            self._validate_segment(segment)
            for segment in segments
        ))

        results = []

        total_downloaded = 0
        total_skipped = 0
        total_failed = 0

        for segment in selected_segments:
            try:
                folders_to_download = self._get_cached_folders(
                    segment
                )

                selected_paths = [
                    (folder.strip("/"), folder)
                    for folder in folders_to_download
                ]

                segment_result = self._process_paths(
                    segment=segment,
                    selected_paths=selected_paths,
                    exchange_date=date,
                    mode="segments",
                )

                total_downloaded += segment_result["downloaded"]
                total_skipped += segment_result["skipped"]
                total_failed += segment_result["failed"]

                results.append(segment_result)

            except Exception as exc:
                total_failed += 1

                results.append({
                    "segment": segment,
                    "status": "failed",
                    "error": str(exc),
                    "downloaded": 0,
                    "skipped": 0,
                    "failed": 1,
                    "downloads": [],
                })

        return {
            "exchange_date": date,
            "selected_segments": selected_segments,
            "segments_processed": len(results),
            "total_downloaded": total_downloaded,
            "total_skipped": total_skipped,
            "total_failed": total_failed,
            "results": results,
        }
