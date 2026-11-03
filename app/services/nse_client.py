from urllib.parse import urljoin

import httpx

from app.config import settings
from app.services.crypto import encrypt_nse_password


class NSEAPIError(RuntimeError):
    """An error returned by the NSE API or its HTTP layer."""


class NSEExtranetClient:
    """Small synchronous client for NSE Extranet API v2.0."""

    SUCCESS_CODE = "601"

    def __init__(self):
        if not settings.nse_credentials_configured:
            raise NSEAPIError("NSE credentials are incomplete. Check the local .env file.")
        self.base_url = settings.nse_api_base_url
        self.version = settings.nse_api_version
        self.member_code = settings.nse_member_code
        self.login_id = settings.nse_login_id
        self._token: str | None = None
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=settings.nse_timeout_seconds,
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )

    @staticmethod
    def _response_json(response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise NSEAPIError(f"NSE returned a non-JSON response (HTTP {response.status_code}).") from exc
        if not isinstance(payload, dict):
            raise NSEAPIError("NSE returned an unexpected response format.")
        return payload

    @staticmethod
    def _codes(payload: dict) -> list[str]:
        value = payload.get("responseCode", [])
        if isinstance(value, (str, int)):
            value = [value]
        return [str(code) for code in value] if isinstance(value, list) else []

    def _check_api_success(self, payload: dict, operation: str) -> None:
        codes = self._codes(payload)
        if self.SUCCESS_CODE not in codes:
            description = payload.get("responseDesc") or payload.get("reponseDesc") or payload.get("status") or "No description supplied"
            code_text = ", ".join(codes) if codes else "missing responseCode"
            raise NSEAPIError(f"NSE {operation} failed (code: {code_text}): {description}")

    def login(self) -> str:
        encrypted_password = encrypt_nse_password(settings.nse_password, settings.nse_secret_key)
        body = {
            "memberCode": self.member_code,
            "loginId": self.login_id,
            "password": encrypted_password,
        }
        try:
            response = self._http.post(f"login/{self.version}", json=body)
        except httpx.HTTPError as exc:
            raise NSEAPIError(f"Could not reach NSE LIVE API: {type(exc).__name__}") from exc

        payload = self._response_json(response)
        if response.is_error:
            raise NSEAPIError(f"NSE login HTTP error {response.status_code}: {payload.get('status', 'Request rejected')}")
        self._check_api_success(payload, "login")
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise NSEAPIError("NSE reported successful login but did not return a token.")
        self._token = token
        return token

    def list_content(self, segment: str, folder_path: str = "/", common: bool = False) -> dict:
        if not self._token:
            self.login()
        endpoint = f"{'common' if common else 'member'}/content/{self.version}"
        try:
            response = self._http.get(
                endpoint,
                params={"segment": segment, "folderPath": folder_path},
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.HTTPError as exc:
            raise NSEAPIError(f"Could not retrieve NSE folder listing: {type(exc).__name__}") from exc

        payload = self._response_json(response)
        if response.is_error:
            raise NSEAPIError(f"NSE folder listing HTTP error {response.status_code}.")
        self._check_api_success(payload, "folder listing")
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise NSEAPIError("NSE folder listing returned an unexpected data field.")
        return payload

    def close(self) -> None:
        if self._token:
            try:
                response = self._http.post(
                    f"logout/{self.version}",
                    json={"memberCode": self.member_code, "loginId": self.login_id},
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                # Do not mask the original operation if logout fails.
                if response.status_code >= 500:
                    pass
            except httpx.HTTPError:
                pass
            finally:
                self._token = None
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
