"""Fit spawning-run windows from the observation ledger (ADR-042) — the deep integration.

The run calendar currently states TYPICAL windows taken from literature/regional convention
(tier T2/T3): "chinook staging 08-25 → 09-15". Those dates drive what the river-mouth markers
say and when the weak-cue species get their run caveat — but nobody measured them HERE. Once the
ledger holds enough dated local reports, we can replace the guess with Thunder Bay's own measured
phenology, and say so with an n.

METHOD — local refinement, not independent discovery (stated honestly in the output):
each calendar entry keeps its authored window as a PRIOR. We take the ledger reports for that
entry's species whose day-of-year falls within the authored window ± `tol_days`, and set the
fitted window to the `lo_pct`/`hi_pct` percentiles of those report dates. Anchoring to the prior
is what keeps steelhead's SPRING and FALL runs from merging into one April–December smear — a
naive pooled percentile over all steelhead reports would do exactly that.

Requires `min_reports` reports spanning `min_years` distinct years before a fit is emitted at
all; below that the authored window stands unchanged (rule: never present a 4-report "fit" as
measured phenology). Effort bias is acknowledged, not corrected: anglers fish when they believe
the run is on, so report density partly reflects belief. The fit therefore describes WHEN PEOPLE
REPORT FISH, which is the honest thing for a fishing tool to time itself against — but it is not
a biological escapement curve, and the output says so.

Pure functions over ledger rows; no I/O here (the script does that), no LLM (ADR-001).
"""
from __future__ import annotations

from datetime import date, timedelta

MIGRATORY = {"chinook", "coho", "pink", "steelhead"}
MIN_REPORTS = 12      # below this a "fit" is not even computed
MIN_YEARS = 3         # a single big season must not define the window
# TWO-STAGE BAR (added 2026-08-09 after the first real fits). The bar above was set BLIND, before
# any data existed; the first live run then proposed moving the fall-steelhead window 20 days
# earlier on 12 photo-sightings. Computing that fit is useful; SHIPPING it to anglers on that
# evidence is not. So a fit is only APPLIED to the product at the higher bar below — everything
# between the two bars is emitted as a visible CANDIDATE that the calendar does not consume.
APPLY_MIN_REPORTS = 20
APPLY_MIN_YEARS = 4
TOL_DAYS = 30         # how far outside the authored window a report can sit and still inform it
LO_PCT, HI_PCT = 10.0, 90.0   # window edges = the bulk of reports, not the extreme tails
_FREEZE_UP_MD = (12, 1)


def _md_to_doy(md: tuple[int, int], year: int = 2001) -> int:
    """Day-of-year for a (month, day) in a NON-leap reference year, so fits are year-agnostic."""
    return date(year, md[0], md[1]).timetuple().tm_yday


def _doy_to_md(doy: float, year: int = 2001) -> str:
    d = date(year, 1, 1) + timedelta(days=int(round(doy)) - 1)
    return f"{d.month:02d}-{d.day:02d}"


def _parse_md(s: str) -> tuple[int, int]:
    if s == "freeze_up":
        return _FREEZE_UP_MD
    m, d = s.split("-")
    return int(m), int(d)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolated percentile (no numpy dependency in the knowledge layer)."""
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _report_doys(rows, species: str, win_start: str, win_end: str, tol_days: int):
    """Day-of-year values (and their years) for reports that plausibly belong to this run."""
    s_doy = _md_to_doy(_parse_md(win_start))
    e_doy = _md_to_doy(_parse_md(win_end))
    lo, hi = s_doy - tol_days, e_doy + tol_days
    doys, years = [], set()
    for r in rows:
        if r.get("species") != species:
            continue
        # ANALOG ROWS NEVER FIT OUR WINDOWS. The ledger deliberately carries out-of-area
        # covariates (e.g. the MN DNR Knife River trap, ~200 km southwest across the lake, a
        # different watershed and thermal regime). They are genuinely useful for CONTEXT and
        # comparison, but fitting Thunder Bay's phenology with Minnesota's run dates would be
        # exactly the silent substitution this project exists to avoid — the window would claim
        # to be "measured locally" while describing another jurisdiction's fish.
        if r.get("analog") or r.get("offshore_survey"):
            continue
        if r.get("kind") not in ("catch", "sighting", "run_status"):
            continue
        if r.get("date_precision", "day") not in ("day", "week"):
            continue          # month/year precision cannot time a run
        try:
            d = date.fromisoformat(r["date"])
        except (ValueError, KeyError):
            continue
        doy = d.timetuple().tm_yday
        if d.year % 4 == 0 and doy > 59:
            doy -= 1          # normalize leap years onto the non-leap reference
        if lo <= doy <= hi:
            doys.append(float(doy))
            years.add(d.year)
    return doys, years


def fit_entry(entry: dict, rows, *, min_reports: int = MIN_REPORTS, min_years: int = MIN_YEARS,
              tol_days: int = TOL_DAYS) -> dict | None:
    """Fit ONE calendar entry's window from the ledger, or None if the evidence is too thin.

    entry: a parsed events_calendar.yaml entry (needs id, species, window.start/.end).
    Returns {id, species, start, end, authored_start, authored_end, n, n_years, shift_days, method}.
    """
    species = str(entry.get("species", ""))
    if species not in MIGRATORY:
        return None
    win = entry.get("window") or {}
    if "start" not in win or "end" not in win:
        return None
    doys, years = _report_doys(rows, species, win["start"], win["end"], tol_days)
    if len(doys) < min_reports or len(years) < min_years:
        return None
    doys.sort()
    start_doy = _percentile(doys, LO_PCT)
    end_doy = _percentile(doys, HI_PCT)
    if end_doy <= start_doy:
        return None
    a_start = _md_to_doy(_parse_md(win["start"]))
    return {
        "id": entry.get("id"), "species": species,
        "start": _doy_to_md(start_doy), "end": _doy_to_md(end_doy),
        "authored_start": win["start"], "authored_end": win["end"],
        "n": len(doys), "n_years": len(sorted(years)),
        "years": sorted(years),
        "shift_days": int(round(start_doy - a_start)),
        "applied": len(doys) >= APPLY_MIN_REPORTS and len(years) >= APPLY_MIN_YEARS,
        "method": (f"p{LO_PCT:g}-p{HI_PCT:g} of local dated reports within the authored window "
                   f"+/-{tol_days}d; effort-biased (reports, not escapement)"),
    }


def fit_all(entries, rows, **kw) -> dict:
    """Fit every eligible calendar entry. Returns {entry_id: fit}; entries with thin evidence are
    simply absent, so the caller falls back to the authored window."""
    out = {}
    for e in entries:
        f = fit_entry(e, rows, **kw)
        if f:
            out[f["id"]] = f
    return out
