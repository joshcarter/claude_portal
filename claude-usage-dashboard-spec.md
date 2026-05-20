# Claude Usage Dashboard — Implementation Handoff

## Goal

A physical desk dashboard that shows how close I am to hitting Anthropic's 5-hour rolling rate limit on my Pro/Max account, plus an hourly history graph below. Hardware is an Adafruit PyPortal (3.2" 320×240 ILI9341 + SAMD51 + ESP32 WiFi co-processor) sitting on my desk.

## Architecture

Two components running on different machines, talking over my LAN:

```
┌─────────────────────────┐         ┌─────────────────────┐         ┌────────────┐
│  claude.ai              │  HTTPS  │  Home-server poller │   HTTP  │  PyPortal  │
│  /api/organizations/    │ ◄────── │  (Python in Docker) │ ◄────── │ CircuitPy  │
│    {org_id}/usage       │  ~60s   │                     │   ~30s  │            │
└─────────────────────────┘         └─────────────────────┘         └────────────┘
```

Why the middle tier exists:
- The PyPortal can't reliably do TLS to claude.ai through its ESP32 co-processor (slow and brittle).
- The `sessionKey` cookie shouldn't live on the PyPortal's FAT filesystem.
- Burn-rate calculations and history aggregation are easier in Python than CircuitPython.
- The middle tier survives PyPortal reboots; the PyPortal sees a stable LAN endpoint.

## Critical context for implementation

Things that aren't in any official docs and that you'd otherwise rediscover the hard way:

1. **The endpoint is undocumented.** `GET https://claude.ai/api/organizations/{org_id}/usage` is an internal endpoint used by the claude.ai settings page. It works, it returns clean JSON, and at least two open-source extensions (`sshnox/Claude-Usage-Tracker`, `lugia19/Claude-Usage-Extension`) rely on it. It may change without notice. Code defensively — wrap parsing in try/except, log unknown shapes, don't assume fields are present.

2. **Auth is a browser session cookie**, not an API key. Format: `sessionKey=sk-ant-sid01-...`. The cookie itself is `HttpOnly` in the browser but readable via DevTools → Application → Cookies. We accept that this means manual re-auth roughly monthly; we explicitly rejected the OAuth-refresh path because of access-token lifetime (8–24h) and refresh-token rotation race conditions with my Claude Code installs.

3. **Org ID discovery.** Before hitting `/usage`, you need an org_id. Get it from `GET https://claude.ai/api/organizations` (same cookie auth) — returns an array. Cache the result; it doesn't change. If the user belongs to multiple orgs (team account, etc.), let them pin one via env var.

4. **The response shape is:**
   ```json
   {
     "five_hour":      { "utilization": 34, "resets_at": "2026-04-20T18:00:00Z" },
     "seven_day":      { "utilization": 72, "resets_at": "2026-04-24T04:00:00Z" },
     "seven_day_opus": { "utilization": 93, "resets_at": "2026-04-26T12:00:00Z" }
   }
   ```
   `utilization` is a percent (0–100, sometimes float, sometimes int — treat as float). `resets_at` is an ISO 8601 UTC timestamp. The `seven_day_opus` field may be absent on Pro plans; handle gracefully.

5. **The 5-hour window is rolling, not bucketed.** Old tokens age out continuously. This means `utilization` can decrease over time without any explicit reset. Burn-rate logic has to account for this — a naive `Δpct/Δt` will be negative during idle periods, which is correct but not useful as a "you're about to run out" signal.

---

## Component 1: home-server poller (Docker container)

### Stack
- Python 3.11+
- Suggested libs: `httpx` (or `requests`), `fastapi` + `uvicorn` for the HTTP server, `apscheduler` or a plain asyncio task for the polling loop, `sqlite3` (stdlib) for persistence. These are suggestions; any equivalent works. The spec is the behavior and the API contract below.

### Behavior

**Polling loop**, every 60–90 seconds:
1. If org_id not cached, fetch it from `/api/organizations` first.
2. `GET https://claude.ai/api/organizations/{org_id}/usage` with `Cookie: sessionKey=<env var>`.
3. On 200: parse, store a timestamped sample in the ring buffer, update the "current" snapshot.
4. On 401/403: mark current snapshot as stale, keep serving the last-good values, optionally fire a webhook notification (env-configurable). Do not crash. Back off to 5-minute polling until auth is fixed.
5. On 429: respect `Retry-After` if present, otherwise back off exponentially up to 5 minutes.
6. On 5xx / network error: log, back off briefly (30s), continue.

**Storage:**
- Keep at least 7 days of timestamped samples. SQLite is fine; a single table `samples(ts INTEGER, five_hour REAL, seven_day REAL, seven_day_opus REAL)` is plenty. Persist across container restarts (mount a volume).
- For the hourly chart, aggregate on read: for each of the last N hours, compute `MAX(five_hour)` observed during that hour. Rationale: the rolling window decays during idle periods and spikes when active, so per-hour max is a good proxy for "how busy was that hour."

**Burn rate:**
- Compute over the last 30 minutes of samples: `(latest_pct - pct_30min_ago) / 0.5` → pct/hour.
- If the result is ≤ 0 (window is decaying faster than you're adding), report `0.0` and `projected_full_at = null`.
- If > 0: `projected_full_at = now + (100 - latest_pct) / burn_rate_per_hour`, capped at 24h out.

### HTTP endpoints exposed to the LAN

No auth on these (LAN-only, behind home firewall). Bind to `0.0.0.0`.

**`GET /status`** — current snapshot:
```json
{
  "five_hour": {
    "used_pct": 34.0,
    "resets_at_unix": 1745526648
  },
  "seven_day": {
    "used_pct": 72.0,
    "resets_at_unix": 1745785600
  },
  "seven_day_opus": {
    "used_pct": 93.0,
    "resets_at_unix": 1745958400
  },
  "burn_rate_pct_per_hour": 8.3,
  "projected_full_at_unix": 1745540000,
  "stale": false,
  "last_update_unix": 1745526400,
  "server_now_unix": 1745526420
}
```
- All timestamps as Unix epoch seconds (integers). Easier for CircuitPython to handle than ISO 8601.
- `stale: true` if the last successful poll was >5 minutes ago. The PyPortal uses this to dim the display.
- `projected_full_at_unix` is `null` if burn rate is non-positive.
- `seven_day_opus` may be absent in the response if the upstream doesn't include it; serve `null`.
- `server_now_unix` lets the PyPortal show a "freshness" age without needing its own NTP.

**`GET /history?hours=24`** — hourly aggregates for the chart:
```json
{
  "buckets": [
    { "hour_unix": 1745440000, "five_hour_peak": 12.0 },
    { "hour_unix": 1745443600, "five_hour_peak": 18.0 },
    ...
  ],
  "server_now_unix": 1745526420
}
```
- Default 24 hours; cap at 168 (7 days).
- Hours are aligned to wall-clock UTC hours.
- Buckets with no samples: emit with `five_hour_peak: null` so the PyPortal can render gaps correctly.

**`GET /health`** — liveness only, returns `{"ok": true}` always when the process is up. Not used by PyPortal; for `docker healthcheck`.

### Configuration (env vars)

| Var | Required | Notes |
|-----|----------|-------|
| `CLAUDE_SESSION_KEY` | yes | The `sessionKey` cookie value (starts `sk-ant-sid01-...`) |
| `CLAUDE_ORG_ID` | no | Pin a specific org. If absent, auto-discover and use the first one. |
| `POLL_INTERVAL_SECONDS` | no | Default 120 |
| `NOTIFY_WEBHOOK_URL` | no | POST `{"event": "auth_failed", "since": "..."}` on 401 |
| `LISTEN_PORT` | no | Default 8080 |
| `DB_PATH` | no | Default `/data/samples.db` |

### Container

- Single Dockerfile, multi-stage build is fine but not required.
- Expose `LISTEN_PORT`.
- `HEALTHCHECK` hitting `/health`.
- Mount `/data` as a volume for the SQLite file.
- Provide a `docker-compose.yml` that picks up `.env` and mounts the volume.
- Run as non-root.

### Failure modes to handle gracefully

- Cookie invalidated → `/status` returns last-good data with `stale: true`, webhook fires once per auth incident (not every poll).
- Network down → same.
- Anthropic returns unexpected JSON shape → log the raw response, mark stale, don't crash.
- SQLite corruption → log loudly, recreate the DB, lose history but keep serving.
- Container restart → ring buffer reloads from SQLite.

---

## Component 2: PyPortal client (CircuitPython)

### Hardware
- Adafruit PyPortal (the 3.2", 320×240, non-Titano variant). Display is ILI9341.
- ATSAMD51J20 + ESP32 co-processor for WiFi.
- Resistive touchscreen present but unused in v1.

### Stack
- CircuitPython 9.x (current stable). 8.x will work if needed; avoid 7.x.
- Libraries (all from the official Adafruit CircuitPython bundle):
  - `adafruit_pyportal` — handles WiFi+ESP32 init and the JSON fetch
  - `adafruit_display_shapes` — `Rect` for bars
  - `adafruit_display_text.label` — `Label` for text
  - `adafruit_bitmap_font` — load a nicer font than the built-in (optional)
- Built-in modules used: `displayio`, `terminalio`, `time`, `rtc`, `wifi` (via PyPortal helper).

### Display layout (320 wide × 240 tall, landscape)

```
┌──────────────────────────────────────────────────────────┐  y=0
│  ████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░   68% / 5h │  top bar  (h=40)
│  Sonnet · resets 14:32 · 8.3%/hr  → full in ~3h 50m      │  status   (h=22)
├──────────────────────────────────────────────────────────┤  y=62
│  │                                                       │
│  │     ▄▄    ▄▄                                          │
│  │ ▄▄ ████ ████▄▄    ▄▄                                  │
│  │ █████████████████████▄  ▄▄                            │  chart    (h=170)
│  │ ████████████████████████████▄▄                        │
│  │ ██████████████████████████████████                    │
│  └────────────────────────────────────────────────────── │  y=232
└──────────────────────────────────────────────────────────┘  y=240
```

Pixel coordinates approximate; tune to taste. All elements in one `displayio.Group`.

### Color coding (for the top bar)

| Five-hour utilization | Fill color |
|-----------------------|-----------|
| < 60% | blue |
| 60–80% | amber |
| 80–95% | orange-red |
| ≥ 95% | red |

If `projected_full_at_unix` is within the current 5-hour window and burn rate would exhaust before reset → upgrade the color band by one step regardless of current percentage (catches "burning fast even if low now").

Seven-day sub-bar uses the same colors.

If `stale: true`, render everything at ~40% brightness (use `board.DISPLAY.brightness = 0.4`) and overlay a small "STALE" badge in the top-right.

### Data flow

On boot:
1. Connect to WiFi (credentials from `settings.toml`).
2. Build the displayio scene graph (bars + labels + chart placeholders, all empty).
3. Show the screen.
4. Enter the main loop.

Main loop:
1. `GET http://home.local:8080/status` (use `adafruit_pyportal.PyPortal.fetch` with `json_path` to extract only the fields needed, to keep memory under control).
2. Update the bar widths, label text, and color of the top bar based on values.
3. Every Nth iteration (e.g. every 5 polls = ~2.5 min), also `GET /history?hours=24` and redraw the chart.
4. Sleep 30 seconds.
5. On any HTTP error: leave display as-is, show a small "no conn" indicator, retry.

### Chart rendering

- 24 hourly buckets across the chart area width (~280px usable → ~11px per bar with a 1px gap).
- Each bar is a `Rect` whose `.height` and `.y` get mutated on update; never recreate the Rects (it's expensive).
- Bar height: `(peak_pct / 100) * chart_area_height`.
- Empty buckets (no samples this hour): render as a 1px-tall dim placeholder at the baseline.
- No axis labels in v1 (keeps it clean). Add tick marks at the right edge for 0/50/100% if there's room.

### Memory and performance notes

- The SAMD51 has 192 KB of SRAM. After CircuitPython + libraries, you have on the order of 60–80 KB free.
- Use `json_path` in `fetch()` to avoid holding the full response. For `/history`, iterate the buckets array rather than holding it as a list if possible.
- `gc.collect()` after each successful update.
- Don't allocate new displayio objects in the main loop. Build everything once at startup and mutate.

### Configuration (`settings.toml`)

```toml
CIRCUITPY_WIFI_SSID = "..."
CIRCUITPY_WIFI_PASSWORD = "..."
HOMESERVER_URL = "http://home.local:8080"
POLL_SECONDS = 30
HISTORY_REFRESH_EVERY_N_POLLS = 5
```

---

## API contract (the boundary between the two components)

This is the contract. The PyPortal code should only depend on these shapes; the server should never break them without bumping a version field. (No need for an explicit version field in v1, but keep the option open.)

### `GET /status` response

| Field | Type | Notes |
|-------|------|-------|
| `five_hour.used_pct` | float | 0–100 |
| `five_hour.resets_at_unix` | int | Unix seconds, UTC |
| `seven_day.used_pct` | float | 0–100 |
| `seven_day.resets_at_unix` | int | Unix seconds, UTC |
| `seven_day_opus.used_pct` | float \| null | Null on Pro plans |
| `seven_day_opus.resets_at_unix` | int \| null | |
| `burn_rate_pct_per_hour` | float | 0 if decaying |
| `projected_full_at_unix` | int \| null | Null if burn ≤ 0 |
| `stale` | bool | True if last upstream poll >5 min ago |
| `last_update_unix` | int | When the last successful upstream poll happened |
| `server_now_unix` | int | Current time on the home server |

### `GET /history?hours=N` response

| Field | Type | Notes |
|-------|------|-------|
| `buckets` | array of objects | Sorted oldest → newest |
| `buckets[].hour_unix` | int | Start of the hour, UTC |
| `buckets[].five_hour_peak` | float \| null | Null = no samples that hour |
| `server_now_unix` | int | |

---

## Initial auth setup (one-time, manual)

This is the README the user will follow on day one and after each cookie expiration:

1. Open `https://claude.ai` in any browser, signed in.
2. Open DevTools (F12 in Chrome/Edge/Firefox).
3. Application (or Storage) → Cookies → `https://claude.ai`.
4. Find the row named `sessionKey`. Value starts with `sk-ant-sid01-`.
5. Copy the **value** (not the name, not the whole row).
6. On the home server: edit `.env` in the project directory and set `CLAUDE_SESSION_KEY=sk-ant-sid01-...`.
7. `docker compose restart claude-usage-poller`.
8. `curl http://localhost:8080/status` — should return real data, `stale: false`.

Expected re-auth cadence: every few weeks to months. When `stale` goes true and stays true, the webhook (if configured) will fire and the dashboard will dim — that's the cue.

---

## Repo structure (suggested)

```
claude-usage-dashboard/
├── README.md
├── server/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── pyproject.toml          # or requirements.txt
│   ├── .env.example
│   └── src/
│       ├── poller.py           # polling loop
│       ├── store.py            # SQLite wrapper
│       ├── api.py              # FastAPI app
│       └── main.py             # entry point
└── pyportal/
    ├── code.py                 # main CircuitPython entry
    ├── settings.toml.example
    └── README.md               # libraries to drop in /lib from CP bundle
```

---

## Testing

### Server side
- Unit tests for the burn-rate calc (positive, zero, decaying), the hourly aggregation, and the JSON shape.
- Integration test with a recorded fixture of the `/usage` response — no live calls in CI.
- Manual smoke test: spin up the container with a real cookie, hit `/status` and `/history` with curl, watch for ~30 minutes to confirm the burn rate updates sensibly.

### PyPortal side
- Mock server (a 50-line Flask/FastAPI returning fixture JSON) to exercise color thresholds, stale handling, and history rendering without needing the real poller.
- Test on the actual hardware before declaring done. The SAMD51's memory pressure can't be checked in simulation.

---

## Explicit non-goals for v1

- No touch interaction (the screen is touch but we're not using it).
- No on-device authentication or HTTPS between PyPortal and home server (LAN-only is fine).
- No OAuth refresh implementation (cookie auth only — this was an explicit design decision).
- No multi-account support (one Anthropic account, one PyPortal).
- No alerting beyond the optional webhook for auth failure.
- No historical data export.
- No mobile/web companion app — the PyPortal is the only client.

Things on the wish-list for a v2, not v1:
- A small touch zone to switch between 24h / 7-day chart views.
- A second screen showing per-model breakdown (would need a different upstream endpoint).
- Battery + suspend for portability.

---

## Open questions to confirm before implementing

2. **Webhook target for auth-failure notifications** — ntfy, Home Assistant, Slack, none? Affects the env var docs.
3. **Time zone for the chart x-axis labels** — UTC, local? The data is UTC; display can be local if the PyPortal knows the offset.
