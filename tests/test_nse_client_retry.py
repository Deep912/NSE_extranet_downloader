"""NSE client retries, checked offline with httpx.MockTransport."""

import time

import httpx
import pytest

import app.services.nse_client as nse_client_module
from app.services.nse_client import NSEAPIError, NSEExtranetClient


TOKEN = "SECRET-TOKEN-123"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(nse_client_module.time, "sleep", lambda seconds: None)


def make_client(steps):
    """steps: callables(request) -> Response; the last one repeats."""

    calls = []

    def handler(request):
        calls.append(str(request.url))
        step = steps[min(len(calls) - 1, len(steps) - 1)]
        return step(request)

    client = NSEExtranetClient.__new__(NSEExtranetClient)
    client.version, client._token = "2.0", TOKEN
    client._token_obtained = time.monotonic()
    client.member_code, client.login_id = "14691", "apiuser"
    client._http = httpx.Client(
        base_url="https://nse.test/extranet-api/",
        transport=httpx.MockTransport(handler),
    )
    return client, calls


def nse_605(request):
    return httpx.Response(500, json={"responseCode": ["605"], "status": "error"})


def nse_712_as_500(request):
    return httpx.Response(500, json={"responseCode": ["712"], "responseDesc": "Folder path invalid"})


def error_page_500(request):
    return httpx.Response(500, text="<html>Internal Server Error</html>")


def file_ok(request):
    return httpx.Response(200, content=b"\x1f\x8bdata", headers={"content-type": "application/octet-stream"})


def list_ok(request):
    return httpx.Response(200, json={"responseCode": ["601"], "data": []})


def download(client):
    return client.download_single_file("CO", "/Reports", "O_SA01_P_14691_11112026_i01.lis.gz")


def test_code_605_is_retried_then_succeeds():
    client, calls = make_client([nse_605, nse_605, file_ok])

    assert download(client) == b"\x1f\x8bdata"
    assert len(calls) == 3


def test_code_605_every_time_reports_nse_code_after_three_tries():
    client, calls = make_client([nse_605])

    with pytest.raises(NSEAPIError, match="HTTP 500, code: 605") as error:
        download(client)

    assert len(calls) == 3
    assert TOKEN not in str(error.value)


def test_html_500_is_retried():
    client, calls = make_client([error_page_500, list_ok])

    assert client.list_content("CO", "/Reports")["data"] == []
    assert len(calls) == 2


def test_other_nse_errors_on_500_are_not_retried():
    client, calls = make_client([nse_712_as_500])

    with pytest.raises(NSEAPIError):
        client.list_content("CO", "/Nope")

    assert len(calls) == 1


# ---------- token expiry (NSE tokens last 60 minutes) ----------

@pytest.fixture
def fake_login(monkeypatch):
    monkeypatch.setattr(nse_client_module, "encrypt_nse_password", lambda password, key: "encrypted")


def nse_server(calls, expired_tokens):
    """Login returns NEW-TOKEN; requests with an expired token get 401 / 803."""

    def handler(request):
        if request.url.path.endswith("/login/2.0"):
            calls.append("login")
            return httpx.Response(200, json={"responseCode": ["601"], "token": "NEW-TOKEN", "status": "success"})

        token = request.headers["Authorization"].removeprefix("Bearer ")
        calls.append(f"{request.url.path.split('/')[-2]}:{token}")

        if token in expired_tokens:
            return httpx.Response(401, json={"responseCode": ["803"], "status": "error"})

        if "download" in request.url.path:
            return file_ok(request)
        return list_ok(request)

    return handler


def client_for(handler, age_seconds=0):
    client = NSEExtranetClient.__new__(NSEExtranetClient)
    client.version, client._token = "2.0", TOKEN
    client._token_obtained = time.monotonic() - age_seconds
    client.member_code, client.login_id = "14691", "apiuser"
    client._http = httpx.Client(base_url="https://nse.test/extranet-api/", transport=httpx.MockTransport(handler))
    return client


def test_expired_token_signs_in_again_and_repeats_the_request(fake_login):
    calls = []
    client = client_for(nse_server(calls, expired_tokens={TOKEN}))

    assert download(client) == file_ok(None).content
    assert calls == ["download:" + TOKEN, "login", "download:NEW-TOKEN"]

    # Later requests use the new token straight away.
    client.list_content("CO", "/Reports")
    assert calls[-1] == "content:NEW-TOKEN"


def test_token_is_renewed_before_the_hour_is_up(fake_login):
    calls = []
    client = client_for(nse_server(calls, expired_tokens=set()), age_seconds=56 * 60)

    client.list_content("CO", "/Reports")

    assert calls == ["login", "content:NEW-TOKEN"]


def test_still_rejected_after_new_login_fails_once_without_looping(fake_login):
    calls = []
    client = client_for(nse_server(calls, expired_tokens={TOKEN, "NEW-TOKEN"}))

    with pytest.raises(NSEAPIError, match="803"):
        download(client)

    assert calls.count("login") == 1
