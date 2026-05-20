import asyncio
import logging
import os
import time
from typing import Optional

from curl_cffi.requests import AsyncSession

from .store import Store

log = logging.getLogger(__name__)

CLAUDE_BASE = "https://claude.ai"
STALE_AFTER = 300  # 5 minutes


def _session_key() -> str:
    val = os.environ.get("CLAUDE_SESSION_KEY", "")
    if not val:
        raise RuntimeError("CLAUDE_SESSION_KEY is not set")
    return val


def _poll_interval() -> int:
    return int(os.environ.get("POLL_INTERVAL_SECONDS", "120"))


async def _fetch_org_id(session: AsyncSession) -> Optional[str]:
    pinned = os.environ.get("CLAUDE_ORG_ID")
    if pinned:
        return pinned
    resp = await session.get(
        f"{CLAUDE_BASE}/api/organizations",
        cookies={"sessionKey": _session_key()},
        timeout=15,
    )
    resp.raise_for_status()
    orgs = resp.json()
    if not orgs:
        raise ValueError("No organizations returned")
    return orgs[0]["uuid"]


def _compute_burn_rate(store: Store) -> tuple[float, Optional[int]]:
    now = int(time.time())
    rows = store.recent_five_hour(now - 30 * 60)
    if len(rows) < 2:
        return 0.0, None

    latest_ts, latest_pct = rows[-1]
    earliest_ts, earliest_pct = rows[0]
    dt_hours = (latest_ts - earliest_ts) / 3600
    if dt_hours < 1e-4:
        return 0.0, None

    rate = (latest_pct - earliest_pct) / dt_hours
    if rate <= 0:
        return 0.0, None

    hours_to_full = min((100.0 - latest_pct) / rate, 24.0)
    return rate, now + int(hours_to_full * 3600)


async def polling_loop(store: Store) -> None:
    import src.state as state

    interval = _poll_interval()
    backoff = interval
    auth_incident_notified = False

    async with AsyncSession(impersonate="chrome124") as session:
        while True:
            try:
                if state.org_id is None:
                    state.org_id = await _fetch_org_id(session)
                    log.info("Discovered org_id: %s", state.org_id)

                resp = await session.get(
                    f"{CLAUDE_BASE}/api/organizations/{state.org_id}/usage",
                    cookies={"sessionKey": _session_key()},
                    timeout=15,
                )

                if resp.status_code not in (200, 429):
                    log.warning("HTTP %s — body: %.500s", resp.status_code, resp.text)

                if resp.status_code in (401, 403):
                    log.warning("Auth failure (%s) — marking auth_failed", resp.status_code)
                    state.snapshot.stale = True
                    state.snapshot.auth_failed = True
                    if not auth_incident_notified:
                        auth_incident_notified = True
                        log.error("Cookie needs renewal — update CLAUDE_SESSION_KEY and restart")
                    backoff = min(backoff * 2, 300)
                    await asyncio.sleep(backoff)
                    continue

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    retry_after = min(retry_after, 300)
                    log.warning("Rate limited, retrying in %ds", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                resp.raise_for_status()

                try:
                    data = resp.json()
                    fh = data["five_hour"]
                    sd = data["seven_day"]
                    sdo = data.get("seven_day_opus")

                    now = int(time.time())
                    five_pct = float(fh["utilization"])
                    seven_pct = float(sd["utilization"])
                    opus_pct = float(sdo["utilization"]) if sdo else None

                    store.insert(now, five_pct, seven_pct, opus_pct)
                    store.prune(now - 7 * 24 * 3600)

                    burn_rate, projected = _compute_burn_rate(store)

                    state.snapshot.five_hour_pct = five_pct
                    state.snapshot.five_hour_resets_at = _iso_to_unix(fh["resets_at"])
                    state.snapshot.seven_day_pct = seven_pct
                    state.snapshot.seven_day_resets_at = _iso_to_unix(sd["resets_at"])
                    state.snapshot.seven_day_opus_pct = opus_pct
                    state.snapshot.seven_day_opus_resets_at = (
                        _iso_to_unix(sdo["resets_at"]) if sdo else None
                    )
                    state.snapshot.burn_rate = burn_rate
                    state.snapshot.projected_full_at = projected
                    state.snapshot.stale = False
                    state.snapshot.auth_failed = False
                    state.snapshot.last_update = now
                    auth_incident_notified = False
                    backoff = interval

                    log.debug(
                        "Polled: 5h=%.1f%% 7d=%.1f%% burn=%.2f%%/hr",
                        five_pct,
                        seven_pct,
                        burn_rate,
                    )

                except (KeyError, ValueError, TypeError) as exc:
                    log.error("Unexpected response shape: %s — raw: %.500s", exc, resp.text)
                    state.snapshot.stale = True

            except Exception as exc:
                log.warning("Request error: %s", exc)
                state.snapshot.stale = True
                backoff = min(backoff + 30, 120)

            now_ts = int(time.time())
            if now_ts - state.snapshot.last_update > STALE_AFTER:
                state.snapshot.stale = True

            await asyncio.sleep(backoff)


def _iso_to_unix(ts: str) -> int:
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt.tzinfo is None else int(dt.timestamp())
