# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project provides two interfaces for downloading TV recordings from a **Fetch TV** set-top box at `10.0.0.14` over its DLNA/UPnP ContentDirectory service. Recordings are streamed as MPEG-TS and remuxed losslessly to `.mkv` via ffmpeg (no re-encoding).

1. **CLI script** — `fetch_downloader.py`: standalone Python script, no external dependencies beyond ffmpeg on PATH.
2. **Web service** — a FastAPI app under `app/` served via Docker.

## Running via Docker

Copy `.env` and set your values, then:

```bash
# Pull and run published image
docker compose up -d

# Build locally (edit docker-compose.yml to use build: . instead of image:)
docker compose up -d --build
```

The web service exposes port `8000`. Output files are written to `OUTPUT_DIR` (default `./recordings`) mounted at `/output` inside the container.

On a Linux Docker host (no Docker Desktop), use `network_mode: host` in `docker-compose.yml` to reach the LAN device at `10.0.0.14`.

## Architecture

### Web Service (`app/`)

FastAPI app at `app/main.py` (uvicorn entry point `main:app`). Single-page HTML/JS frontend at `app/static/index.html` — no build step.

- DLNA browsing via `GET /api/browse/{object_id}`
- Download queue managed in-memory; progress delivered via SSE (`GET /api/progress`) or polling (`GET /api/queue`) depending on `PROGRESS_MODE`
- Three quality presets (`hd`/`hq`/`sd`) defined in `QUALITY_PRESETS` dict — each specifies ffmpeg args and output filename suffix
- All frontend `fetch()` calls use relative URLs (no leading `/`) so the app works behind a reverse proxy at any sub-path
- Config exposed via `GET /api/config` and applied by the frontend on load — includes `app_name`, `root_path`, `progress_mode`, and `poll_interval`

#### Applying configuration changes

| Change type | Command needed |
|---|---|
| Code change (`main.py`, `index.html`) | `docker compose up -d --build` |
| Environment variable change (`.env`) | `docker compose up -d` |

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `FETCH_HOST` | `10.0.0.14` | IP of the Fetch TV box |
| `FETCH_PORT` | `49152` | DLNA port |
| `OUTPUT_DIR` | `./recordings` | Host path for downloaded files |
| `FFMPEG_PATH` | (PATH lookup) | Override ffmpeg binary location |
| `APP_NAME` | `Fetch TV Downloader` | Name shown in browser tab and page header |
| `ROOT_PATH` | _(empty)_ | Sub-path prefix for reverse proxy deployments (e.g. `/fetchtv`) |
| `PROGRESS_MODE` | `sse` | Progress update mode: `sse` or `poll` (use `poll` if reverse proxy buffers SSE) |
| `POLL_INTERVAL` | `3` | Polling interval in seconds when `PROGRESS_MODE=poll` (minimum 1) |
