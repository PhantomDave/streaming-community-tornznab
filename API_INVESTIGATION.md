# StreamingCommunity API Investigation Report

## Executive Summary
The integration tests are failing because **`streamingcommunityz.studio` does not expose the `/api/search` endpoint** that the code expects. This is an environmental/configuration issue, not a code bug.

---

## Findings

### ✗ What Doesn't Exist
The application expects these API endpoints on the StreamingCommunity instance:
```
GET /api/search?q={query}        → Returns: {"data": [...titles...]}
GET /titles/{id}-{slug}          → Page with Inertia data
GET /titles/{id}-{slug}/season-{n} → Season episodes
GET /iframe/{id}                 → HTML containing m3u8 stream URL
```

**But on `streamingcommunityz.studio`, these endpoints return:**
- `/api/search` → **404 Not Found**
- `/api/titles` → **404 Not Found**
- `/api/*` → **All 404**
- `/titles` → **405 Method Not Allowed** (expects POST)
- `/titles/search` → **405 Method Not Allowed** (expects PATCH/DELETE)
- `/iframe/{id}` → **404 Not Found**

### ✓ What Does Exist
- Root domain: **200 OK** (serves HTML)
- Pages like `/it/titles/65568-...` → **200 OK** (HTML pages)
- Inertia.js framework is used for frontend

### Server Response Analysis

| Endpoint | GET | POST | PATCH | DELETE | Status Code | Notes |
|----------|-----|------|-------|--------|-------------|-------|
| `/titles` | 405 | 419 | 405 | 405 | — | POST returns 419 (CSRF error) |
| `/titles/search` | 405 | 405 | 419 | 419 | — | Only PATCH/DELETE allowed, both return 419 |
| `/api/search` | 404 | 404 | 404 | 404 | — | Endpoint doesn't exist |
| `/iframe/1` | 404 | 404 | 404 | 404 | — | Endpoint doesn't exist |

**Status Code Meanings:**
- **404**: Endpoint not found
- **405**: Method not allowed (endpoint exists but doesn't accept this HTTP method)
- **419**: Likely CSRF token validation failure (Laravel-style framework)

---

## Root Cause Analysis

The code was designed to integrate with a **StreamingCommunity instance that exposes a REST API**, specifically:
- Search API at `/api/search`
- Title details accessible via `/titles/{id}-{slug}` with Inertia headers
- Iframe/stream extraction from `/iframe/{id}`

**But `streamingcommunityz.studio` appears to be:**
1. A **web-only interface** (renders HTML pages for users)
2. **NOT exposing programmatic APIs** for external tools
3. Possibly a **different implementation** than what the code targets

---

## Evidence from Code

From `ROADMAP.md`:
```
SITE[("Sito StreamingCommunity\napi/search · titles · vixcloud → m3u8")]
```

From `app/sc/search.py`:
```python
payload = await client.get_json("/api/search", params={"q": query})
```

From `app/sc/titles.py`:
```python
payload = await client.get_json(f"/titles/{sc_id}-{slug}", headers=INERTIA_HEADERS)
```

From `app/sc/resolver.py`:
```python
iframe_html = await client.get_text(iframe_path, params=params)  # "/iframe/{sc_id}"
```

---

## Why Tests Pass Locally But Fail on Integration

✅ **Unit tests PASS** (33/35)
- They don't make real API calls
- They use mocked data
- They only test the code logic

❌ **Integration tests FAIL** (2/2 when enabled)
- They make real HTTP calls to `streamingcommunityz.studio`
- They expect `/api/search` endpoint to exist
- Endpoint returns 404

---

## Recommendations

### Option 1: Use a Different StreamingCommunity Instance
If you have access to a **different SC mirror/instance** that exposes the API, update:
- `.env.example`: `SC_BASE_URL=https://your-sc-instance.com`
- `.github/workflows/integration.yml`: Update `SC_INTEGRATION_BASE_URL`
- `tests/test_sc_integration.py`: Update `DEFAULT_BASE_URL`

### Option 2: Mock the Integration Tests
Since the public domain doesn't expose APIs, you can:
- Mock the HTTP responses in tests
- Or skip integration tests for CI/CD
- Or document that integration tests require a private SC instance

### Option 3: Implement HTML Scraping Fallback
If no API is available, implement fallback that:
- Scrapes search results from HTML
- Extracts data from `<script>` tags
- Parses Inertia.js props

---

## Next Steps

1. **Verify the intended SC instance**: Do you have a working SC instance with API access?
2. **Update configuration**: If yes, update the base URL in config files
3. **Document requirements**: Add setup guide for which SC instances work
4. **Conditional CI**: Make integration tests conditional on having a working instance
