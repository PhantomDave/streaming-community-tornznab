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

### Avvio con Docker Compose

```bash
docker compose up --build -d
```

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
