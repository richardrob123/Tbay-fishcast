"""Spawning-run phenology — the WHEN dimension for the migratory, weak-temperature-cue species.

The thermal map is honest that salmon and steelhead are `temp_cue: weak` — they stage on tributary
plumes on a SEASONAL clock, not where the hypolimnion is cold. That clock lives in
`knowledge/schemas/events_calendar.yaml` (committed, provenanced) but until now nothing read it, so
the river-mouth markers were static dots year-round: the Kaministiquia mouth looked identical in
June (no run) and early September (chinook staging). This module makes the markers TRUTHFUL to the
calendar — a mouth lights up only inside its species' run window.

These are TYPICAL phenology windows (tier T2/T3 per entry), not a forecast of a specific fish. The
system says so: the marker reads "run window active (typical)", never "fish are here now." Pure,
date-only, no I/O beyond reading the committed YAML, no LLM (ADR-001). The GLSEA-anomaly date-shift
noted in the YAML header is a future refinement; today the windows are used as-authored.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

_CALENDAR = Path(__file__).resolve().parents[3] / "knowledge" / "schemas" / "events_calendar.yaml"
# FITTED windows (ADR-042): measured local phenology from the observation ledger, written by
# scripts/fit_run_windows.py. When a fit exists for an entry it REPLACES the authored window and
# the entry is stamped fitted=True (+n/n_years) so the UI can say "measured locally, n=..." instead
# of "typical". Absent file / absent entry => the authored literature window stands, unchanged.
_FITTED = Path(__file__).resolve().parents[3] / "data" / "calib" / "run_windows_fitted.json"

# The map's coarse species buckets → the finer calendar species. "salmon" on the map covers the
# Pacific salmon that run Thunder Bay tributaries; steelhead and lake_trout map through directly.
_MAP_TO_CALENDAR = {
    "salmon": {"chinook", "coho", "pink"},
    "steelhead": {"steelhead"},
    "lake_trout": {"lake_trout"},
    "brook_trout": {"brook_trout", "brook_trout_coaster"},
}

# "freeze_up" is an open-ended biological end; pin it to a deterministic late-fall date so the
# window is testable. Shore fishing on Thunder Bay tributaries is done by early December most years.
_FREEZE_UP = (12, 1)


@dataclass(frozen=True)
class ActiveRun:
    id: str
    species: str          # the calendar species (chinook, coho, pink, steelhead, ...)
    note: str
    window: tuple[str, str]  # ("MM-DD", "MM-DD" or "freeze_up") as authored — or as FITTED
    tier: str
    fitted: dict | None = None   # {n, n_years, authored, shift_days} when measured locally


@lru_cache(maxsize=1)
def _load_entries():
    """Parse the committed calendar once. Only calendar-triggered angling runs are kept — the
    condition-triggered upwelling_event (a wind/thermal trigger, handled by the phase banner) and
    the dip-net-only whitefish season are excluded from the run-marker logic."""
    try:
        raw = yaml.safe_load(_CALENDAR.read_text()) or []
    except (OSError, yaml.YAMLError) as e:  # pragma: no cover - config must exist in repo
        print(f"⚠ events_calendar.yaml unreadable ({e}) — run markers disabled")
        return []
    fits = _load_fits()
    out = []
    for e in raw:
        win = e.get("window") or {}
        if "start" not in win or "end" not in win:
            continue
        if e.get("mode") == "dip_net":          # not rod-and-reel; skip
            continue
        if "trigger" in e:                        # condition-triggered (upwelling) — not a calendar run
            continue
        f = fits.get(e.get("id"))
        if f and f.get("start") and f.get("end"):
            # measured local phenology supersedes the authored literature window
            e = dict(e)
            e["window"] = {"start": f["start"], "end": f["end"]}
            e["fitted"] = {"n": f.get("n"), "n_years": f.get("n_years"),
                           "authored": [win["start"], win["end"]],
                           "shift_days": f.get("shift_days")}
            e["tier"] = "DATA(local reports)"
        out.append(e)
    return out


def _load_fits() -> dict:
    """Fitted windows keyed by calendar-entry id. Any problem => {} (authored windows stand)."""
    try:
        import json
        d = json.loads(_FITTED.read_text())
        return d.get("windows", {}) if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _md(s: str) -> tuple[int, int]:
    m, d = s.split("-")
    return int(m), int(d)


def _in_window(d: date, start: str, end: str) -> bool:
    """Is date d within [start, end] (inclusive), each 'MM-DD' (or end 'freeze_up')? Handles a
    window that wraps the new year (start month > end month), though none currently do."""
    s = _md(start)
    e = _FREEZE_UP if end == "freeze_up" else _md(end)
    cur = (d.month, d.day)
    if s <= e:
        return s <= cur <= e
    return cur >= s or cur <= e   # wrap-around window


def active_runs(d: date) -> list[ActiveRun]:
    """All calendar run windows active on date d (species-level phenology, typical timing)."""
    out = []
    for e in _load_entries():
        win = e["window"]
        if _in_window(d, win["start"], win["end"]):
            out.append(ActiveRun(
                id=e["id"], species=str(e.get("species", "")),
                note=e.get("note", ""), window=(win["start"], win["end"]),
                tier=str(e.get("tier", "")), fitted=e.get("fitted"),
            ))
    return out


def active_calendar_species(d: date) -> set[str]:
    """Set of calendar species with an active run window on date d."""
    return {r.species for r in active_runs(d)}


def marker_status(d: date, marker_species: list[str]) -> dict:
    """Run-window status for one river-mouth marker on date d.

    marker_species: the map's coarse species the mouth helps (e.g. ['salmon','steelhead']).
    Returns {active: bool, runs: [{species, id, note, tier}], species_active: [map species...]}.
    `active` is True iff ANY of the mouth's species has a run window open — the marker is drawn
    bright when active, dimmed when out of window (so June's Kam mouth reads 'no run' honestly).
    """
    open_cal = active_calendar_species(d)
    runs, species_active = [], []
    for ms in marker_species:
        cal = _MAP_TO_CALENDAR.get(ms, {ms})
        hit = open_cal & cal
        if hit:
            species_active.append(ms)
            for r in active_runs(d):
                if r.species in hit and r.id not in {x["id"] for x in runs}:
                    runs.append({"species": r.species, "id": r.id, "note": r.note,
                                 "tier": r.tier, "fitted": r.fitted})
    return {"active": bool(species_active), "runs": runs, "species_active": species_active}


def next_run(d: date) -> dict | None:
    """The NEXT upcoming run window after date d (walkthrough finding #5: 'no run open' is a
    shrug — a salmon angler in August wants the chinook countdown). Returns
    {id, species, opens (ISO), days_until} for the soonest future start within the next year,
    or None if the calendar is empty."""
    from datetime import date as _date, timedelta as _td
    best = None
    for e in _load_entries():
        m, day = _md(e["window"]["start"])
        opens = _date(d.year, m, day)
        if opens <= d:
            opens = _date(d.year + 1, m, day)
        if best is None or opens < best[0]:
            best = (opens, e)
    if best is None:
        return None
    opens, e = best
    return {"id": e["id"], "species": str(e.get("species", "")),
            "opens": opens.isoformat(), "days_until": (opens - d).days}
