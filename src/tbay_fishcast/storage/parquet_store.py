"""Bronze temperature store — parquet on disk, DuckDB for as-of queries (ADR-003).

The LSOFS backfill writes tidy TempRow records here. Times are stored UTC
(ADR-014). The bronze table is append-only; idempotency is the caller's job
(re-running a cycle overwrites its partition).
"""
from __future__ import annotations

from datetime import timezone
from pathlib import Path

import duckdb
import pandas as pd

from ..ingest.lsofs_extract import TempRow

BRONZE_COLUMNS = [
    "station_id", "node", "depth_m", "temp_c",
    "valid_time", "water_column_m", "clamped",
]


def rows_to_frame(rows: list[TempRow]) -> pd.DataFrame:
    df = pd.DataFrame(
        [
            {
                "station_id": r.station_id,
                "node": r.node,
                "depth_m": r.depth_m,
                "temp_c": r.temp_c,
                "valid_time": r.valid_time,
                "water_column_m": r.water_column_m,
                "clamped": r.clamped,
            }
            for r in rows
        ],
        columns=BRONZE_COLUMNS,
    )
    if not df.empty:
        # Store UTC, tz-aware -> pandas UTC datetime64 (ADR-014).
        df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)
    return df


def write_bronze(rows: list[TempRow], path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_parquet(path, index=False)
    return path


def read_bronze(path: Path | str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "valid_time" in df and not df.empty:
        df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)
    return df


def asof_latest(
    path: Path | str,
    station_id: str,
    depth_m: float,
    as_of,
) -> float | None:
    """Temperature at (station, depth) as known AT `as_of` — the newest record
    whose valid_time <= as_of. This is the as-of join that enforces CLAUDE rule 2:
    "no feature reads data timestamped after its forecast time." Returns None if
    nothing is available by then.
    """
    as_of = pd.Timestamp(as_of)
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize(timezone.utc)
    con = duckdb.connect()
    try:
        q = """
            SELECT temp_c
            FROM read_parquet($p)
            WHERE station_id = $s AND depth_m = $d AND valid_time <= $t
            ORDER BY valid_time DESC
            LIMIT 1
        """
        res = con.execute(
            q, {"p": str(path), "s": station_id, "d": float(depth_m),
                "t": as_of.to_pydatetime()}
        ).fetchone()
        return None if res is None else float(res[0])
    finally:
        con.close()
