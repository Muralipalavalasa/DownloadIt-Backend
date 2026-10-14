# DownloadIt Backend

This is the FastAPI backend for the DownloadIt project.

## Overview

The backend provides authenticated media analysis, PostgreSQL-backed download jobs, and a replaceable provider registry. Direct MP4 uses exact HTTPS host allowlisting. When `YTDLP_ENABLED` is active, yt-dlp is attempted for safe HTTP/HTTPS URLs and its extractor is reported only after successful analysis.

Only download content you are authorized to download and that the applicable platform permits. yt-dlp jobs require an explicit user acknowledgement. The provider accepts no cookies, credentials, custom headers, proxies, or user-supplied yt-dlp options; playlists and access-control bypasses are not supported.

## Start locally

Copy `.env.example` to `.env`, replace the JWT/database placeholders, then start PostgreSQL from the repository root:

Existing local `.env` files must include matching `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` values for Compose. Production credentials must come from deployment-managed secrets.

```bash
docker compose --env-file backend/.env up -d
```

Install dependencies and apply migrations:

```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
```

Start the API:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run backend tests with `pytest` from `backend/`. Tests use an isolated in-memory SQLite database; the application and migrations target PostgreSQL.

## Provider configuration

- Set `DOWNLOAD_ALLOWED_HOSTS` to exact hosts approved for direct MP4 downloads.
- `YTDLP_ENABLED` is a provider-wide opt-in. The example defaults it to `false`; the local environment enables generic extractor attempts for safe public HTTP(S) URLs.
- The base `requirements.txt` does not install yt-dlp. Install `requirements-ytdlp.txt` before enabling `YTDLP_ENABLED`.
- FFmpeg and ffprobe are detected on `PATH`; FFmpeg is required to merge separate video/audio streams. ffprobe validates the resulting MP4/M4A container and required stream before a job is marked complete.
- The frontend sends only controlled `best`, `1080p`, `720p`, `480p`, `360p`, and `audio` values when analysis reports them. Raw yt-dlp selectors remain server-side; unavailable qualities are rejected.
- `DOWNLOAD_MAX_BYTES` is checked during provider download and after file creation. Files are stored temporarily under private generated directories and expire according to `DOWNLOAD_FILE_EXPIRATION_MINUTES`.
- Background jobs persist status/progress in PostgreSQL. Generated files are stored in `0700` job directories with `0600` file permissions and served only through owner-checked authenticated routes.
- Provider availability can change as site extractors and platform rules change. The application does not claim support for every yt-dlp extractor or website.

To install yt-dlp in the existing virtual environment, run `pip install -r requirements-ytdlp.txt`, then set `YTDLP_ENABLED=true` in the ignored local `.env`. Run `pytest tests/test_ytdlp_provider.py` for mocked provider checks; these tests never contact media websites.

## API docs

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
