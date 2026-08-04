"""LSOFS hindcast backfill runner (PLAN Phase 0 task 2).

Concurrent fetch of nowcast station-temps over a date range, written as
date-partitioned bronze parquet. LSOFS-only — the one allowlisted source — so this
runs today without the blocked reference hosts.

    python scripts/backfill_lsofs.py 2025-06-01 2025-06-30 --out data/bronze --workers 16

Honors ADR-018 (archive starts 2024-03; refuses earlier silently-never — it warns
and clips loudly). No silent caps: every failed file is logged and counted.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.ingest.backfill import (  # noqa: E402
    BackfillItem, extract_item, iter_nowcast_items, station_node_map,
)
from tbay_fishcast.storage.parquet_store import write_bronze  # noqa: E402


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("start", type=lambda s: datetime.fromisoformat(s).date())
    ap.add_argument("end", type=lambda s: datetime.fromisoformat(s).date())
    ap.add_argument("--out", default="data/bronze", type=Path)
    ap.add_argument("--workers", default=16, type=int)
    args = ap.parse_args()

    cfg = load_config()
    nodes = station_node_map(cfg)
    if not nodes:
        print("no bootstrapped shore stations — run scripts/bootstrap_grid.py", file=sys.stderr)
        return 2

    archive_start = datetime.fromisoformat(cfg.lsofs.archive_starts).date()
    start = args.start
    if start < archive_start:
        print(f"WARNING: {start} precedes LSOFS archive start {archive_start} "
              f"(ADR-018) — clipping to {archive_start}", file=sys.stderr)
        start = archive_start

    days = list(_daterange(start, args.end))
    items = list(iter_nowcast_items(days, cfg))
    split = cfg.temporal_split
    print(f"backfill {start}..{args.end}: {len(days)} days, {len(items)} files, "
          f"{args.workers} workers -> {args.out}")

    ok = fail = 0
    by_day: dict[date, list] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(extract_item, cfg, it, nodes): it for it in items}
        for fut in as_completed(futs):
            it: BackfillItem = futs[fut]
            try:
                rows = fut.result()
            except Exception as e:  # noqa: BLE001 — log-and-continue, never silent
                fail += 1
                print(f"  FAIL {it.day} {it.cycle} n{it.hour:03d}: {type(e).__name__}: {e}",
                      file=sys.stderr)
                continue
            ok += 1
            by_day.setdefault(it.day, []).extend(rows)

    written = 0
    for d, rows in sorted(by_day.items()):
        split_tag = split.assign(d) or "unassigned"
        out = args.out / f"split={split_tag}" / f"date={d.isoformat()}" / "temps.parquet"
        write_bronze(rows, out)
        written += len(rows)

    print(f"done: {ok} files ok, {fail} failed, {written} rows across {len(by_day)} days")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
