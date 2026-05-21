import tempfile
import time

from src.poller import _compute_five_hour_burn
from src.store import Store


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

    assert _compute_five_hour_burn(store) > 0


def test_burn_rate_decaying():
    store = make_store()
    now = int(time.time())
    store.insert(now - 1800, 60.0, 70.0, None)
    store.insert(now,        30.0, 50.0, None)

    assert _compute_five_hour_burn(store) == 0.0


def test_burn_rate_flat():
    store = make_store()
    now = int(time.time())
    store.insert(now - 1800, 50.0, 50.0, None)
    store.insert(now,        50.0, 50.0, None)

    assert _compute_five_hour_burn(store) == 0.0


def test_burn_rate_insufficient_data():
    store = make_store()
    now = int(time.time())
    store.insert(now, 40.0, 50.0, None)

    assert _compute_five_hour_burn(store) == 0.0
