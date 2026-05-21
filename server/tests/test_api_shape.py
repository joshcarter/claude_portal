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
    state.snapshot.five_hour_burn_rate = 8.3
    state.snapshot.five_hour_sustainable_rate = 13.2
    state.snapshot.five_hour_redline_ratio = 0.63
    state.snapshot.seven_day_pct = 72.0
    state.snapshot.seven_day_resets_at = 1745640000
    state.snapshot.seven_day_burn_rate = 1.2
    state.snapshot.seven_day_sustainable_rate = 0.5
    state.snapshot.seven_day_redline_ratio = 2.4
    state.snapshot.seven_day_opus_pct = 93.5
    state.snapshot.seven_day_opus_resets_at = 1745812800
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
    assert body["five_hour"]["used_pct"] == 34.0
    assert "resets_at_unix" in body["five_hour"]
    assert body["five_hour"]["burn_rate_pct_per_hour"] == 8.3
    assert body["five_hour"]["sustainable_pct_per_hour"] == 13.2
    assert body["five_hour"]["redline_ratio"] == 0.63

    assert "seven_day" in body
    assert body["seven_day"]["burn_rate_pct_per_hour"] == 1.2
    assert body["seven_day"]["sustainable_pct_per_hour"] == 0.5
    assert body["seven_day"]["redline_ratio"] == 2.4
    assert "seven_day_opus" in body

    # 5H and 7D carry the identical field set; nothing window-specific at top level
    assert set(body["five_hour"]) == set(body["seven_day"])
    assert "burn_rate_pct_per_hour" not in body
    assert "projected_full_at_unix" not in body

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
    # The two fixture samples span ~30 min, so they fill at most 2 adjacent
    # hour-buckets — with 4 buckets at least one is guaranteed empty regardless
    # of what minute of the hour the test runs at.
    r = client.get("/history?hours=4")
    body = r.json()
    peaks = [b["five_hour_peak"] for b in body["buckets"]]
    assert None in peaks


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
