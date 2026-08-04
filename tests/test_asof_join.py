"""As-of join invariant (CLAUDE rule 2): no feature reads data timestamped after
its forecast time. The bronze store's asof_latest must never leak the future.
"""
from datetime import datetime, timedelta, timezone

import pytest

from tbay_fishcast.ingest.lsofs_extract import TempRow
from tbay_fishcast.storage.parquet_store import (
    asof_latest, read_bronze, rows_to_frame, write_bronze,
)


def _row(t, temp, station="silver_harbour_outer", depth=6.0):
    return TempRow(
        station_id=station, node=29434, depth_m=depth, temp_c=temp,
        valid_time=t, water_column_m=12.0, clamped=False,
    )


def _series(base):
    return [_row(base + timedelta(hours=h), 15.0 - h) for h in range(6)]


def test_asof_returns_latest_not_future(tmp_path):
    base = datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)
    p = write_bronze(_series(base), tmp_path / "bronze.parquet")

    # as_of at +2h30m: latest available is the +2h record (temp 13.0), NOT +3h.
    v = asof_latest(p, "silver_harbour_outer", 6.0, base + timedelta(hours=2, minutes=30))
    assert v == pytest.approx(13.0)


def test_asof_before_any_data_is_none(tmp_path):
    base = datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)
    p = write_bronze(_series(base), tmp_path / "bronze.parquet")
    assert asof_latest(p, "silver_harbour_outer", 6.0, base - timedelta(hours=1)) is None


def test_asof_exact_timestamp_inclusive(tmp_path):
    base = datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)
    p = write_bronze(_series(base), tmp_path / "bronze.parquet")
    # exactly +3h -> that record (temp 12.0) is allowed (<=)
    v = asof_latest(p, "silver_harbour_outer", 6.0, base + timedelta(hours=3))
    assert v == pytest.approx(12.0)


def test_naive_as_of_treated_as_utc(tmp_path):
    base = datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)
    p = write_bronze(_series(base), tmp_path / "bronze.parquet")
    naive = datetime(2026, 8, 3, 2, 30)  # no tzinfo
    assert asof_latest(p, "silver_harbour_outer", 6.0, naive) == pytest.approx(13.0)


def test_roundtrip_preserves_utc(tmp_path):
    base = datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)
    p = write_bronze(_series(base), tmp_path / "bronze.parquet")
    df = read_bronze(p)
    assert str(df["valid_time"].dt.tz) == "UTC"
    assert len(df) == 6
