
import hashlib
import io
import re
import zipfile

from datetime import datetime
from pathlib import Path

from app.services.nse_client import NSEExtranetClient, NSEAPIError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOWNLOAD_ROOT = PROJECT_ROOT / "data" / "downloads"

SUPPORTED_SEGMENTS = {"CM", "FO", "CD", "CO", "SLB"}


class DownloaderService:
    """Coordinates multi-segment NSE bulk downloads."""

    def __init__(self, download_root: Path = DOWNLOAD_ROOT):
        self.download_root = Path(download_root)

    @staticmethod
    def _validate_date(date: str) -> str:
        try:
            parsed = datetime.strptime(date, "%d-%m-%Y")
        except ValueError as exc:
            raise ValueError(
                "Date must use DD-MM-YYYY format."
            ) from exc

        if parsed.strftime("%d-%m-%Y") != date:
            raise ValueError("Date must use DD-MM-YYYY format.")

        return date

    @staticmethod
    def _validate_folder_path(folder_path: str) -> str:
        if not folder_path:
            raise ValueError("Folder path cannot be empty.")

        if "\\" in folder_path:
            raise ValueError(
                "Folder path must use forward slashes."
            )

        if ".." in folder_path.split("/"):
            raise ValueError(
                "Folder path cannot contain '..'."
            )

        return folder_path.strip("/") + "/"

    @staticmethod
    def _safe_folder_name(folder_path: str) -> str:
        name = folder_path.strip("/").replace("/", "_")
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        return name or "root"

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _save_archive(
        self,
        segment: str,
        folder_path: str,
        date: str,
        archive_bytes: bytes,
    ) -> dict:
        """Validate and save the original NSE ZIP without extraction."""

        if not archive_bytes:
            raise NSEAPIError("NSE returned an empty archive.")

        if not zipfile.is_zipfile(io.BytesIO(archive_bytes)):
            raise NSEAPIError(
                "NSE response is not a valid ZIP archive."
            )

        date_for_filename = date.replace("-", "")
        folder_name = self._safe_folder_name(folder_path)

        destination_dir = (
            self.download_root / segment / date_for_filename
        )
        destination_dir.mkdir(parents=True, exist_ok=True)

        folder_hash = self._sha256(
            folder_path.encode("utf-8")
        )[:10]

        base_name = (
            f"{segment}_{date_for_filename}_"
            f"{folder_name}_{folder_hash}"
        )

        archive_hash = self._sha256(archive_bytes)

        archive_path = destination_dir / f"{base_name}.zip"

        # Skip an identical archive already downloaded.
        if archive_path.exists():
            existing_hash = self._sha256(
                archive_path.read_bytes()
            )

            if existing_hash == archive_hash:
                return {
                    "status": "skipped",
                    "segment": segment,
                    "folder_path": folder_path,
                    "file_name": archive_path.name,
                    "file_path": str(archive_path),
                    "file_size": archive_path.stat().st_size,
                    "sha256": archive_hash,
                }

            # Preserve both versions if the content changed.
            archive_path = destination_dir / (
                f"{base_name}_{archive_hash[:8]}.zip"
            )

        if archive_path.exists():
            if self._sha256(archive_path.read_bytes()) == archive_hash:
                return {
                    "status": "skipped",
                    "segment": segment,
                    "folder_path": folder_path,
                    "file_name": archive_path.name,
                    "file_path": str(archive_path),
                    "file_size": archive_path.stat().st_size,
                    "sha256": archive_hash,
                }

            raise NSEAPIError(
                "Archive filename collision detected."
            )

        temporary_path = archive_path.with_suffix(".zip.part")

        try:
            temporary_path.write_bytes(archive_bytes)
            temporary_path.replace(archive_path)
        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise

        return {
            "status": "downloaded",
            "segment": segment,
            "folder_path": folder_path,
            "file_name": archive_path.name,
            "file_path": str(archive_path),
            "file_size": archive_path.stat().st_size,
            "sha256": archive_hash,
        }

    def download_folder(
        self,
        segment: str,
        folder_path: str,
        date: str,
    ) -> dict:
        """Download one NSE folder as a ZIP archive."""

        segment = segment.upper().strip()

        if segment not in SUPPORTED_SEGMENTS:
            raise ValueError("Unsupported segment.")

        date = self._validate_date(date)
        folder_path = self._validate_folder_path(folder_path)

        with NSEExtranetClient() as client:
            archive_bytes = client.download_bulk_folder(
                segment=segment,
                folder_path=folder_path,
                date=date,
            )

        return self._save_archive(
            segment=segment,
            folder_path=folder_path,
            date=date,
            archive_bytes=archive_bytes,
        )

    def download_segments(
        self,
        segments: list[str],
        date: str,
    ) -> dict:
        """
        Scan and download all accessible folders containing files
        across the selected NSE segments.
        """

        date = self._validate_date(date)

        # Normalise and validate the selected segments.
        selected_segments = list(
            dict.fromkeys(
                str(segment).upper().strip()
                for segment in segments
            )
        )

        if not selected_segments:
            raise ValueError("Select at least one segment.")

        invalid_segments = [
            segment
            for segment in selected_segments
            if segment not in SUPPORTED_SEGMENTS
        ]

        if invalid_segments:
            raise ValueError(
                f"Unsupported segments: {', '.join(invalid_segments)}"
            )

        results = []

        total_downloaded = 0
        total_skipped = 0
        total_failed = 0

        # Process segments sequentially.
        for segment in selected_segments:

            segment_result = {
                "segment": segment,
                "status": "started",
                "folders_scanned": 0,
                "folders_found": 0,
                "downloaded": 0,
                "skipped": 0,
                "failed": 0,
                "scan_failures": [],
                "downloads": [],
            }

            try:
                # One NSE login/session for this segment.
                with NSEExtranetClient() as client:

                    print(f"[NSE] Scanning segment: {segment}")

                    scan = client.list_all_content(
                        segment=segment,
                        root_path="/",
                    )

                    segment_result["folders_scanned"] = (
                        scan["folders_scanned"]
                    )

                    segment_result["scan_failures"] = (
                        scan["failed_folders"]
                    )

                    # Find unique folders containing files.
                    folders_to_download = sorted({
                        self._validate_folder_path(
                            item["folderPath"]
                        )
                        for item in scan["files"]
                        if item.get("folderPath")
                    })

                    segment_result["folders_found"] = len(
                        folders_to_download
                    )

                    # Download one folder at a time.
                    for folder_path in folders_to_download:

                        print(
                            f"[NSE] Downloading "
                            f"{segment}/{folder_path}"
                        )

                        try:
                            archive_bytes = client.download_bulk_folder(
                                segment=segment,
                                folder_path=folder_path,
                                date=date,
                            )

                            result = self._save_archive(
                                segment=segment,
                                folder_path=folder_path,
                                date=date,
                                archive_bytes=archive_bytes,
                            )

                            segment_result["downloads"].append(result)

                            if result["status"] == "downloaded":
                                segment_result["downloaded"] += 1
                                total_downloaded += 1

                            elif result["status"] == "skipped":
                                segment_result["skipped"] += 1
                                total_skipped += 1

                        except Exception as exc:
                            segment_result["failed"] += 1
                            total_failed += 1

                            segment_result["downloads"].append({
                                "status": "failed",
                                "folder_path": folder_path,
                                "error": str(exc),
                            })

                # Determine whether the segment completed fully.
                if (
                    segment_result["failed"] == 0
                    and not segment_result["scan_failures"]
                ):
                    segment_result["status"] = "completed"
                else:
                    segment_result["status"] = "partial"

            except Exception as exc:
                segment_result["status"] = "failed"
                segment_result["error"] = str(exc)
                total_failed += 1

            results.append(segment_result)

        return {
            "exchange_date": date,
            "selected_segments": selected_segments,
            "segments_processed": len(results),
            "total_downloaded": total_downloaded,
            "total_skipped": total_skipped,
            "total_failed": total_failed,
            "results": results,
        }
