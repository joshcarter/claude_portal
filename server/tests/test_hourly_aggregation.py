import time
import tempfile

from src.store import Store


def make_store():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return Store(f.name)


def test_hourly_peaks_correct_max():
    store = make_store()
    now = int(time.time())
    hour = (now // 3600) * 3600

    store.insert(hour + 100, 10.0, 0.0, None)
    store.insert(hour + 200, 40.0, 0.0, None)
    store.insert(hour + 300, 25.0, 0.0, None)

    buckets = store.hourly_peaks(1)
    assert len(buckets) == 1
    assert buckets[0]["five_hour_peak"] == 40.0


def test_hourly_peaks_empty_bucket_is_null():
    store = make_store()
    now = int(time.time())
    hour = (now // 3600) * 3600

    # Only insert into current hour, not the one before
    store.insert(hour + 100, 30.0, 0.0, None)

    buckets = store.hourly_peaks(2)
    assert len(buckets) == 2
    assert buckets[0]["five_hour_peak"] is None  # previous hour has no data
    assert buckets[1]["five_hour_peak"] == 30.0


def test_hourly_peaks_sorted_oldest_first():
    store = make_store()
    now = int(time.time())
    hour = (now // 3600) * 3600

    store.insert(hour - 7200 + 100, 15.0, 0.0, None)
    store.insert(hour - 3600 + 100, 25.0, 0.0, None)
    store.insert(hour + 100,        35.0, 0.0, None)

    buckets = store.hourly_peaks(3)
    assert len(buckets) == 3
    assert buckets[0]["five_hour_peak"] == 15.0
    assert buckets[1]["five_hour_peak"] == 25.0
    assert buckets[2]["five_hour_peak"] == 35.0
    assert buckets[0]["hour_unix"] < buckets[1]["hour_unix"] < buckets[2]["hour_unix"]
