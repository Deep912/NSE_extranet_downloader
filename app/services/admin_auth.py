
import hmac
import secrets
import threading
import time


class AdminAuth:
    """
    One admin, identified by the ADMIN_PASSWORD setting.

    A successful login returns a random session token (kept in memory,
    so restarting the app logs the admin out). With no password set,
    there is no admin and protected actions are open to everyone.
    """

    COOKIE_NAME = "nse_admin"
    SESSION_SECONDS = 12 * 60 * 60

    def __init__(self, password: str, clock=time.time):
        self._password = password or ""
        self._clock = clock
        self._sessions: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._password)

    def login(self, password: str) -> str | None:
        """Return a session token for the right password, else None."""

        if not self.configured:
            return None

        if not hmac.compare_digest(
            str(password or "").encode("utf-8"),
            self._password.encode("utf-8"),
        ):
            return None

        token = secrets.token_urlsafe(32)

        with self._lock:
            self._sessions[token] = self._clock() + self.SESSION_SECONDS

        return token

    def is_admin(self, token: str | None) -> bool:
        if not token:
            return False

        with self._lock:
            expires = self._sessions.get(token)

            if expires is None:
                return False

            if expires < self._clock():
                del self._sessions[token]
                return False

            return True

    def logout(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

    def allowed(self, token: str | None) -> bool:
        """May this request change schedules / folder lists?"""

        return not self.configured or self.is_admin(token)
