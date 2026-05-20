# Claude Usage Dashboard

Physical desk display showing Claude Pro/Max rate-limit burn rate on an Adafruit PyPortal.

```
┌──────────────────────────────────────────────────────────┐
│  5H  68%  ▐█▐█▐█▐█▐█▐█▐█▐█▐█▐█▐█▐█▐█░░░░░░░░░░░░░░░░░  │
│  resets 14:32 · 8.3%/hr → ~3h50m                        │
│  7D  72%  ▐█▐█▐█▐█▐█▐█▐█▐█▐█▐█▐█▐█▐█▐█░░░░░░░░░░░░░░░  │
├──────────────────────────────────────────────────────────┤
│  24H                                                     │
│   █                                                      │
│   █  █                                                   │
│  ██  ██ ██                                               │
│  ████████████ █                                          │
└──────────────────────────────────────────────────────────┘
```

## Architecture

```
claude.ai/api/organizations/{id}/usage
        ↓ HTTPS ~120s
server/   (Python + FastAPI in Docker, home server)
        ↓ HTTP ~30s
pyportal/ (CircuitPython on Adafruit PyPortal)
```

## Quick start

### 1. Home server

```bash
cd server
cp .env.example .env
# Edit .env — set CLAUDE_SESSION_KEY (see Auth setup below)
docker compose up -d
curl http://localhost:8080/status
```

### 2. PyPortal

See [pyportal/README.md](pyportal/README.md) for library and font installation.

```
# Copy to CIRCUITPY:
pyportal/code.py         → /code.py
pyportal/settings.toml   → /settings.toml   (fill in your values)
fonts/*.bdf              → /fonts/
```

## Auth setup (first time + after cookie expiry)

1. Open `https://claude.ai` in your browser, signed in.
2. DevTools → Application → Cookies → `https://claude.ai`
3. Find `sessionKey` — value starts `sk-ant-sid01-...`
4. Copy the value into `.env` as `CLAUDE_SESSION_KEY=sk-ant-sid01-...`
5. `docker compose restart claude-usage-poller`
6. `curl http://localhost:8080/status` — should show `"stale": false`

Expected re-auth cadence: every few weeks to months.
When the cookie expires the PyPortal will show "NEEDS AUTH" at reduced brightness.

## Server environment variables

| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `CLAUDE_SESSION_KEY` | yes | — | Cookie value |
| `CLAUDE_ORG_ID` | no | auto-discover | Pin a specific org |
| `POLL_INTERVAL_SECONDS` | no | `120` | |
| `LISTEN_PORT` | no | `8080` | |
| `DB_PATH` | no | `/data/samples.db` | Mount `/data` as a volume |

## Running server tests

```bash
cd server
pip install -r requirements.txt httpx pytest
pytest tests/
```
