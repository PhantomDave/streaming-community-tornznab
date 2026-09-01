# sctorznab

A Python bridge that exposes:
- a **Torznab** indexer for Prowlarr
- a **qBittorrent-compatible** client for Sonarr/Radarr
- an HLS downloader based on `yt-dlp`/`ffmpeg`

The app runs on FastAPI and listens on port `9118` by default.

## Requirements

- Python `3.13+`
- `ffmpeg`
- access to at least one of StreamingCommunity or AnimeUnity
- Docker optional, for deployment

## Configuration

1. Copy the example file:
   ```bash
   cp .env.example .env
   ```
2. Set at least these variables:
   - `SC_BASE_URL`
   - `ANIMEUNITY_BASE_URL` (optional — enables the AnimeUnity provider as a second source)
   - `PUBLIC_URL`
   - `TORZNAB_API_KEY`
   - `QBIT_USERNAME`
   - `QBIT_PASSWORD`
   - `DOWNLOAD_PATH`
   - `DB_PATH`

## Build

### Build the Docker image

```bash
docker build -t sctorznab .
```

### Start with Docker Compose (local build)

```bash
docker compose up --build -d
```

### Start with the pre-built image (Docker / Podman)

An example file using the image published on GHCR is available at
[`docker-compose.example.yml`](docker-compose.example.yml).
Image names are fully qualified (`ghcr.io/...`) to ensure
compatibility with Podman.

```bash
cp docker-compose.example.yml docker-compose.yml
# Edit the environment variables in the file, then:
docker compose up -d      # Docker
podman-compose up -d      # Podman
```

### Downloads path

The database (`/data/db`) and downloads (`/data/downloads`) are mounted as
separate volumes. To point downloads at a specific host path — for example
the same library folder already used by Sonarr/Radarr, to allow hardlinks
instead of copies — set `DOWNLOADS_HOST_PATH` before starting compose:

```bash
DOWNLOADS_HOST_PATH=/mnt/media/downloads docker compose up -d
```

If not set, a named Docker/Podman volume is used (`sctorznab-downloads`
in the GHCR example, `./data/downloads` in the local-build compose). `DOWNLOAD_PATH`,
on the other hand, remains the path *inside the container* (default `/data/downloads`)
used by the app to build file paths: only change it if you also move the
mount point in `docker-compose.yml`.

**⚠️ Warning if you're upgrading from a previous version**: before
this change, `docker-compose.example.yml` mounted a single named volume,
`sctorznab-data:/data`. The updated compose file uses two new, separate volumes
(`sctorznab-db` and `sctorznab-downloads`): Docker/Podman creates them empty, the
old `sctorznab-data` is neither touched nor removed, but the app can no longer
see it — the DB and already-downloaded files would appear to have disappeared.
Before running `docker compose up -d` with the new file, copy the data from
the existing volume:

```bash
docker run --rm \
  -v sctorznab-data:/old \
  -v sctorznab-db:/new-db \
  -v sctorznab-downloads:/new-downloads \
  alpine sh -c "cp -a /old/db/. /new-db/ && cp -a /old/downloads/. /new-downloads/"
```

(replace `docker` with `podman` if you're using Podman). If you use the
local-build `docker-compose.yml` with bind mounts (`./data/...`), this doesn't
apply to you: paths on disk remain unchanged.

### Local startup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --host 0.0.0.0 --port 9118
```

## Usage

### Quick check

```bash
curl http://localhost:9118/health
curl "http://localhost:9118/torznab/api?t=caps"
```

### Integration with *Arr apps

- **Prowlarr**
  - type: Generic Torznab
  - URL: `http://<host>:9118/torznab/api`
  - API key: `TORZNAB_API_KEY`
- **Sonarr/Radarr**
  - download client: qBittorrent
  - host: `<host>`
  - port: `9118`
  - credentials: `QBIT_USERNAME` / `QBIT_PASSWORD`
  - category: `sonarr` or `radarr`

## Tests

### Local unit and functional tests

```bash
pytest -q
```

### Live integration tests

These use a real instance and are separate from the main suite.

```bash
RUN_INTEGRATION_TESTS=1 \
SC_INTEGRATION_BASE_URL=https://streamingcommunityz.studio \
pytest -q -m integration
```

### CI

- `.github/workflows/tests.yml` runs `pytest -q` and a Docker smoke build on push and pull requests
- `.github/workflows/integration.yml` runs the live tests manually
- `.github/workflows/publish.yml` publishes the image to GHCR on pushes to `main`

## Contributing

1. Create a branch from your feature.
2. Keep changes small and focused.
3. Update or add tests for every behavior change.
4. Before opening a PR, run:
   ```bash
   pytest -q
   docker build -t sctorznab .
   ```
5. If you touch the StreamingCommunity part, also run the live integration tests when possible.

## Additional documentation

- `ROADMAP.md` contains the extended document with architecture, flows, API, and project plan.
