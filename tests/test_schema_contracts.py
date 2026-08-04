"""Schema contracts on the bronze temperature frame (CLAUDE rule 2: schema
contracts on bronze). Shape, dtypes, UTC-awareness, and config integrity.
"""
from datetime import datetime, timezone

import pandas as pd

from tbay_fishcast.config import knowledge_pack_version, load_config
from tbay_fishcast.ingest.lsofs_extract import TempRow
from tbay_fishcast.storage.parquet_store import BRONZE_COLUMNS, rows_to_frame


def _rows():
    t = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
    return [
        TempRow("marina_east_mcvicar", 29138, 6.0, 13.964, t, 16.7, False),
        TempRow("marina_east_mcvicar", 29138, 10.0, 12.366, t, 16.7, False),
    ]


def test_bronze_columns_exact():
    df = rows_to_frame(_rows())
    assert list(df.columns) == BRONZE_COLUMNS


def test_bronze_valid_time_is_utc_datetime():
    df = rows_to_frame(_rows())
    assert pd.api.types.is_datetime64_any_dtype(df["valid_time"])
    assert str(df["valid_time"].dt.tz) == "UTC"


def test_bronze_dtypes():
    df = rows_to_frame(_rows())
    assert pd.api.types.is_float_dtype(df["temp_c"])
    assert pd.api.types.is_float_dtype(df["depth_m"])
    assert pd.api.types.is_integer_dtype(df["node"])
    assert pd.api.types.is_bool_dtype(df["clamped"])


def test_empty_rows_still_has_schema():
    df = rows_to_frame([])
    assert list(df.columns) == BRONZE_COLUMNS
    assert len(df) == 0


# --- config / station-registry contract ---

def test_station_split_shore_vs_river():
    cfg = load_config()
    shore = {s.id for s in cfg.shore_stations}
    assert shore == {"silver_harbour_outer", "mackenzie_point", "marina_east_mcvicar"}
    # river/warmwater stations must have no LSOFS node (by design)
    for s in cfg.stations:
        if not s.lsofs_shore:
            assert s.lsofs_node is None


def test_shore_stations_are_bootstrapped():
    cfg = load_config()
    for s in cfg.shore_stations:
        assert s.has_lsofs, f"{s.id} missing pinned lsofs_node"
        assert s.node_depth_m >= 10.0


def test_knowledge_pack_version_deterministic():
    v1 = knowledge_pack_version()
    v2 = knowledge_pack_version()
    assert v1 == v2 and len(v1) == 12
