# NSE Extranet Downloader

Local-first Python/FastAPI project for segment-wise NSE Extranet file downloads.

## Current status — v0.1.0

- FastAPI application and dashboard
- SQLite download-history schema
- Segment list
- NSE AES-256-ECB/PKCS7 password-encryption helper based on the supplied NSE FAQ
- Docker and GitHub Actions starter configuration
- Live NSE requests intentionally disabled pending the complete API specification

## Run locally (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

## Run tests

```powershell
pytest -q
```

## API docs

Open http://127.0.0.1:8000/docs

## Important

Do not add real credentials to source control. Keep them in `.env`, which is ignored by Git.
Do not use production credentials during initial development. Validate the integration in NSE UAT.

The supplied FAQ is not the complete API specification. Confirm endpoint versions, exact
parameter casing, request/response formats, folder traversal behaviour and file response
handling against the official technical specification before enabling downloads.
