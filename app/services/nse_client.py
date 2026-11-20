
import posixpath
import time
import httpx

from app.config import settings
from app.services.app_log import log
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
            raise NSEAPIError(
                "NSE returned an unexpected response format."
            )

        return payload

    @staticmethod
    def _codes(payload: dict) -> list[str]:
        value = payload.get("responseCode", [])

        if isinstance(value, (str, int)):
            value = [value]

        if isinstance(value, list):
            return [str(code) for code in value]

        return []

    def _check_api_success(
        self,
        payload: dict,
        operation: str,
    ) -> None:
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
        log.info("Signed in to NSE %s.", settings.nse_environment)
        return token

    def _ensure_authenticated(self) -> None:
        """Login only when this client does not already have a token."""

        if not self._token:
            self.login()

    def _auth_headers(self) -> dict:
        """Return headers for authenticated NSE API requests."""

        self._ensure_authenticated()

        return {
            "Authorization": f"Bearer {self._token}",
        }

    @staticmethod
    def _validate_folder_path(folder_path: str) -> str:
        """Validate and normalise an NSE folder path."""

        if (
            not isinstance(folder_path, str)
            or not folder_path.strip()
            or "\\" in folder_path
            or ".." in folder_path.split("/")
        ):
            raise NSEAPIError("Invalid NSE folder path.")

        normalised = "/" + folder_path.strip("/")

        if normalised == "":
            normalised = "/"

        return normalised

    @staticmethod
    def _validate_filename(filename: str) -> str:
        """Validate a filename before sending it to NSE."""

        if (
            not isinstance(filename, str)
            or not filename.strip()
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
        ):
            raise NSEAPIError("Invalid NSE filename.")

        return filename

    def _download_error(
        self,
        response: httpx.Response,
        operation: str,
    ) -> NSEAPIError:
        """Convert an NSE JSON or HTTP error into a readable exception."""

        try:
            payload = self._response_json(response)
            codes = self._codes(payload)

            description = (
                payload.get("responseDesc")
                or payload.get("reponseDesc")
                or payload.get("status")
                or "Unknown NSE error"
            )

            return NSEAPIError(
                f"{operation} failed "
                f"(HTTP {response.status_code}, "
                f"code: {', '.join(codes) or 'unknown'}): "
                f"{description}"
            )

        except NSEAPIError as exc:
            return NSEAPIError(
                f"{operation} failed "
                f"(HTTP {response.status_code}): {exc}"
            )

    # Transient failures worth retrying: timeouts, dropped connections,
    # gateway errors and NSE's "technical error" (HTTP 500, code 605).
    # Other NSE API errors (704, 706, 712, 714, ...) and HTTP errors are
    # returned to the caller without retrying.
    RETRY_DELAYS_SECONDS = (2, 5)
    RETRYABLE_STATUS_CODES = {502, 503, 504}
    RETRYABLE_NSE_CODES = {"605"}

    def _transient_reason(self, response: httpx.Response) -> str | None:
        """Why this response is worth retrying, or None if it is not."""

        status = response.status_code

        if status in self.RETRYABLE_STATUS_CODES:
            return f"HTTP {status}"

        if status == 500:
            try:
                payload = response.json()
            except ValueError:
                return "HTTP 500"  # no NSE code: a server error page

            codes = self._codes(payload) if isinstance(payload, dict) else []

            if not codes:
                return "HTTP 500"

            retryable = [code for code in codes if code in self.RETRYABLE_NSE_CODES]

            if retryable:
                return f"HTTP 500 (code {retryable[0]})"

        return None

    def _get_with_retry(
        self,
        endpoint: str,
        params: dict,
        headers: dict,
        description: str,
    ) -> httpx.Response:
        """
        Send a GET request, retrying transient failures with a delay.

        description is used in log and error messages, e.g.
        "folder listing /Reports". It must not contain secrets.
        """

        attempts = len(self.RETRY_DELAYS_SECONDS) + 1

        for attempt in range(1, attempts + 1):
            try:
                response = self._http.get(
                    endpoint,
                    params=params,
                    headers=headers,
                )

            except httpx.TransportError as exc:
                # Name the phase (ConnectTimeout, ReadTimeout, ReadError...).
                reason = type(exc).__name__

                if attempt == attempts:
                    timed_out = isinstance(exc, httpx.TimeoutException)

                    raise NSEAPIError(
                        f"NSE {'timed out' if timed_out else 'request failed'} "
                        f"for {description} after {attempts} attempts "
                        f"({reason})"
                    ) from exc

            except httpx.HTTPError as exc:
                # Not transient (e.g. an invalid request); do not retry.
                raise NSEAPIError(
                    f"NSE request failed for {description}: "
                    f"{type(exc).__name__}"
                ) from exc

            else:
                reason = self._transient_reason(response)

                if reason is None:
                    return response

                if attempt == attempts:
                    # Let the caller report NSE's own error code.
                    if response.status_code == 500:
                        return response

                    raise NSEAPIError(
                        f"NSE returned {reason} for {description} "
                        f"after {attempts} attempts"
                    )

            delay = self.RETRY_DELAYS_SECONDS[attempt - 1]

            log.warning(
                "NSE %s for %s (attempt %s/%s). Retrying in %ss.",
                reason, description, attempt, attempts, delay,
            )

            time.sleep(delay)

        raise AssertionError("unreachable")

    def list_content(
        self,
        segment: str,
        folder_path: str = "/",
    ) -> dict:
        """
        List files and folders inside an NSE Extranet directory.

        Timeouts, dropped connections and HTTP 502/503/504 are retried.
        """

        folder_path = self._validate_folder_path(folder_path)
        headers = self._auth_headers()

        endpoint = f"member/content/{self.version}"

        params = {
            "segment": segment,
            "folderPath": folder_path,
        }

        response = self._get_with_retry(
            endpoint,
            params,
            headers,
            f"folder listing {folder_path}",
        )

        payload = self._response_json(response)

        if response.is_error:
            raise NSEAPIError(
                f"NSE folder listing failed for {folder_path}: "
                f"HTTP {response.status_code} - {payload}"
            )

        self._check_api_success(payload, "folder listing")

        return payload

    def download_single_file(
        self,
        segment: str,
        folder_path: str,
        filename: str,
    ) -> bytes:
        """
        Download one individual file from NSE Extranet.

        The API requires:
        - segment
        - folderPath
        - filename

        Returns the original downloaded file bytes.
        """

        folder_path = self._validate_folder_path(folder_path)
        filename = self._validate_filename(filename)

        headers = self._auth_headers()

        endpoint = f"member/file/download/{self.version}"

        params = {
            "segment": segment,
            "folderPath": folder_path,
            "filename": filename,
        }

        response = self._get_with_retry(
            endpoint,
            params,
            headers,
            f"file download {folder_path.rstrip('/')}/{filename}",
        )

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        if response.is_error or "application/json" in content_type:
            raise self._download_error(
                response,
                f"File download ({filename})",
            )

        if not response.content:
            raise NSEAPIError(
                f"NSE returned an empty file: {filename}"
            )

        return response.content

    def test_single_file_download(
        self,
        segment: str,
        folder_path: str,
        filename: str,
    ):
        """Diagnostic request for one individual file."""

        folder_path = self._validate_folder_path(folder_path)
        filename = self._validate_filename(filename)

        response = self._http.get(
            f"member/file/download/{self.version}",
            params={
                "segment": segment,
                "folderPath": folder_path,
                "filename": filename,
            },
            headers=self._auth_headers(),
        )

        log.debug(
            "Test download: HTTP %s, %s, %s bytes",
            response.status_code,
            response.headers.get("content-type"),
            response.headers.get("content-length"),
        )

        return response

    def list_all_content(
        self,
        segment: str,
        root_path: str = "/",
        max_folders: int = 1000,
    ) -> dict:
        """Recursively scan folders and display progress."""

        root_path = self._validate_folder_path(root_path)

        pending = [(root_path, 0)]
        visited = set()
        all_items = []
        failed_folders = []
        folders_scanned = 0

        log.info("Scanning all folders of %s.", segment)

        while pending:
            current_path, depth = pending.pop()

            if current_path in visited:
                continue

            if folders_scanned >= max_folders:
                log.warning("Scan stopped at the folder limit (%s).", max_folders)
                break

            if depth > 20:
                failed_folders.append({
                    "folder": current_path,
                    "error": "Maximum folder depth exceeded",
                })
                continue

            visited.add(current_path)
            folders_scanned += 1

            log.debug("Scanning %s (%s/%s)", current_path, folders_scanned, max_folders)

            try:
                result = self.list_content(
                    segment=segment,
                    folder_path=current_path,
                )

                items = result.get("data", [])

                if not isinstance(items, list):
                    raise NSEAPIError(
                        f"Unexpected folder listing data for {current_path}"
                    )

                log.debug("Scanned %s: %s item(s)", current_path, len(items))

            except NSEAPIError as exc:
                log.warning("Scan could not list %s: %s", current_path, exc)

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

                if name in {".", ".."} or "/" in name or "\\" in name:
                    continue

                item_path = posixpath.normpath(
                    posixpath.join(current_path, name)
                )

                record = dict(item)
                record["folderPath"] = current_path
                record["fullPath"] = item_path

                all_items.append(record)

                if (
                    item_type == "folder"
                    and item_path not in visited
                ):
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

        log.info(
            "Scan finished: %s folder(s), %s file(s), %s error(s).",
            folders_scanned, len(files), len(failed_folders),
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

                log.info("Signed out of NSE.")

            except httpx.HTTPError as exc:
                log.warning("NSE sign-out failed: %s", type(exc).__name__)

            finally:
                self._token = None

        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
