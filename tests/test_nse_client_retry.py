"""NSE client retries, checked offline with httpx.MockTransport."""

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
