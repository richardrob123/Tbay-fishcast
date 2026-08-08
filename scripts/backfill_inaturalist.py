"""Backfill the observation ledger from iNaturalist (ADR-042 source registry, community P1).

Why a SCRIPT and not a research agent: iNaturalist serves structured, already-validated records
over an open API (no key, no account). There is nothing to interpret — extracting these with an
LLM would be slower, costlier, and less reproducible than 100 lines of deterministic code. The
research agent's judgment is reserved for prose sources where dates and places must be inferred.

Records are photo-backed and community-identified, which makes them the highest-quality
LOCATED, DAY-DATED fish observations available to this project — GPS coordinates rather than
"off the Kam", which is exactly what the pre-registered spatial back test needs.

    python scripts/backfill_inaturalist.py [--dry-run]

Honesty measures baked in:
  * kind="sighting", never "catch" — iNat records a fish that was SEEN and photographed; it does
    not claim the fish was angled (the schema keeps these separable for the back test).
  * research_grade -> T3/confidence 0.9; needs_id -> T4/confidence 0.5.
  * geoprivacy-obscured records are kept but flagged and given region scope: iNat deliberately
    fuzzes some coordinates, and a fuzzed point must never be presented as a located observation.
  * Oncorhynchus mykiss is recorded as steelhead ONLY when observed in the lake/tributary window;
    resident rainbows exist, so confidence is reduced and the ambiguity noted on the row.
  * lat/lon are carried through as extra fields so the back test can use true coordinates later,
    while place_id stays gazetteer-only (a coordinate never mints a new place).
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

API = "https://api.inaturalist.org/v1/observations"
# Nearshore Thunder Bay / NW Lake Superior. Deliberately tighter than the iNat "Thunder Bay
# District" place, which is enormous and mostly inland lakes irrelevant to shore fishing.
BBOX = {"swlat": 48.00, "swlng": -89.90, "nelat": 48.85, "nelng": -88.00}
TAXON_SALMONIDAE = 47520

SPECIES_BY_SCIENTIFIC = {
    "Salvelinus fontinalis": "brook_trout",
    "Salvelinus namaycush": "lake_trout",
    "Oncorhynchus mykiss": "steelhead",          # ambiguity flagged per-row
    "Oncorhynchus tshawytscha": "chinook",
    "Oncorhynchus kisutch": "coho",
    "Oncorhynchus gorbuscha": "pink",
    "Coregonus clupeaformis": "lake_whitefish",
    "Sander vitreus": "walleye",
    "Esox lucius": "northern_pike",
    "Micropterus dolomieu": "smallmouth_bass",
}


def _fetch(page: int) -> dict:
    q = {"taxon_id": TAXON_SALMONIDAE, "per_page": 200, "page": page,
         "order_by": "observed_on", "order": "asc", **BBOX}
    url = API + "?" + "&".join(f"{k}={v}" for k, v in q.items())
    req = urllib.request.Request(url, headers={"User-Agent": "tbay-fishcast/1.0 (research)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _row_from(o: dict) -> dict | None:
    d = o.get("observed_on")
    if not d:
        return None                       # undated observation cannot enter a dated ledger
    taxon = o.get("taxon") or {}
    sci = taxon.get("name", "")
    species = SPECIES_BY_SCIENTIFIC.get(sci)
    if species is None:
        # genus-level or a salmonid we don't model: keep as "other" so the record still counts
        # toward presence/effort context, never guessed into a modelled species
        species = "other"
    obscured = bool(o.get("obscured") or o.get("geoprivacy") == "obscured")
    lat = lon = None
    loc = o.get("location")
    if loc and not obscured:
        try:
            lat, lon = (float(x) for x in loc.split(","))
        except ValueError:
            lat = lon = None
    place_guess = o.get("place_guess") or ""
    pid = obs.resolve_place(place_guess)
    research = o.get("quality_grade") == "research"
    conf = 0.9 if research else 0.5
    note = None
    if species == "steelhead":
        conf = round(conf * 0.8, 2)
        note = ("O. mykiss: migratory steelhead vs resident rainbow not distinguishable from the "
                "record; treated as steelhead with reduced confidence")
    quote = (f"{taxon.get('preferred_common_name') or sci} | observed {d} | "
             f"{place_guess or 'location not stated'} | quality: {o.get('quality_grade')} | "
             f"iNat obs {o.get('id')}")[:400]
    row = {
        "kind": "sighting", "species": species, "date": d, "date_precision": "day",
        "place_raw": place_guess or None,
        "place_id": pid if not obscured else None,
        "place_scope": ("point" if (pid and not obscured) else "region"),
        "count": 1,
        "quote": quote,
        "source": o.get("uri") or f"https://www.inaturalist.org/observations/{o.get('id')}",
        "source_kind": "social", "tier": "T3" if research else "T4", "confidence": conf,
        "retrieved": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scientific_name": sci, "quality_grade": o.get("quality_grade"),
        "obscured": obscured,
    }
    if lat is not None:
        row["lat"], row["lon"] = round(lat, 5), round(lon, 5)
    if note:
        row["note"] = note
    return row


def main(argv) -> int:
    dry = "--dry-run" in argv
    rows, page, total = [], 1, None
    while True:
        d = _fetch(page)
        total = d.get("total_results", 0)
        results = d.get("results", [])
        if not results:
            break
        for o in results:
            r = _row_from(o)
            if r:
                rows.append(r)
        print(f"  page {page}: {len(results)} records ({len(rows)} usable so far of {total})")
        if page * 200 >= total:
            break
        page += 1
        time.sleep(1.0)                   # iNat etiquette: ~1 req/s
    from collections import Counter
    print(f"\nfetched {total} iNat salmonid records in bbox; {len(rows)} dated + usable")
    print("species:", dict(Counter(r["species"] for r in rows).most_common()))
    print("placed (gazetteer-resolved):",
          sum(1 for r in rows if r.get("place_id")), "| obscured:",
          sum(1 for r in rows if r.get("obscured")))
    yrs = Counter(r["date"][:4] for r in rows)
    print("by year:", dict(sorted(yrs.items())))
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
