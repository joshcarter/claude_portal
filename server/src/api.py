import asyncio
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query

from .poller import polling_loop
from .store import Store


def _store() -> Store:
    return Store(os.environ.get("DB_PATH", "/data/samples.db"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = _store()
    app.state.store = store
    task = asyncio.create_task(polling_loop(store))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status")
def status():
    import src.state as state

    s = state.snapshot
    now = int(time.time())

    seven_day_opus = None
    if s.seven_day_opus_pct is not None:
        seven_day_opus = {
            "used_pct": s.seven_day_opus_pct,
            "resets_at_unix": s.seven_day_opus_resets_at,
        }

    return {
        "five_hour": {
            "used_pct": s.five_hour_pct,
            "resets_at_unix": s.five_hour_resets_at,
        },
        "seven_day": {
            "used_pct": s.seven_day_pct,
            "resets_at_unix": s.seven_day_resets_at,
        },
        "seven_day_opus": seven_day_opus,
        "burn_rate_pct_per_hour": s.burn_rate,
        "projected_full_at_unix": s.projected_full_at,
        "stale": s.stale,
        "auth_failed": s.auth_failed,
        "last_update_unix": s.last_update,
        "server_now_unix": now,
    }


@app.get("/history")
def history(hours: int = Query(default=24, ge=1, le=168)):
    store: Store = app.state.store
    buckets = store.hourly_peaks(hours)
    return {
        "buckets": buckets,
        "server_now_unix": int(time.time()),
    }
