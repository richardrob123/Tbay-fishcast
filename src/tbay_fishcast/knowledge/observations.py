"""Observation ledger — the schema-validated, append-only record of researched fish observations.

ADR-042: the research agent (LLM, outside the heartbeat) extracts dated fish observations —
catches, run sightings, stocking events, trap counts, derby results — from ToS-respecting public
sources. THIS module is the deterministic gate those claims must pass to exist: schema validation,
gazetteer-only place resolution (the agent can never mint new water — the Kakabeka lesson), and
content-hash dedup. The deterministic build and the pre-registered back test consume ONLY rows
that passed this gate. No I/O to the network here; no LLM anywhere in this module (ADR-001).

Ledger file: knowledge/observations/observations.jsonl — one JSON object per line, sorted by
(date, id) on every append, append-only in spirit (rows are never edited, only added).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
LEDGER = _ROOT / "knowledge" / "observations" / "observations.jsonl"
CONFIRMATIONS = _ROOT / "knowledge" / "observations" / "confirmations.json"

# "sighting" = a fish OBSERVED and evidenced (photo-verified citizen science) without a claim
# that it was angled. Kept distinct from "catch" so the back test can weight them differently:
# a catch implies successful angling at that spot; a sighting only implies the fish was there.
KINDS = {"catch", "sighting", "run_status", "stocking", "trap_count", "derby_result"}
# regs/closure/access claims are NOT a kind on purpose: legality is never research-ingested
# (rule 4 — T1 or field-verified only, via human review).
SPECIES = {"lake_trout", "brook_trout", "chinook", "coho", "pink", "steelhead",
           "walleye", "northern_pike", "lake_whitefish", "smallmouth_bass", "other"}
RUN_STATES = {"staging", "in_river", "dropback", None}
SOURCE_KINDS = {"agency", "dataset", "news", "derby", "forum", "social"}
TIERS = {"T1", "T2", "T3", "T4"}
DATE_PRECISIONS = {"day", "week", "month", "year"}
# place_scope: how precisely the observation is located. LOCATIONLESS reports are still recorded
# (operator 2026-08-08: "100 salmon in a week" with no spot still says the runs are ON) — they
# carry scope="region" and drive REGION-level inference, never a specific-spot claim.
PLACE_SCOPES = {"point", "tributary", "region"}
# species whose regional reports imply tributary run activity (the migratory set)
MIGRATORY = {"chinook", "coho", "pink", "steelhead"}

# GAZETTEER: the only place names an observation may resolve to — our covered stretches and
# river mouths plus their common aliases. An unknown place stays place_id=None (kept for the
# record, excluded from every place-keyed analysis); the agent NEVER adds entries here.
GAZETTEER = {
    # river mouths (marker ids)
    "kam": "kam", "kaministiquia": "kam", "kam river": "kam", "kam mouth": "kam",
    "mission": "kam", "mckellar river": "kam",
    "current river": "current", "current": "current", "trowbridge": "current",
    "neebing": "neebing", "mcintyre": "neebing", "floodway": "neebing",
    # stretches (build ids)
    "silver islet": "silver_islet", "sleeping giant": "silver_islet",
    "mckellar": "mckellar_harbour", "north harbour": "mckellar_harbour",
    "marina": "marina_mcvicar", "marina park": "marina_mcvicar", "mcvicar": "marina_mcvicar",
    "pier 3": "marina_mcvicar",
    "chippewa": "chippewa", "sturgeon river": "chippewa", "sturgeon bay": "chippewa",
    "mission island": "kam_mission", "boulevard": "boulevard_current",
    "bare point": "current_barepoint", "shipyard": "shipyard_mackenzie",
    "mackenzie": "mackenzie_silver", "little trout bay": "little_trout_bay",
    "silver harbour": "mackenzie_silver",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_place(raw: str | None) -> str | None:
    """Longest-alias match against the gazetteer, case-insensitive. None if unknown — an
    unresolved place is a RECORDED fact, not an invitation to invent one."""
    if not raw:
        return None
    t = raw.lower().strip()
    best = None
    for alias, pid in GAZETTEER.items():
        if alias in t and (best is None or len(alias) > best[0]):
            best = (len(alias), pid)
    return best[1] if best else None


def row_id(row: dict) -> str:
    """Content-hash dedup key: same source+date+species+kind+place = same observation."""
    basis = "|".join(str(row.get(k, "")) for k in
                     ("source", "date", "species", "kind", "place_raw", "count"))
    # Structured datasets can emit many rows sharing all of the above (one trawl tow, several
    # life stages, species collapsed into "other") — those collided and were silently deduped.
    # Appended ONLY when present, so every hash computed before this existed is unchanged.
    ext = row.get("external_id")
    if ext:
        basis += "|" + str(ext)
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def validate_row(row: dict) -> list[str]:
    """Return a list of violations (empty = valid). Pure; never mutates."""
    errs = []
    if row.get("kind") not in KINDS:
        errs.append(f"kind {row.get('kind')!r} not in {sorted(KINDS)}")
    if row.get("species") not in SPECIES:
        errs.append(f"species {row.get('species')!r} unknown")
    d = row.get("date", "")
    if not (isinstance(d, str) and _DATE_RE.match(d)):
        errs.append(f"date {d!r} not YYYY-MM-DD")
    else:
        try:
            dt = date.fromisoformat(d)
            if dt.year < 1950 or dt > datetime.now(timezone.utc).date():
                errs.append(f"date {d} out of plausible range")
        except ValueError:
            errs.append(f"date {d!r} invalid")
    if row.get("date_precision", "day") not in DATE_PRECISIONS:
        errs.append(f"date_precision {row.get('date_precision')!r} invalid")
    if row.get("kind") == "run_status" and row.get("state") not in RUN_STATES:
        errs.append(f"run state {row.get('state')!r} invalid")
    if not row.get("source", "").startswith(("http://", "https://")):
        errs.append("source must be a URL")
    if row.get("source_kind") not in SOURCE_KINDS:
        errs.append(f"source_kind {row.get('source_kind')!r} invalid")
    if row.get("tier") not in TIERS:
        errs.append(f"tier {row.get('tier')!r} invalid")
    conf = row.get("confidence")
    if not (isinstance(conf, (int, float)) and 0.0 <= conf <= 1.0):
        errs.append("confidence must be in [0,1]")
    q = row.get("quote", "")
    if not (isinstance(q, str) and 0 < len(q) <= 400):
        errs.append("quote required, <=400 chars (verbatim provenance)")
    if not row.get("retrieved"):
        errs.append("retrieved timestamp required")
    # place_id, if set, MUST come from the gazetteer's value set — no minted water, ever
    pid = row.get("place_id")
    if pid is not None and pid not in set(GAZETTEER.values()):
        errs.append(f"place_id {pid!r} not in gazetteer (new water is never auto-added)")
    scope = row.get("place_scope", "point" if pid else "region")
    if scope not in PLACE_SCOPES:
        errs.append(f"place_scope {scope!r} invalid")
    if scope == "point" and pid is None:
        errs.append("place_scope 'point' requires a gazetteer-resolved place_id")
    return errs


def load(path: Path = LEDGER) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def append_rows(rows: list[dict], path: Path = LEDGER) -> dict:
    """Validate, id, dedupe against the existing ledger, and write sorted. Returns a summary
    {added, skipped_dupe, rejected: [(row, errs)]}. Atomic rewrite (temp + replace)."""
    existing = load(path)
    seen = {r["id"] for r in existing}
    added, rejected, dupes = [], [], 0
    for row in rows:
        row = dict(row)
        row.setdefault("place_id", resolve_place(row.get("place_raw")))
        row["id"] = row_id(row)
        errs = validate_row(row)
        if errs:
            rejected.append((row, errs))
            continue
        if row["id"] in seen:
            dupes += 1
            continue
        seen.add(row["id"])
        added.append(row)
    if added:
        allrows = sorted(existing + added, key=lambda r: (r.get("date", ""), r["id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(r, sort_keys=True) for r in allrows) + "\n")
        tmp.replace(path)
    return {"added": len(added), "skipped_dupe": dupes, "rejected": rejected}


def confirmations(as_of: date, window_days: int = 7, path: Path = LEDGER) -> dict:
    """In-season 'confirmed active' state per (mouth/stretch, species): the freshest qualifying
    report within `window_days` of `as_of`. Consumed by the deterministic build to flip a marker
    from climatological-window to CONFIRMED — with the date + source domain shown, never bare.
    Only day-precision catch/run_status rows with a resolved place and confidence >= 0.5 count."""
    out: dict = {}
    for r in load(path):
        if r.get("kind") not in ("catch", "sighting", "run_status"):
            continue
        if r.get("date_precision", "day") not in ("day", "week"):
            continue
        if float(r.get("confidence", 0)) < 0.5:
            continue
        try:
            d = date.fromisoformat(r["date"])
        except ValueError:
            continue
        age = (as_of - d).days
        win = window_days if r.get("date_precision", "day") == "day" else window_days + 7
        if not (0 <= age <= win):
            continue
        pid = r.get("place_id")
        if pid is None:
            # LOCATIONLESS but real (operator: "100 salmon in a week" still means the runs are
            # on): migratory species drive a REGION-level inference, shown as "regional reports",
            # never pinned to a specific mouth the report didn't name.
            if r["species"] not in MIGRATORY:
                continue
            key = f"_region:{r['species']}"
        else:
            key = f"{pid}:{r['species']}"
        cur = out.get(key)
        if cur is None or r["date"] > cur["date"]:
            dom = re.sub(r"^www\.", "", re.sub(r"^https?://", "", r["source"]).split("/")[0])
            out[key] = {"place_id": pid, "species": r["species"], "scope":
                        r.get("place_scope", "point" if pid else "region"),
                        "date": r["date"], "kind": r["kind"], "source_domain": dom}
    return out
