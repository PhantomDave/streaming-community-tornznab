# Prowlarr compatibility issues

## Summary

Two Prowlarr integration paths are currently blocked:

1. **Generic Torznab indexer cannot be saved**
2. **qBittorrent download client cannot be saved**

Both behaviors reproduce against the current FastAPI app exposed by this repository.

---

## 1) Torznab indexer save is blocked by the Prowlarr test request

### Observed behavior

- Direct queries against `/torznab/api` work when a real query is supplied.
- Prowlarr still rejects the indexer during the built-in test with:
  - `Query successful, but no results were returned from your indexer...`

### Current app behavior

The Torznab route returns an empty feed when `t=search` is called without any query term or ID:

- `/home/runner/work/streaming-community-tornznab/streaming-community-tornznab/app/torznab/router.py`
  - `_build_query(...)` returns `""` when no `q`, `imdbid`, `tmdbid`, or `tvdbid` is provided
  - the route then returns `build_feed_xml(query="", releases=[])`

### Prowlarr behavior

Prowlarr's test path builds a basic Torznab search request with:

- `t=search`
- `extended=1`
- **no `q`**

If the response contains zero releases, Prowlarr treats the test as a failure and refuses to save the indexer.

### Likely fix

Make blank `t=search` requests return a small non-empty default/recent feed instead of an empty feed, or otherwise add a Prowlarr-compatible fallback for RSS-style validation.

---

## 2) qBittorrent download client save is blocked by response format mismatch

### Observed behavior

Prowlarr reaches the qBittorrent-compatible API endpoints:

- `GET /api/v2/app/webapiVersion` -> `200 OK`
- `POST /api/v2/auth/login` -> `403 Forbidden`

Prowlarr then reports:

- `Unable to connect to qBittorrent`
- `The input string '\"2' was not in a correct format.`

### Current app behavior

The qBittorrent compatibility routes currently return Python strings directly for:

- `/api/v2/app/version`
- `/api/v2/app/webapiVersion`

In FastAPI, returning a bare string from a path function produces a JSON string response, so the payload becomes quoted, for example:

- `"2.9.2"`

instead of plain text:

- `2.9.2`

This matches the reported parse failure on the first version segment (`"2`).

Relevant local code:

- `/home/runner/work/streaming-community-tornznab/streaming-community-tornznab/app/qbit/router.py`
  - `app_version()` returns `"v4.6.0"`
  - `app_webapi_version()` returns `"2.9.2"`

### Likely fix

Return **plain text** responses for qBittorrent version endpoints rather than JSON strings, matching qBittorrent WebUI API behavior.

Example endpoints to align:

- `GET /api/v2/app/version`
- `GET /api/v2/app/webapiVersion`
- `POST /api/v2/auth/login` should continue to return `Ok.` / `Fails.` as plain text

### Additional note

The `403` on `/api/v2/auth/login` may still indicate a separate credential mismatch, but the `\"2` parsing error points to a response-format incompatibility that should be fixed regardless.

---

## Recommended implementation order

1. Fix qBittorrent version endpoints to return plain text
2. Fix blank Torznab `t=search` requests to return a non-empty compatibility feed for Prowlarr validation
3. Add regression tests covering both compatibility paths
