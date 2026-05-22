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


def test_burn_rate_insufficient_span():
    # Two samples a minute apart fall below the 10-minute span guard.
    store = make_store()
    now = int(time.time())
    store.insert(now - 60, 40.0, 50.0, None)
    store.insert(now,      41.0, 51.0, None)

    assert _compute_five_hour_burn(store) == 0.0


def test_burn_rate_regression_uses_all_samples():
    # A clean 12 %/hr ramp sampled every minute for 30 minutes.
    store = make_store()
    now = int(time.time())
    for i in range(31):
        store.insert(now - 1800 + i * 60, 0.2 * i, 50.0, None)

    assert abs(_compute_five_hour_burn(store) - 12.0) < 0.5


def test_burn_rate_regression_resists_endpoint_noise():
    # A flat series with a single high final reading. A two-point slope would
    # report 6 %/hr (3% over 0.5h); the regression keeps it near zero.
    store = make_store()
    now = int(time.time())
    for i in range(30):
        store.insert(now - 1800 + i * 60, 50.0, 50.0, None)
    store.insert(now, 53.0, 50.0, None)

    assert _compute_five_hour_burn(store) < 2.0
