import time
import tempfile

from src.store import Store
from src.poller import _compute_burn_rate


def make_store():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return Store(f.name)


def test_burn_rate_positive():
    store = make_store()
    now = int(time.time())
    store.insert(now - 1800, 20.0, 50.0, None)
    store.insert(now - 900,  30.0, 55.0, None)
    store.insert(now,        40.0, 60.0, None)

    rate, projected = _compute_burn_rate(store)
    assert rate > 0
    assert projected is not None
    assert projected > now


def test_burn_rate_decaying():
    store = make_store()
    now = int(time.time())
    store.insert(now - 1800, 60.0, 70.0, None)
    store.insert(now,        30.0, 50.0, None)

    rate, projected = _compute_burn_rate(store)
    assert rate == 0.0
    assert projected is None


def test_burn_rate_flat():
    store = make_store()
    now = int(time.time())
    store.insert(now - 1800, 50.0, 50.0, None)
    store.insert(now,        50.0, 50.0, None)

    rate, projected = _compute_burn_rate(store)
    assert rate == 0.0
    assert projected is None


def test_burn_rate_insufficient_data():
    store = make_store()
    now = int(time.time())
    store.insert(now, 40.0, 50.0, None)

    rate, projected = _compute_burn_rate(store)
    assert rate == 0.0
    assert projected is None


def test_burn_rate_caps_projection_at_24h():
    store = make_store()
    now = int(time.time())
    # 1% over 30 min = 2%/hr; at 1% current → 49.5h to full — should cap at 24h
    store.insert(now - 1800, 0.0, 0.0, None)
    store.insert(now,        1.0, 1.0, None)

    rate, projected = _compute_burn_rate(store)
    assert rate > 0
    assert projected is not None
    assert projected <= now + 24 * 3600 + 5
