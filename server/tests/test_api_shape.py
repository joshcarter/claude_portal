"""
Integration test: spin up the FastAPI app against fixture data and verify
the /status and /history response shapes match the API contract.
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import src.state as state
from src.api import app
from src.store import Store


@pytest.fixture(autouse=True)
def reset_state():
    state.snapshot.five_hour_pct = 34.0
    state.snapshot.five_hour_resets_at = 1745525400
    state.snapshot.seven_day_pct = 72.0
    state.snapshot.seven_day_resets_at = 1745640000
    state.snapshot.seven_day_opus_pct = 93.5
    state.snapshot.seven_day_opus_resets_at = 1745812800
    state.snapshot.burn_rate = 8.3
    state.snapshot.projected_full_at = 1745540000
    state.snapshot.stale = False
    state.snapshot.auth_failed = False
    state.snapshot.last_update = int(time.time())


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db)

    # Pre-populate DB at the same path the lifespan will open
    store = Store(db)
    now = int(time.time())
    store.insert(now - 1800, 20.0, 50.0, None)
    store.insert(now,        34.0, 72.0, 93.5)

    # Stub the polling loop so it doesn't try to hit claude.ai
    async def _noop(store):
        await asyncio.sleep(3600)

    monkeypatch.setattr("src.api.polling_loop", _noop)

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_status_shape(client):
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()

    assert "five_hour" in body
    assert "used_pct" in body["five_hour"]
    assert "resets_at_unix" in body["five_hour"]

    assert "seven_day" in body
    assert "seven_day_opus" in body

    assert isinstance(body["burn_rate_pct_per_hour"], float)
    assert isinstance(body["stale"], bool)
    assert isinstance(body["auth_failed"], bool)
    assert isinstance(body["last_update_unix"], int)
    assert isinstance(body["server_now_unix"], int)


def test_status_auth_failed_flag(client):
    state.snapshot.stale = True
    state.snapshot.auth_failed = True
    r = client.get("/status")
    body = r.json()
    assert body["stale"] is True
    assert body["auth_failed"] is True


def test_history_shape(client):
    r = client.get("/history?hours=24")
    assert r.status_code == 200
    body = r.json()
    assert "buckets" in body
    assert len(body["buckets"]) == 24
    assert "server_now_unix" in body

    for bucket in body["buckets"]:
        assert "hour_unix" in bucket
        assert "five_hour_peak" in bucket


def test_history_empty_buckets_are_null(client):
    r = client.get("/history?hours=2")
    body = r.json()
    # At least the bucket before last hour should be null (no data inserted there)
    peaks = [b["five_hour_peak"] for b in body["buckets"]]
    assert None in peaks


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
