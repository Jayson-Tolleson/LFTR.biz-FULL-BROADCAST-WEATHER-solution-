from datetime import datetime, timezone

from server.gfs.providers.adapters import _iso_time_or_last
from server.gfs.serializers import iso_utc


def test_iso_utc_normalizes_naive_and_aware_values():
    naive = datetime(2026, 3, 13, 0, 0, 0)
    aware = datetime(2026, 3, 13, 0, 0, 0, tzinfo=timezone.utc)
    assert iso_utc(naive) == "2026-03-13T00:00:00Z"
    assert iso_utc(aware) == "2026-03-13T00:00:00Z"


def test_adapter_time_selector_uses_valid_utc_iso8601():
    aware = datetime(2026, 3, 13, 0, 0, 0, tzinfo=timezone.utc)
    assert _iso_time_or_last(aware) == "2026-03-13T00:00:00Z"
    assert _iso_time_or_last(None) == "last"
