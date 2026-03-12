import asyncio
import html as html_lib
import json
import os
import subprocess
import threading
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.request import urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

FETCH_HOST = os.environ.get("FETCH_HOST", "10.0.0.14")
FETCH_PORT = os.environ.get("FETCH_PORT", "49152")
DEVICE_URL = f"http://{FETCH_HOST}:{FETCH_PORT}"
CDS_CONTROL = f"{DEVICE_URL}/web/cds_control"
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))
FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
APP_NAME = os.environ.get("APP_NAME", "Fetch TV Downloader")
ROOT_PATH = os.environ.get("ROOT_PATH", "")

app = FastAPI(root_path=ROOT_PATH)

NS = {
    "d": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
}

BROWSE_SOAP = """\
<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">
      <ObjectID>{object_id}</ObjectID>
      <BrowseFlag>BrowseDirectChildren</BrowseFlag>
      <Filter>*</Filter>
      <StartingIndex>0</StartingIndex>
      <RequestedCount>500</RequestedCount>
      <SortCriteria></SortCriteria>
    </u:Browse>
  </s:Body>
</s:Envelope>"""

# Quality presets: (label, ffmpeg_video_args, ffmpeg_audio_args, scale_filter)
# "hd"  = lossless remux — no re-encoding, full original quality
# "hq"  = H.264 CRF 20, keep original resolution, AAC 192k
# "sd"  = H.264 CRF 28, scale to 720p max, AAC 128k
QUALITY_PRESETS = {
    "hd": {
        "label": "HD",
        "description": "Original quality, no re-encoding",
        "ffmpeg_extra": ["-c", "copy", "-sn"],
        "suffix": "",
    },
    "hq": {
        "label": "HQ",
        "description": "High quality H.264 (CRF 20)",
        "ffmpeg_extra": [
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            "-sn",
        ],
        "suffix": ".hq",
    },
    "sd": {
        "label": "SD",
        "description": "Standard definition 720p (CRF 28)",
        "ffmpeg_extra": [
            "-c:v", "libx264", "-crf", "28", "-preset", "fast",
            "-vf", "scale=-2:720",
            "-c:a", "aac", "-b:a", "128k",
            "-sn",
        ],
        "suffix": ".sd",
    },
}

# In-memory state
downloads: dict[str, "DownloadTask"] = {}
sse_queues: set[asyncio.Queue] = set()
_download_queue: asyncio.Queue = None
_event_loop: asyncio.AbstractEventLoop = None
executor = ThreadPoolExecutor(max_workers=4)
# Maps task_id → threading.Event; set to signal cancellation of an active download
_cancel_events: dict[str, threading.Event] = {}


@dataclass
class DownloadTask:
    id: str
    title: str
    path: list          # DLNA folder hierarchy
    url: str
    size: int
    duration: str
    quality: str = "hd"  # hd | hq | sd
    status: str = "queued"  # queued | downloading | done | error | skipped
    downloaded: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["progress"] = round(self.downloaded / self.size * 100, 1) if self.size > 0 else 0
        preset = QUALITY_PRESETS.get(self.quality, QUALITY_PRESETS["hd"])
        parts = [sanitize(p) for p in self.path]
        filename = sanitize(self.title) + preset["suffix"] + ".mkv"
        d["output_path"] = str(Path(*parts) / filename) if parts else filename
        d["quality_label"] = preset["label"]
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize(name: str) -> str:
    keep = set(" ._-()[]'&")
    return "".join(c if (c.isalnum() or c in keep) else "_" for c in name).strip()


def sizeof_fmt(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def _browse_dlna(object_id: str) -> list[dict]:
    """Blocking DLNA ContentDirectory Browse (runs in thread pool)."""
    body = BROWSE_SOAP.format(object_id=object_id).encode()
    req = urllib.request.Request(
        CDS_CONTROL,
        data=body,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": '"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"',
        },
    )
    with urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")

    start = raw.index("<Result>") + 8
    end = raw.index("</Result>")
    root = ET.fromstring(html_lib.unescape(raw[start:end]))

    children = []
    for child in root:
        tag = child.tag.split("}")[1] if "}" in child.tag else child.tag
        title_el = child.find("dc:title", NS)
        title = title_el.text if title_el is not None else "Unknown"

        if tag == "container":
            children.append({
                "id": child.get("id"),
                "title": title,
                "type": "container",
                "childCount": int(child.get("childCount", 0)),
            })
        elif tag == "item":
            res = child.find("d:res", NS)
            url = res.text if res is not None else None
            if url:
                size = int(res.get("size", 0)) if res is not None else 0
                children.append({
                    "id": child.get("id"),
                    "title": title,
                    "type": "item",
                    "url": url,
                    "size": size,
                    "size_fmt": sizeof_fmt(size),
                    "duration": res.get("duration", "") if res is not None else "",
                })
    return children


async def _broadcast(data: dict):
    msg = f"data: {json.dumps(data)}\n\n"
    dead = set()
    for q in sse_queues:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.add(q)
    sse_queues.difference_update(dead)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    global _download_queue, _event_loop
    _download_queue = asyncio.Queue()
    _event_loop = asyncio.get_event_loop()
    asyncio.create_task(_worker())


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/browse/{object_id}")
async def api_browse(object_id: str):
    loop = asyncio.get_event_loop()
    try:
        children = await loop.run_in_executor(executor, _browse_dlna, object_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"children": children}


@app.get("/api/config")
async def api_config():
    return {"app_name": APP_NAME, "fetch_host": FETCH_HOST, "root_path": ROOT_PATH}


@app.get("/api/qualities")
async def api_qualities():
    return {k: {"label": v["label"], "description": v["description"]} for k, v in QUALITY_PRESETS.items()}


@app.post("/api/download")
async def api_queue_download(body: dict):
    quality = body.get("quality", "hd")
    if quality not in QUALITY_PRESETS:
        quality = "hd"
    task = DownloadTask(
        id=str(uuid.uuid4()),
        title=body["title"],
        path=body["path"],
        url=body["url"],
        size=body.get("size", 0),
        duration=body.get("duration", ""),
        quality=quality,
    )
    downloads[task.id] = task
    await _download_queue.put(task.id)
    await _broadcast({"type": "queued", "task": task.to_dict()})
    return task.to_dict()


@app.get("/api/queue")
async def api_get_queue():
    return [t.to_dict() for t in downloads.values()]


@app.delete("/api/queue/done")
async def api_clear_done():
    to_remove = [k for k, v in downloads.items() if v.status in ("done", "skipped", "error", "cancelled")]
    for k in to_remove:
        del downloads[k]
    await _broadcast({"type": "cleared", "ids": to_remove})
    return {"removed": len(to_remove)}


@app.delete("/api/queue/active")
async def api_cancel_all():
    cancelled = 0
    for task in list(downloads.values()):
        if task.status not in ("queued", "downloading"):
            continue
        event = _cancel_events.get(task.id)
        if event:
            event.set()
        else:
            task.status = "cancelled"
            await _broadcast({"type": "update", "task": task.to_dict()})
        cancelled += 1
    return {"cancelled": cancelled}


@app.delete("/api/queue/{task_id}")
async def api_cancel(task_id: str):
    task = downloads.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in ("queued", "downloading"):
        raise HTTPException(status_code=400, detail="Task is not cancellable")
    event = _cancel_events.get(task_id)
    if event:
        event.set()  # signal the download thread to stop
    else:
        # Still queued — mark cancelled directly so the worker skips it
        task.status = "cancelled"
        await _broadcast({"type": "update", "task": task.to_dict()})
    return {"ok": True}


@app.get("/api/progress")
async def api_progress():
    async def stream():
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        sse_queues.add(q)
        try:
            for task in downloads.values():
                yield f"data: {json.dumps({'type': 'state', 'task': task.to_dict()})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_queues.discard(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Download worker
# ---------------------------------------------------------------------------

async def _worker():
    """Single async worker — processes downloads one at a time."""
    while True:
        task_id = await _download_queue.get()
        task = downloads.get(task_id)
        if not task or task.status == "cancelled":
            continue
        task.status = "downloading"
        await _broadcast({"type": "update", "task": task.to_dict()})
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(executor, _download_sync, task)
        except Exception:
            pass  # errors are set inside _download_sync


def _download_sync(task: DownloadTask):
    """Blocking download + ffmpeg transcode/remux (runs in thread pool)."""

    def send(data: dict):
        asyncio.run_coroutine_threadsafe(_broadcast(data), _event_loop)

    cancel = threading.Event()
    _cancel_events[task.id] = cancel

    preset = QUALITY_PRESETS.get(task.quality, QUALITY_PRESETS["hd"])

    parts = [sanitize(p) for p in task.path]
    dest_dir = OUTPUT_DIR.joinpath(*parts) if parts else OUTPUT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (sanitize(task.title) + preset["suffix"] + ".mkv")
    tmp = dest.with_name(dest.stem + ".part.mkv")

    if dest.exists() and dest.stat().st_size > 0:
        task.status = "skipped"
        task.downloaded = task.size
        _cancel_events.pop(task.id, None)
        send({"type": "update", "task": task.to_dict()})
        return

    ffmpeg_cmd = [
        FFMPEG,
        "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        *preset["ffmpeg_extra"],
        "-y", str(tmp),
    ]

    proc = None
    req = urllib.request.Request(task.url)
    try:
        with urlopen(req, timeout=60) as resp:
            proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)
            last_update_bytes = 0
            try:
                while True:
                    if cancel.is_set():
                        proc.kill()
                        raise RuntimeError("cancelled")
                    buf = resp.read(1024 * 1024)  # 1 MB chunks
                    if not buf:
                        break
                    proc.stdin.write(buf)
                    task.downloaded += len(buf)
                    if task.downloaded - last_update_bytes >= 10 * 1024 * 1024:
                        last_update_bytes = task.downloaded
                        send({"type": "update", "task": task.to_dict()})
            finally:
                proc.stdin.close()
                proc.wait()

        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg exited with code {proc.returncode}")

        tmp.rename(dest)
        task.status = "done"
        task.downloaded = task.size or task.downloaded
        send({"type": "update", "task": task.to_dict()})

    except RuntimeError as e:
        tmp.unlink(missing_ok=True)
        if str(e) == "cancelled":
            task.status = "cancelled"
            task.error = ""
        else:
            task.status = "error"
            task.error = str(e)
        send({"type": "update", "task": task.to_dict()})
    except Exception as e:
        tmp.unlink(missing_ok=True)
        task.status = "error"
        task.error = str(e)
        send({"type": "update", "task": task.to_dict()})
        raise
    finally:
        _cancel_events.pop(task.id, None)


# Serve the frontend last so API routes take priority
app.mount("/", StaticFiles(directory="static", html=True), name="static")
