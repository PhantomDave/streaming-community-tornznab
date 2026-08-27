# sctorznab

Bridge in Python che espone:
- un indexer **Torznab** per Prowlarr
- un client **qBittorrent-compatible** per Sonarr/Radarr
- un downloader HLS basato su `yt-dlp`/`ffmpeg`

L'app gira su FastAPI e ascolta di default sulla porta `9118`.

## Requisiti

- Python `3.13+`
- `ffmpeg`
- accesso a un'istanza StreamingCommunity compatibile
- Docker opzionale per il deploy

## Configurazione

1. Copia il file di esempio:
   ```bash
   cp .env.example .env
   ```
2. Imposta almeno queste variabili:
   - `SC_BASE_URL`
   - `PUBLIC_URL`
   - `TORZNAB_API_KEY`
   - `QBIT_USERNAME`
   - `QBIT_PASSWORD`
   - `DOWNLOAD_PATH`
   - `DB_PATH`

## Build

### Build immagine Docker

```bash
docker build -t sctorznab .
```

### Avvio con Docker Compose (build locale)

```bash
docker compose up --build -d
```

### Avvio con immagine pre-compilata (Docker / Podman)

Un file d'esempio con l'immagine pubblicata su GHCR è disponibile in
[`docker-compose.example.yml`](docker-compose.example.yml).
I nomi immagine sono completamente qualificati (`ghcr.io/...`) per garantire
la compatibilità con Podman.

```bash
cp docker-compose.example.yml docker-compose.yml
# Modifica le variabili d'ambiente nel file, poi:
docker compose up -d      # Docker
podman-compose up -d      # Podman
```

### Percorso dei download

Il database (`/data/db`) e i download (`/data/downloads`) sono montati come volumi
separati. Per puntare i download a un path specifico dell'host — ad esempio la
stessa cartella libreria già usata da Sonarr/Radarr, per permettere hardlink
invece di copie — imposta `DOWNLOADS_HOST_PATH` prima di avviare compose:

```bash
DOWNLOADS_HOST_PATH=/mnt/media/downloads docker compose up -d
```

Se non impostata, viene usato un volume Docker/Podman con nome (`sctorznab-downloads`
nell'esempio GHCR, `./data/downloads` nel compose per build locale). `DOWNLOAD_PATH`
resta invece il path *interno al container* (default `/data/downloads`) usato
dall'app per costruire i percorsi dei file: va cambiato solo se sposti anche il
mount point nel `docker-compose.yml`.

**⚠️ Attenzione se stai aggiornando da una versione precedente**: prima di
questa modifica `docker-compose.example.yml` montava un unico volume con nome
`sctorznab-data:/data`. Il compose aggiornato usa due volumi nuovi e distinti
(`sctorznab-db` e `sctorznab-downloads`): Docker/Podman li crea vuoti, il
vecchio `sctorznab-data` non viene toccato né rimosso, ma l'app non lo vede
più — DB e download già scaricati sembrerebbero spariti. Prima di eseguire
`docker compose up -d` con il nuovo file, copia i dati dal volume esistente:

```bash
docker run --rm \
  -v sctorznab-data:/old \
  -v sctorznab-db:/new-db \
  -v sctorznab-downloads:/new-downloads \
  alpine sh -c "cp -a /old/db/. /new-db/ && cp -a /old/downloads/. /new-downloads/"
```

(sostituisci `docker` con `podman` se usi Podman). Chi usa il `docker-compose.yml`
per build locale con bind mount (`./data/...`) non è interessato: i percorsi
su disco restano invariati.

### Avvio locale

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --host 0.0.0.0 --port 9118
```

## Utilizzo

### Verifica rapida

```bash
curl http://localhost:9118/health
curl "http://localhost:9118/torznab/api?t=caps"
```

### Integrazione con *Arr

- **Prowlarr**
  - tipo: Generic Torznab
  - URL: `http://<host>:9118/torznab/api`
  - API key: `TORZNAB_API_KEY`
- **Sonarr/Radarr**
  - download client: qBittorrent
  - host: `<host>`
  - porta: `9118`
  - credenziali: `QBIT_USERNAME` / `QBIT_PASSWORD`
  - categoria: `sonarr` o `radarr`

## Test

### Test unitari e funzionali locali

```bash
pytest -q
```

### Test di integrazione live

Usano un'istanza reale e sono separati dalla suite principale.

```bash
RUN_INTEGRATION_TESTS=1 \
SC_INTEGRATION_BASE_URL=https://streamingcommunityz.studio \
pytest -q -m integration
```

### CI

- `.github/workflows/tests.yml` esegue `pytest -q` e una build Docker smoke su push e pull request
- `.github/workflows/integration.yml` esegue i test live manualmente
- `.github/workflows/publish.yml` pubblica l'immagine su GHCR dai push a `main`

## Contribuire

1. Crea un branch dalla tua feature.
2. Mantieni le modifiche piccole e mirate.
3. Aggiorna o aggiungi test per ogni cambiamento di comportamento.
4. Esegui prima di aprire la PR:
   ```bash
   pytest -q
   docker build -t sctorznab .
   ```
5. Se tocchi la parte StreamingCommunity, esegui anche i test di integrazione live quando possibile.

## Documentazione aggiuntiva

- `ROADMAP.md` contiene il documento esteso con architettura, flussi, API e piano progettuale.
