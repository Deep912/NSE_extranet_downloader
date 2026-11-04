
import posixpath

import httpx

from app.config import settings
from app.services.crypto import encrypt_nse_password


class NSEAPIError(RuntimeError):
    """An error returned by the NSE API or its HTTP layer."""


class NSEExtranetClient:
    """Synchronous client for NSE Extranet API v2.0."""

    SUCCESS_CODE = "601"

    def __init__(self):
        if not settings.nse_credentials_configured:
            raise NSEAPIError(
                "NSE credentials are incomplete. Check the local .env file."
            )

        self.base_url = settings.nse_api_base_url
        self.version = settings.nse_api_version
        self.member_code = settings.nse_member_code
        self.login_id = settings.nse_login_id

        self._token: str | None = None

        self._http = httpx.Client(
    base_url=self.base_url,
    timeout=httpx.Timeout(
    connect=20.0,
    read=180.0,
    write=30.0,
    pool=20.0,
    ),
    headers={"Accept": "application/json"},
    follow_redirects=False,
)

    @staticmethod
    def _response_json(response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise NSEAPIError(
                f"NSE returned a non-JSON response "
                f"(HTTP {response.status_code})."
            ) from exc

        if not isinstance(payload, dict):
            raise NSEAPIError("NSE returned an unexpected response format.")

        return payload

    @staticmethod
    def _codes(payload: dict) -> list[str]:
        value = payload.get("responseCode", [])

        if isinstance(value, (str, int)):
            value = [value]

        if isinstance(value, list):
            return [str(code) for code in value]

        return []

    def _check_api_success(self, payload: dict, operation: str) -> None:
        codes = self._codes(payload)

        if self.SUCCESS_CODE not in codes:
            description = (
                payload.get("responseDesc")
                or payload.get("reponseDesc")
                or payload.get("status")
                or "No description supplied"
            )

            code_text = (
                ", ".join(codes)
                if codes
                else "missing responseCode"
            )

            raise NSEAPIError(
                f"NSE {operation} failed "
                f"(code: {code_text}): {description}"
            )

    def login(self) -> str:
        """Authenticate and obtain an NSE API token."""

        encrypted_password = encrypt_nse_password(
            settings.nse_password,
            settings.nse_secret_key,
        )

        body = {
            "memberCode": self.member_code,
            "loginId": self.login_id,
            "password": encrypted_password,
        }

        try:
            response = self._http.post(
                f"login/{self.version}",
                json=body,
            )
        except httpx.HTTPError as exc:
            raise NSEAPIError(
                f"Could not reach NSE LIVE API: {type(exc).__name__}"
            ) from exc

        payload = self._response_json(response)

        if response.is_error:
            raise NSEAPIError(
                f"NSE login HTTP error {response.status_code}: "
                f"{payload.get('status', 'Request rejected')}"
            )

        self._check_api_success(payload, "login")

        token = payload.get("token")

        if not isinstance(token, str) or not token:
            raise NSEAPIError(
                "NSE reported successful login but did not return a token."
            )

        self._token = token
        return token

    def _ensure_authenticated(self) -> None:
        """Login only when this client does not already have a token."""

        if not self._token:
            self.login()

    def list_content(
        self,
        segment: str,
        folder_path: str = "/",
    ) -> dict:
        """Retrieve one level of folder contents from NSE."""

        self._ensure_authenticated()

        try:
            response = self._http.get(
                f"member/content/{self.version}",
                params={
                    "segment": segment,
                    "folderPath": folder_path,
                },
                headers={
                    "Authorization": f"Bearer {self._token}",
                },
            )
        except httpx.TimeoutException as exc:
            raise NSEAPIError(
                f"NSE timed out while listing folder: {folder_path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise NSEAPIError(
                f"NSE folder listing request failed for {folder_path}: "
                f"{type(exc).__name__}"
            ) from exc

        payload = self._response_json(response)

        if response.is_error:
            raise NSEAPIError(
                f"NSE folder listing HTTP error {response.status_code}: "
                f"{payload.get('responseDesc', payload.get('status', 'Request rejected'))}"
            )

        self._check_api_success(payload, "folder listing")

        return payload

    
    def download_single_file(
        self,
        segment: str,
        folder_path: str,
        filename: str,
    ) -> bytes:
        self._ensure_authenticated()

        response = self._http.get(
            f"member/file/download/{self.version}",
            params={
                "segment": segment,
                "folderPath": folder_path,
                "filename": filename,
            },
            headers={
                "Authorization": f"Bearer {self._token}",
            },
        )

        content_type = response.headers.get("content-type", "").lower()

        if response.is_error or "application/json" in content_type:
            try:
                payload = self._response_json(response)
            except Exception:
                payload = {}

            raise NSEAPIError(
                f"Single-file download failed: "
                f"HTTP {response.status_code}, "
                f"response={payload}"
            )

        return response.content

    
    def download_bulk_folder(
        self,
        segment: str,
        folder_path: str,
        date: str,
    ) -> bytes:
        """
        Download all files for a specified date from one NSE folder.

        NSE documents multiple-file responses as GZIP.
        Returns the original response bytes without modifying them.
        """

        from datetime import datetime

        # Validate NSE's required date format.
        try:
            datetime.strptime(date, "%d-%m-%Y")
        except ValueError as exc:
            raise NSEAPIError(
                "Invalid date. Expected format: DD-MM-YYYY."
            ) from exc

        if (
            not folder_path
            or "\\" in folder_path
            or ".." in folder_path.split("/")
        ):
            raise NSEAPIError("Invalid NSE folder path.")

        # NSE LIVE bulk API accepts paths such as Reports/
        folder_path = folder_path.strip("/") + "/"

        self._ensure_authenticated()

        try:
            response = self._http.get(
                f"member/file/download/{self.version}",
                params={
                    "segment": segment,
                    "folderPath": folder_path,
                    "date": date,
                },
                headers={
                    "Authorization": f"Bearer {self._token}",
                },
            )

        except httpx.TimeoutException as exc:
            raise NSEAPIError(
                f"NSE timed out downloading folder: {folder_path}"
            ) from exc

        except httpx.HTTPError as exc:
            raise NSEAPIError(
                f"NSE bulk download request failed: "
                f"{type(exc).__name__}"
            ) from exc

        # NSE may return a JSON error response instead of file data.
        content_type = response.headers.get("content-type", "").lower()
        body = response.content

        if response.is_error or "application/json" in content_type:
            try:
                payload = self._response_json(response)
                codes = self._codes(payload)
                description = (
                    payload.get("responseDesc")
                    or payload.get("reponseDesc")
                    or payload.get("status")
                    or "Unknown NSE error"
                )

                raise NSEAPIError(
                    f"NSE bulk download failed "
                    f"(HTTP {response.status_code}, "
                    f"code: {', '.join(codes) or 'unknown'}): "
                    f"{description}"
                )

            except NSEAPIError:
                raise

        if not body:
            raise NSEAPIError(
                f"NSE returned an empty download for {folder_path}."
            )

        return body

    
    
    def test_single_file_download(
        self,
        segment: str,
        folder_path: str,
        filename: str,
    ):
        self._ensure_authenticated()

        response = self._http.get(
            f"member/file/download/{self.version}",
            params={
                "segment": segment,
                "folderPath": folder_path,
                "filename": filename,
            },
            headers={
                "Authorization": f"Bearer {self._token}",
            },
        )

        print("HTTP status:", response.status_code)
        print("Content-Type:", response.headers.get("content-type"))
        print("Content-Length:", response.headers.get("content-length"))
        print("Response preview:", response.content[:300])

        return response

    
    def list_all_content(
        self,
        segment: str,
        root_path: str = "/",
        max_folders: int = 1000,
    ) -> dict:
        """Recursively scan folders and display progress."""

        pending = [(root_path, 0)]
        visited = set()
        all_items = []
        failed_folders = []
        folders_scanned = 0

        print(f"[NSE] Starting recursive scan: {segment}", flush=True)

        while pending:
            current_path, depth = pending.pop()

            if current_path in visited:
                continue

            if folders_scanned >= max_folders:
                print(
                    f"[NSE] Maximum folder limit reached: {max_folders}",
                    flush=True,
                )
                break

            if depth > 20:
                failed_folders.append({
                    "folder": current_path,
                    "error": "Maximum folder depth exceeded",
                })
                continue

            visited.add(current_path)
            folders_scanned += 1

            print(
                f"[NSE] Scanning {current_path} "
                f"({folders_scanned}/{max_folders})",
                flush=True,
            )

            try:
                result = self.list_content(
                    segment=segment,
                    folder_path=current_path,
                )

                items = result.get("data", [])

                print(
                    f"[NSE] Completed {current_path} "
                    f"— {len(items)} items found",
                    flush=True,
                )

            except NSEAPIError as exc:
                print(
                    f"[NSE] ERROR in {current_path}: {exc}",
                    flush=True,
                )

                failed_folders.append({
                    "folder": current_path,
                    "error": str(exc),
                })
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue

                name = item.get("name")
                item_type = str(item.get("type", "")).lower()

                if not isinstance(name, str) or not name.strip():
                    continue

                item_path = posixpath.normpath(
                    posixpath.join(current_path, name)
                )

                record = dict(item)
                record["folderPath"] = current_path
                record["fullPath"] = item_path

                all_items.append(record)

                if item_type == "folder" and item_path not in visited:
                    pending.append((item_path, depth + 1))

        files = [
            item for item in all_items
            if str(item.get("type", "")).lower() == "file"
        ]

        folders = [
            item for item in all_items
            if str(item.get("type", "")).lower() == "folder"
        ]

        scan_complete = (
            len(failed_folders) == 0
            and len(pending) == 0
            and folders_scanned < max_folders
        )

        print(
            f"[NSE] Scan finished: {folders_scanned} folders, "
            f"{len(files)} files, {len(failed_folders)} errors",
            flush=True,
        )

        return {
            "segment": segment,
            "root_path": root_path,
            "folders_scanned": folders_scanned,
            "total_items": len(all_items),
            "files": files,
            "folders": folders,
            "failed_folders": failed_folders,
            "scan_complete": scan_complete,
        }

    def close(self) -> None:
        """Logout and close the HTTP session."""

        if self._token:
            try:
                self._http.post(
                    f"logout/{self.version}",
                    json={
                        "memberCode": self.member_code,
                        "loginId": self.login_id,
                    },
                    headers={
                        "Authorization": f"Bearer {self._token}",
                    },
                )
            except httpx.HTTPError:
                pass
            finally:
                self._token = None

        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
