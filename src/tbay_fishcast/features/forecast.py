"""Forecast reachability windows from an isotherm-depth trajectory.

LSOFS publishes an hourly 0–120 h (5-day) forecast (ADR: fields.fNNN). Feeding each
lead time through the same cross-shore logic as the nowcast gives a TRAJECTORY of
"is the target cold water reachable from this spot?" over the next five days. This
module is the pure reduction of that trajectory into human-facing WINDOWS — the
contiguous spans when a spot is open — so the brief/alert can say "reachable now
through Thursday, closes Friday" instead of only "today".

Pure and deterministic (no network, no LLM). The trajectory itself is built by the
caller (scripts/forecast_window.py), which pulls the forecast files.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ForecastPoint:
    valid_time: datetime          # UTC valid time of this lead step
    lead_h: int                   # hours from issue (0 = nowcast)
    isotherm_depth_m: float | None
    distance_m: float | None      # distance-from-shore of the target band
    reachable: bool               # under the CENTRAL bias estimate
    # honest-band bookends (AUDIT_ROUND3: the band was computed then discarded).
    # certain  = reachable even under the least-favorable end of the bias band;
    # possible = reachable under the most-favorable end. certain => reachable => possible.
    reachable_certain: bool = False
    reachable_possible: bool = False


@dataclass(frozen=True)
class Window:
    start: datetime               # valid time the window opens
    end: datetime                 # valid time of the last reachable step
    start_lead_h: int
    end_lead_h: int

    @property
    def open_now(self) -> bool:
        return self.start_lead_h == 0


def reachable_windows(points: list[ForecastPoint]) -> list[Window]:
    """Contiguous runs of reachable steps, in lead order.

    Points are sorted by lead time; each run of consecutive `reachable=True` steps
    becomes one Window spanning the first to the last reachable step in the run.
    """
    pts = sorted(points, key=lambda p: p.lead_h)
    windows: list[Window] = []
    run: list[ForecastPoint] = []
    for p in pts:
        if p.reachable:
            run.append(p)
        elif run:
            windows.append(_window(run))
            run = []
    if run:
        windows.append(_window(run))
    return windows


def _window(run: list[ForecastPoint]) -> Window:
    return Window(run[0].valid_time, run[-1].valid_time, run[0].lead_h, run[-1].lead_h)


def summarize(points: list[ForecastPoint], windows: list[Window]) -> str:
    """One-line verdict, local-agnostic (dates in UTC; caller localizes for display)."""
    if not points:
        return "no forecast data"
    if not windows:
        return "no reachable window in the next 5 days (cold water stays offshore/deep)"
    parts = []
    for w in windows:
        when = "open now" if w.open_now else f"opens {w.start:%a %b %-d}"
        # a window that reaches the end of the horizon is "still open"
        last = points[-1]
        if w.end_lead_h >= last.lead_h:
            parts.append(f"{when} and still open at +{last.lead_h} h")
        else:
            parts.append(f"{when}, closes ~{w.end:%a %b %-d}")
    return "; ".join(parts)
