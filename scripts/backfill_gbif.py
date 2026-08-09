"""Backfill the observation ledger from GBIF (ADR-042 source registry, community P2).

GBIF aggregates museum, agency and citizen-science occurrence records under one open API. For
this project the prize is the USGS Great Lakes Science Center RVCAT research-vessel trawl series
— standardized, dated, located survey catches reaching back to the 1950s-70s, which is real
HISTORICAL DEPTH that no amount of forum mining could supply.

Deterministic script, not a research agent: GBIF serves structured records, so there is nothing
to interpret (ADR-001 keeps the LLM for prose sources).

    python scripts/backfill_gbif.py [--dry-run]

Honesty measures:
  * kind="sighting" for every record — an occurrence documents that a fish WAS THERE on a date;
    it is not a claim that it was angled. basisOfRecord + dataset are carried on the row so the
    back test can weight a research trawl differently from a museum lot.
  * iNat records inside GBIF are SKIPPED when the same observation is already in the ledger from
    the direct iNat backfill (GBIF mirrors iNaturalist; double-counting would inflate every n).
  * Records without a usable date, or with only a year, get honest date_precision — never a
    fabricated day.
  * Coordinates travel as extra fields; place_id stays gazetteer-only (coordinates never mint
    a place).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tbay_fishcast.knowledge import observations as obs  # noqa: E402

API = "https://api.gbif.org/v1/occurrence/search"
FAMILY_SALMONIDAE = 8615
BBOX = {"decimalLatitude": "48.0,49.2", "decimalLongitude": "-89.9,-88.0"}
PAGE = 300

SPECIES_BY_SCIENTIFIC = {
    "Salvelinus fontinalis": "brook_trout",
    "Salvelinus namaycush": "lake_trout",
    "Oncorhynchus mykiss": "steelhead",
    "Oncorhynchus tshawytscha": "chinook",
    "Oncorhynchus kisutch": "coho",
    "Oncorhynchus gorbuscha": "pink",
    "Coregonus clupeaformis": "lake_whitefish",
}


def _fetch(offset: int) -> dict:
    q = {"familyKey": FAMILY_SALMONIDAE, "limit": PAGE, "offset": offset, **BBOX}
    url = API + "?" + "&".join(f"{k}={v}" for k, v in q.items())
    req = urllib.request.Request(url, headers={"User-Agent": "tbay-fishcast/1.0 (research)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def _row_from(o: dict, seen_inat: set) -> dict | None:
    # dedupe against the direct iNaturalist backfill
    occ = str(o.get("occurrenceID") or "")
    if "inaturalist.org" in occ:
        oid = occ.rstrip("/").split("/")[-1]
        if oid in seen_inat:
            return None
    y, m, d = o.get("year"), o.get("month"), o.get("day")
    if not y:
        return None
    if m and d:
        date_s, prec = f"{y:04d}-{m:02d}-{d:02d}", "day"
    elif m:
        date_s, prec = f"{y:04d}-{m:02d}-01", "month"
    else:
        date_s, prec = f"{y:04d}-01-01", "year"
    if y < 1950:
        return None                       # schema floor; older museum lots are out of scope
    sci = o.get("species") or o.get("scientificName") or ""
    species = SPECIES_BY_SCIENTIFIC.get(sci.split(" (")[0].strip(), "other")
    locality = o.get("locality") or o.get("waterBody") or ""
    pid = obs.resolve_place(locality)
    basis = o.get("basisOfRecord", "")
    dataset = o.get("datasetName") or o.get("datasetKey", "")
    # agency/museum records are primary data; anything else stays T3
    tier = "T1" if basis in ("PRESERVED_SPECIMEN", "MATERIAL_SAMPLE", "MACHINE_OBSERVATION") \
        or "USGS" in str(dataset).upper() else "T3"
    quote = (f"{sci or 'Salmonidae'} | {date_s} | {locality or 'locality not stated'} | "
             f"basis: {basis} | dataset: {dataset} | gbif {o.get('key')}")[:400]
    row = {
        "kind": "sighting", "species": species, "date": date_s, "date_precision": prec,
        "place_raw": locality or None, "place_id": pid,
        "place_scope": "point" if pid else "region",
        "count": o.get("individualCount") or 1,
        "quote": quote,
        "source": f"https://www.gbif.org/occurrence/{o.get('key')}",
        "source_kind": "dataset", "tier": tier,
        "confidence": 0.9 if tier == "T1" else 0.6,
        "retrieved": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scientific_name": sci, "basis_of_record": basis, "dataset": str(dataset)[:120],
    }
    if o.get("decimalLatitude") is not None:
        row["lat"] = round(float(o["decimalLatitude"]), 5)
        row["lon"] = round(float(o["decimalLongitude"]), 5)
    # CURATION (stated rule, not a silent drop): a year-precision record of an unidentified
    # salmonid answers none of this project's questions — it cannot time a run (no day) and
    # cannot inform a species layer (no species). Keeping ~1,000 of them would pad the ledger's
    # row count while adding zero signal, and every future "n=" would flatter itself. Keep a
    # record if it has usable timing OR a usable species; drop it if it has neither.
    if prec == "year" and species == "other":
        return None
    return row


def main(argv) -> int:
    dry = "--dry-run" in argv
    seen_inat = {r["source"].rstrip("/").split("/")[-1]
                 for r in obs.load() if "inaturalist.org" in r.get("source", "")}
    print(f"{len(seen_inat)} iNat observations already in the ledger — will dedupe against them")
    rows, offset, total = [], 0, None
    while True:
        d = _fetch(offset)
        total = d.get("count", 0)
        results = d.get("results", [])
        if not results:
            break
        for o in results:
            r = _row_from(o, seen_inat)
            if r:
                rows.append(r)
        print(f"  offset {offset}: {len(results)} records ({len(rows)} usable of {total})")
        offset += PAGE
        if offset >= total or d.get("endOfRecords"):
            break
        time.sleep(0.5)
    from collections import Counter
    print(f"\nGBIF salmonid occurrences in bbox: {total}; usable after dedupe/date filter: {len(rows)}")
    print("species:", dict(Counter(r["species"] for r in rows).most_common()))
    print("basis:", dict(Counter(r["basis_of_record"] for r in rows).most_common(6)))
    print("precision:", dict(Counter(r["date_precision"] for r in rows)))
    yrs = sorted(r["date"][:4] for r in rows)
    if yrs:
        print(f"years: {yrs[0]}..{yrs[-1]}")
    if dry:
        return 0
    out = obs.append_rows(rows)
    print(f"\nledger: +{out['added']} added, {out['skipped_dupe']} dupes, "
          f"{len(out['rejected'])} rejected")
    for r, errs in out["rejected"][:5]:
        print("  rejected:", r.get("date"), r.get("species"), errs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
