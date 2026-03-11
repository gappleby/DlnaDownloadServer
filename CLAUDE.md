# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project provides two interfaces for downloading TV recordings from a **Fetch TV** set-top box at `10.0.0.14` over its DLNA/UPnP ContentDirectory service. Recordings are streamed as MPEG-TS and remuxed losslessly to `.mkv` via ffmpeg (no re-encoding).

1. **CLI script** — `fetch_downloader.py`: standalone Python script, no external dependencies beyond ffmpeg on PATH.
2. **Web service** (in progress) — a FastAPI app under `app/` served via Docker; the `app/` directory does not yet exist.

## Running the CLI

```bash
# List all recorded shows
python fetch_downloader.py list

# Download all recordings
python fetch_downloader.py download --dest ./recordings

# Download a specific show container by ID
python fetch_downloader.py download --show-id <ID> --dest ./recordings

# Preview without downloading
python fetch_downloader.py download --dry-run
```

ffmpeg must be on PATH, or set `FFMPEG_PATH` env var:
- Windows: `winget install ffmpeg` or `choco install ffmpeg`

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

### CLI (`fetch_downloader.py`)

- `browse(object_id)` — sends a UPnP SOAP `Browse` request to `http://10.0.0.14:49152/web/cds_control` and returns the parsed DIDL-Lite XML.
- `get_items(object_id)` — recursively walks containers, collecting video item metadata (title, show name, URL, size, duration).
- `download_item(item, dest_dir, ffmpeg)` — streams TS from the item URL via HTTP, pipes directly into `ffmpeg` stdin for remux to `.mkv`. Uses a `.mkv.tmp` temp file; renames on success, deletes on failure. Skips if destination already exists with non-zero size.
- `list_shows()` — lists top-level containers (one per recorded show) with child counts.
- Container ID `1` is the root Recordings container on Fetch TV boxes.

### Web Service (`app/`) — not yet implemented

The Dockerfile expects a FastAPI app at `app/main:app` (uvicorn entry point). `requirements.txt` pins `fastapi==0.115.6` and `uvicorn[standard]==0.32.1`.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `FETCH_HOST` | `10.0.0.14` | IP of the Fetch TV box |
| `FETCH_PORT` | `49152` | DLNA port |
| `OUTPUT_DIR` | `./recordings` | Host path for downloaded files |
| `FFMPEG_PATH` | (PATH lookup) | Override ffmpeg binary location |
