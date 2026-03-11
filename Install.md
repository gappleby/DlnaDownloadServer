# Installing on a QNAP NAS

## Prerequisites

- QNAP NAS running QTS 5.x
- **Container Station** installed (App Center → search "Container Station")
- The Fetch TV box and the NAS on the same network
- A shared folder on the NAS where recordings will be saved (e.g. `recordings`)

---

## Option A — Container Station UI

### 1. Pull the image

Open **Container Station** → **Images** → **Pull**.

- Registry: `ghcr.io`
- Image: `gappleby/dlnadownloadserver`
- Tag: `latest`

Click **Pull**.

### 2. Create the container

Go to **Containers** → **Create**.

**Basic settings**
| Field | Value |
|-------|-------|
| Name | `fetchtv` |
| Image | `ghcr.io/gappleby/dlnadownloadserver:latest` |
| Restart policy | Unless stopped |

**Network**

Leave as **Bridge** — QNAP bridge containers can reach LAN devices without any extra configuration.

Add a port mapping:
| Host port | Container port | Protocol |
|-----------|---------------|----------|
| `8000` | `8000` | TCP |

**Environment variables**
| Name | Value | Notes |
|------|-------|-------|
| `FETCH_HOST` | `10.0.0.14` | IP of the Fetch TV box |
| `FETCH_PORT` | `49152` | DLNA port |
| `OUTPUT_DIR` | `/output` | Must match the container path in the volume mount |
| `APP_NAME` | `Fetch TV Downloader` | Optional — name shown in browser tab and header |
| `ROOT_PATH` | _(empty)_ | Optional — set to your reverse proxy sub-path e.g. `/fetchtv` |

**Storage (volume mount)**

Click **Add volume** and map your recordings shared folder into the container:

| Host path | Container path |
|-----------|---------------|
| `/share/recordings` | `/output` |

> Replace `/share/recordings` with the actual path to your shared folder on the NAS. You can find this in **File Station** → right-click the folder → **Properties**.

Click **Create** and then **Start**.

### 3. Access the web UI

Open a browser and go to:

```
http://<NAS-IP>:8000
```

---

## Option B — docker-compose via SSH

### 1. Enable SSH on the NAS

**Control Panel** → **Network & File Services** → **Telnet / SSH** → enable SSH.

### 2. SSH into the NAS

```bash
ssh admin@<NAS-IP>
```

### 3. Create the project directory

```bash
mkdir -p /share/recordings
mkdir -p ~/fetchtv
cd ~/fetchtv
```

### 4. Create docker-compose.yml

```bash
cat > docker-compose.yml << 'EOF'
services:
  fetchtv:
    image: ghcr.io/gappleby/dlnadownloadserver:latest
    container_name: fetchtv
    ports:
      - "8000:8000"
    volumes:
      - /share/recordings:/output
    environment:
      - FETCH_HOST=10.0.0.14
      - FETCH_PORT=49152
      - OUTPUT_DIR=/output
      - APP_NAME=Fetch TV Downloader
      # - ROOT_PATH=/fetchtv   # uncomment if using a reverse proxy sub-path
    restart: unless-stopped
EOF
```

### 5. Start the container

```bash
docker compose up -d
```

### 6. Access the web UI

```
http://<NAS-IP>:8000
```

---

## Updating to a new version

```bash
docker compose pull && docker compose up -d
```

Or in Container Station: **Images** → find the image → **Pull** (re-pulls latest) → **Containers** → **Restart**.

---

## Troubleshooting

**Cannot connect to Fetch TV device**
- Confirm the Fetch TV box is on and on the same subnet as the NAS
- SSH into the NAS and test: `curl http://10.0.0.14:49152/MediaServer.xml`
- If that times out, try setting `network_mode: host` in docker-compose.yml and remove the `ports:` mapping

**Port 8000 already in use**
- Change the host port in the mapping, e.g. `8001:8000`, then access via `:8001`

**Output files not appearing in File Station**
- Confirm the volume host path matches your shared folder exactly
- Check permissions: the container runs as root, so the shared folder should be writable by all
