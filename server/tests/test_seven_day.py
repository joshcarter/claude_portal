import tempfile
import time

from src.poller import (
    MAX_REDLINE_RATIO,
    _compute_seven_day_burn,
    _redline,
)
from src.store import Store


def make_store():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return Store(f.name)


def test_seven_day_burn_positive():
    store = make_store()
    now = int(time.time())
    store.insert(now - 3 * 3600, 50.0, 60.0, None)
    store.insert(now - 2 * 3600, 52.0, 62.0, None)
    store.insert(now - 1 * 3600, 54.0, 64.0, None)
    store.insert(now,            56.0, 66.0, None)

    rate = _compute_seven_day_burn(store)
    assert abs(rate - 2.0) < 1e-6  # 60 -> 66 over 3h


def test_seven_day_burn_decaying():
    store = make_store()
    now = int(time.time())
    store.insert(now - 3 * 3600, 70.0, 80.0, None)
    store.insert(now,            70.0, 60.0, None)

    assert _compute_seven_day_burn(store) == 0.0


def test_seven_day_burn_baseline_too_short():
    # 30-minute span is below the 1-hour minimum baseline — too noisy to trust.
    store = make_store()
    now = int(time.time())
    store.insert(now - 1800, 50.0, 60.0, None)
    store.insert(now,        50.0, 70.0, None)

    assert _compute_seven_day_burn(store) == 0.0


def test_seven_day_burn_insufficient_data():
    store = make_store()
    now = int(time.time())
    store.insert(now, 50.0, 60.0, None)

    assert _compute_seven_day_burn(store) == 0.0


def test_redline_normal():
    now = int(time.time())
    resets_at = now + 48 * 3600
    # sustainable = (100 - 76) / 48 = 0.5 %/hr
    sustainable, ratio = _redline(76.0, resets_at, burn=1.0, now=now)
    assert sustainable is not None and ratio is not None
    assert abs(sustainable - 0.5) < 1e-6
    assert abs(ratio - 2.0) < 1e-6


def test_redline_idle_when_burn_zero():
    now = int(time.time())
    sustainable, ratio = _redline(76.0, now + 48 * 3600, burn=0.0, now=now)
    assert sustainable is not None and sustainable > 0
    assert ratio == 0.0


def test_redline_none_when_no_reset_time():
    now = int(time.time())
    assert _redline(50.0, None, burn=1.0, now=now) == (None, None)


def test_redline_none_when_reset_in_past():
    now = int(time.time())
    assert _redline(50.0, now - 3600, burn=1.0, now=now) == (None, None)


def test_redline_pinned_when_window_full():
    now = int(time.time())
    sustainable, ratio = _redline(100.0, now + 48 * 3600, burn=1.0, now=now)
    assert sustainable == 0.0
    assert ratio == MAX_REDLINE_RATIO


def test_redline_ratio_capped():
    now = int(time.time())
    # tiny headroom, huge burn -> ratio would explode; must clamp
    _, ratio = _redline(99.9, now + 48 * 3600, burn=50.0, now=now)
    assert ratio == MAX_REDLINE_RATIO
