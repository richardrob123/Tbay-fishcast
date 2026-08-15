"""One place that knows how remote sources fail, so a new script cannot forget (ADR-059).

WHY THIS EXISTS. Four separate upstreams in this project fail by returning NOTHING rather than an
error, and each time the caller could not distinguish that from "this station has no data". Each
was fixed in the file where it was found, and then the SAME class recurred in the next new file —
twice. `scripts/calibrate_upwelling_local.py` lost an entire 2026 season to an unclamped satellite
request months after ADR-054 fixed exactly that in `backfill_thermal_gate.py`. Per-call-site
clamping is not a fix; it is a fresh chance to forget.

THE FOUR REAL FAILURE MODES, all measured rather than supposed:

    ERDDAP (GLSEA)        end_date past coverage -> 404 for the WHOLE range, not a short series
    Open-Meteo archive    end_date past today    -> hard error for the WHOLE request
    ECCC climate-hourly   oversized response     -> connection reset, EMPTY BODY, no HTTP error
                                                    (2024-04-01..11-30 = 6.0 MB in 1.5 s is fine;
                                                     one month more is killed mid-flight)
    Open-Meteo runs       oversized response     -> same empty body
    Iowa State ASOS       rate limited           -> plain-text notice with HTTP **200**

And every caller here legitimately asks past the end of coverage, because a hindcast needs data
out to its longest lead's valid time, which is in the future by construction. So "don't ask for
too much" was never available as a fix.

THE ONE DESIGN DECISION THAT MATTERS. `fetch_windowed` returns `(records, FetchReport)` — a
tuple, not a bare list. The report carries what was clamped, how many chunks were requested, and
crucially `partial` / `missing`: chunks that yielded nothing after every retry. A caller cannot
obtain the data without also being handed the evidence of what is missing from it. That is the
structural version of "count what came back rather than trusting the range you asked for", and it
is why this returns an awkward tuple instead of a convenient list.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta

# Every source measured so far dies somewhere north of ~6 MB / ~6000 hourly records. 90 days of
# hourly data is comfortably inside that for every one of them, and requests are ~1.5 s, so more
# chunks costs little. Callers with a known-larger budget may raise it deliberately.
DEFAULT_CHUNK_DAYS = 90
DEFAULT_TRIES = 4


@dataclass
class FetchReport:
    """What actually happened — not what was asked for."""
    requested_start: date
    requested_end: date
    effective_start: date
    effective_end: date
    chunk_days: int
    chunks: int = 0
    attempts: int = 0
    records: int = 0
    clamped: bool = False
    clamp_reason: str | None = None
    missing: list = field(default_factory=list)     # [(start_iso, end_iso)] empty after retries

    @property
    def partial(self) -> bool:
        """True when some chunk never returned data. The caller MUST decide what that means —
        for a validation gate it usually means 'do not publish a verdict from this'."""
        return bool(self.missing)

    def as_dict(self) -> dict:
        return {"requested": [self.requested_start.isoformat(), self.requested_end.isoformat()],
                "effective": [self.effective_start.isoformat(), self.effective_end.isoformat()],
                "clamped": self.clamped, "clamp_reason": self.clamp_reason,
                "chunks": self.chunks, "attempts": self.attempts, "records": self.records,
                "partial": self.partial, "missing_windows": self.missing}

    def describe(self) -> str:
        bits = [f"{self.records} records over {self.chunks} chunk(s)"]
        if self.clamped:
            bits.append(f"clamped to {self.effective_end} ({self.clamp_reason})")
        if self.partial:
            bits.append(f"INCOMPLETE — {len(self.missing)} window(s) returned nothing")
        return "; ".join(bits)


def clamp_window(start: date, end: date, *, coverage_end: str | date | None = None,
                 not_after_today: bool = False, today: date | None = None):
    """-> (start, end, clamped, reason). Pure, so the clamping rule is testable on its own."""
    reason = None
    eff = end
    if not_after_today:
        t = today or date.today()
        if eff > t:
            eff, reason = t, "requested end is in the future"
    if coverage_end is not None:
        ce = coverage_end if isinstance(coverage_end, date) else date.fromisoformat(
            str(coverage_end)[:10])
        if eff > ce:
            eff = ce
            reason = (f"source coverage ends {ce.isoformat()}"
                      if reason is None else
                      f"{reason}; source coverage ends {ce.isoformat()}")
    return start, eff, (eff != end), reason


def fetch_windowed(start: date, end: date, *, fetch, chunk_days: int = DEFAULT_CHUNK_DAYS,
                   coverage_end=None, not_after_today: bool = False, tries: int = DEFAULT_TRIES,
                   backoff: float = 2.0, today: date | None = None, sleep=time.sleep):
    """Clamp, chunk, retry, and report. -> (records, FetchReport).

    ``fetch(chunk_start, chunk_end)`` returns a list of records for that window. It may return an
    empty list or raise; BOTH are treated as a possible transport failure and retried, because
    every source here signals overload with silence rather than an error. A window that is still
    empty after `tries` lands in ``report.missing`` instead of vanishing.

    An empty window is genuinely possible (a station really can have no data for a fortnight), so
    this cannot distinguish the two — and does not pretend to. It records the ambiguity and hands
    it to the caller, which is the honest thing an inner layer can do.
    """
    s, eff_end, clamped, reason = clamp_window(
        start, end, coverage_end=coverage_end, not_after_today=not_after_today, today=today)
    rep = FetchReport(requested_start=start, requested_end=end, effective_start=s,
                      effective_end=eff_end, chunk_days=int(chunk_days),
                      clamped=clamped, clamp_reason=reason)
    if eff_end < s:
        # The whole window is past coverage. Empty is the honest answer; sending a reversed range
        # would be a 400 the caller reads as an outage.
        rep.clamp_reason = rep.clamp_reason or "window entirely past coverage"
        return [], rep

    out = []
    cs = s
    while cs <= eff_end:
        ce = min(cs + timedelta(days=int(chunk_days) - 1), eff_end)
        rep.chunks += 1
        got = None
        for attempt in range(max(1, int(tries))):
            rep.attempts += 1
            try:
                got = fetch(cs, ce)
            except Exception:  # noqa: BLE001 — every source signals overload differently
                got = None
            if got:
                break
            if attempt < tries - 1:
                sleep(backoff * (2 ** attempt))
        if got:
            out.extend(got)
        else:
            rep.missing.append((cs.isoformat(), ce.isoformat()))
        cs = ce + timedelta(days=1)
    rep.records = len(out)
    return out, rep
