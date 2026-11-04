
import hashlib
import re
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from io import BytesIO

from app.services.nse_client import NSEExtranetClient, NSEAPIError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOWNLOAD_ROOT = PROJECT_ROOT / "data" / "downloads"


class DownloaderService:
    """Coordinates NSE folder-level bulk downloads."""

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
    def _safe_folder_name(folder_path: str) -> str:
        name = folder_path.strip("/").replace("/", "_")
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        return name or "root"

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def download_folder(
        self,
        segment: str,
        folder_path: str,
        date: str,
    ) -> dict:

        segment = segment.upper().strip()
        date = self._validate_date(date)

        if not folder_path or "\\" in folder_path:
            raise ValueError("Invalid NSE folder path.")

        if ".." in folder_path.split("/"):
            raise ValueError("Invalid NSE folder path.")

        # NSE listing returned paths such as Reports/.
        # Preserve that convention for the download API.
        folder_path = folder_path.strip("/") + "/"

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

        # Download from NSE.
        with NSEExtranetClient() as client:
            archive_bytes = client.download_bulk_folder(
                segment=segment,
                folder_path=folder_path,
                date=date,
            )

        if not archive_bytes:
            raise NSEAPIError("NSE returned an empty archive.")

        # Validate ZIP signature.
        if not archive_bytes.startswith(b"PK\x03\x04"):
            raise NSEAPIError(
                "NSE response is not a recognised ZIP archive."
            )

        archive_hash = self._sha256(archive_bytes)
        archive_path = destination_dir / f"{base_name}.zip"

        if archive_path.exists():
            if self._sha256(archive_path.read_bytes()) == archive_hash:
                return {
                    "status": "already_downloaded",
                    "archive": str(archive_path),
                    "sha256": archive_hash,
                    "file_size": archive_path.stat().st_size,
                }

            archive_path = destination_dir / (
                f"{base_name}_{archive_hash[:8]}.zip"
            )

        # Save original archive.
        temporary_path = archive_path.with_suffix(".zip.part")

        try:
            temporary_path.write_bytes(archive_bytes)
            temporary_path.replace(archive_path)
        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise

        # Safely extract archive contents.
        extraction_dir = destination_dir / f"{base_name}_extracted"
        extraction_dir.mkdir(parents=True, exist_ok=True)

        extracted_files = []

        try:
            with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:

                for member in archive.infolist():

                    if member.is_dir():
                        continue

                    relative_path = PurePosixPath(member.filename)

                    if (
                        relative_path.is_absolute()
                        or ".." in relative_path.parts
                        or "\\" in member.filename
                    ):
                        raise NSEAPIError(
                            f"Unsafe archive entry: {member.filename}"
                        )

                    destination = extraction_dir.joinpath(
                        *relative_path.parts
                    )

                    if not destination.resolve().is_relative_to(
                        extraction_dir.resolve()
                    ):
                        raise NSEAPIError(
                            f"Unsafe archive path: {member.filename}"
                        )

                    destination.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    file_data = archive.read(member)

                    destination.write_bytes(file_data)

                    extracted_files.append({
                        "name": member.filename,
                        "size": len(file_data),
                        "sha256": self._sha256(file_data),
                        "path": str(destination),
                    })

        except zipfile.BadZipFile as exc:
            raise NSEAPIError(
                "NSE returned an invalid ZIP archive."
            ) from exc

        return {
            "status": "downloaded",
            "segment": segment,
            "folder_path": folder_path,
            "exchange_date": date,
            "archive_name": archive_path.name,
            "archive_path": str(archive_path),
            "archive_size": archive_path.stat().st_size,
            "archive_sha256": archive_hash,
            "extraction_directory": str(extraction_dir),
            "files_extracted": len(extracted_files),
            "files": extracted_files,
        }
