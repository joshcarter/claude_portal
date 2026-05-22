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
SEVEN_DAY_BURN_BASELINE = 3 * 3600  # 7d utilization moves slowly; a 30-min delta is just noise
FIVE_HOUR_BURN_WINDOW = 30 * 60  # regression window for the 5h burn rate
FIVE_HOUR_BURN_MIN_SPAN = 10 / 60  # hours of spread required before a slope is trusted
MAX_REDLINE_RATIO = 10.0


def _session_key() -> str:
    val = os.environ.get("CLAUDE_SESSION_KEY", "")
    if not val:
        raise RuntimeError("CLAUDE_SESSION_KEY is not set")
    return val


def _poll_interval() -> int:
    return int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))


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


def _burn_rate(rows: list[tuple[int, float]], min_dt_hours: float) -> float:
    """Least-squares burn rate (pct/hour) across the sample window; 0.0 if decaying or too sparse.

    Uses every sample, not just the endpoints, so integer-quantized utilization
    readings average out instead of jolting the slope as steps age in and out.
    """
    if len(rows) < 2:
        return 0.0

    if (rows[-1][0] - rows[0][0]) / 3600 < min_dt_hours:
        return 0.0

    n = len(rows)
    t0 = rows[0][0]
    ts = [(ts - t0) / 3600 for ts, _ in rows]
    ys = [pct for _, pct in rows]
    mean_t = sum(ts) / n
    mean_y = sum(ys) / n
    var_t = sum((t - mean_t) ** 2 for t in ts)
    if var_t == 0:
        return 0.0

    cov = sum((t - mean_t) * (y - mean_y) for t, y in zip(ts, ys))
    rate = cov / var_t
    return rate if rate > 0 else 0.0


def _compute_five_hour_burn(store: Store) -> float:
    return _burn_rate(
        store.recent_five_hour(int(time.time()) - FIVE_HOUR_BURN_WINDOW),
        FIVE_HOUR_BURN_MIN_SPAN,
    )


def _compute_seven_day_burn(store: Store) -> float:
    return _burn_rate(store.recent_seven_day(int(time.time()) - SEVEN_DAY_BURN_BASELINE), 1.0)


def _fmt_ratio(ratio: Optional[float]) -> str:
    return "n/a" if ratio is None else "{:.2f}".format(ratio)


def _redline(
    pct: float, resets_at: Optional[int], burn: float, now: int
) -> tuple[Optional[float], Optional[float]]:
    """Return (sustainable pct/hour, redline_ratio) for a usage window; ratio 1.0 = redline."""
    if resets_at is None:
        return None, None
    hours_to_reset = (resets_at - now) / 3600
    if hours_to_reset <= 0:
        return None, None

    sustainable = max(0.0, (100.0 - pct) / hours_to_reset)
    if burn <= 0:
        return sustainable, 0.0
    if sustainable <= 0:
        return sustainable, MAX_REDLINE_RATIO
    return sustainable, min(burn / sustainable, MAX_REDLINE_RATIO)


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

                    five_resets = _iso_to_unix(fh["resets_at"])
                    seven_resets = _iso_to_unix(sd["resets_at"])

                    five_burn = _compute_five_hour_burn(store)
                    five_sustainable, five_ratio = _redline(
                        five_pct, five_resets, five_burn, now
                    )
                    seven_burn = _compute_seven_day_burn(store)
                    seven_sustainable, seven_ratio = _redline(
                        seven_pct, seven_resets, seven_burn, now
                    )

                    state.snapshot.five_hour_pct = five_pct
                    state.snapshot.five_hour_resets_at = five_resets
                    state.snapshot.five_hour_burn_rate = five_burn
                    state.snapshot.five_hour_sustainable_rate = five_sustainable
                    state.snapshot.five_hour_redline_ratio = five_ratio
                    state.snapshot.seven_day_pct = seven_pct
                    state.snapshot.seven_day_resets_at = seven_resets
                    state.snapshot.seven_day_burn_rate = seven_burn
                    state.snapshot.seven_day_sustainable_rate = seven_sustainable
                    state.snapshot.seven_day_redline_ratio = seven_ratio
                    state.snapshot.seven_day_opus_pct = opus_pct
                    state.snapshot.seven_day_opus_resets_at = (
                        _iso_to_unix(sdo["resets_at"]) if sdo else None
                    )
                    state.snapshot.stale = False
                    state.snapshot.auth_failed = False
                    state.snapshot.last_update = now
                    auth_incident_notified = False
                    backoff = interval

                    log.debug(
                        "Polled: 5h=%.1f%% redline=%s  7d=%.1f%% redline=%s",
                        five_pct,
                        _fmt_ratio(five_ratio),
                        seven_pct,
                        _fmt_ratio(seven_ratio),
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
