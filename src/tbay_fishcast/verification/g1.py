"""G1 truth-pairing: build daily-mean model SST and pair it with GLSEA (PLAN task 5).

Why daily means: GLSEA is a daily SST composite. Comparing one instantaneous LSOFS
hour to it aliases the diurnal cycle (a 12:00Z snapshot sits near the daily minimum
in local time and would bias the model cold). So we average all nowcast valid-hours
of a UTC day into a daily-mean model SST, then compare to that day's GLSEA.

Cross-cycle dedup: the same valid hour is produced by consecutive nowcast cycles;
the later-issued cycle is more fully analyzed, so for a duplicate (station, valid_time)
we keep the sample from the latest issuance. QC: a day must have >= `min_hours`
distinct valid-hours or it is dropped (and reported, never silently averaged thin).

Pure functions over plain records — no I/O, fully unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

PLAUSIBLE_SST_C = (-2.0, 30.0)  # liquid Superior surface; outside => sensor/fill junk


@dataclass(frozen=True)
class SurfaceSample:
    station_id: str
    valid_time: datetime  # UTC
    temp_c: float
    issued: datetime      # cycle issuance time (later = more analyzed) — dedup key


@dataclass(frozen=True)
class DailySurface:
    station_id: str
    day: date
    mean_temp_c: float
    max_temp_c: float   # warmest hour — fairer vs a daytime-weighted satellite composite
    min_temp_c: float
    n_hours: int

    @property
    def diurnal_range_c(self) -> float:
        return self.max_temp_c - self.min_temp_c


@dataclass(frozen=True)
class DroppedDay:
    station_id: str
    day: date
    n_hours: int
    reason: str


def build_daily_surface(
    samples, min_hours: int = 18
) -> tuple[list[DailySurface], list[DroppedDay]]:
    """Daily-mean model SST per (station, UTC day) with dedup + QC.

    Returns (kept, dropped). A day is dropped when, after dedup and plausibility
    filtering, it has fewer than `min_hours` distinct valid-hours.
    """
    lo, hi = PLAUSIBLE_SST_C
    # dedup by (station, valid_time), keeping the latest issuance
    best: dict[tuple[str, datetime], SurfaceSample] = {}
    for s in samples:
        if not (lo <= s.temp_c <= hi):
            continue  # QC: drop implausible/fill values before they touch a mean
        key = (s.station_id, s.valid_time)
        cur = best.get(key)
        if cur is None or s.issued > cur.issued:
            best[key] = s

    # group by (station, day)
    groups: dict[tuple[str, date], list[float]] = {}
    for (sid, vt), s in best.items():
        groups.setdefault((sid, vt.date()), []).append(s.temp_c)

    kept: list[DailySurface] = []
    dropped: list[DroppedDay] = []
    for (sid, d), temps in sorted(groups.items()):
        if len(temps) >= min_hours:
            kept.append(DailySurface(sid, d, sum(temps) / len(temps),
                                     max(temps), min(temps), len(temps)))
        else:
            dropped.append(DroppedDay(sid, d, len(temps), f"< {min_hours} valid hours"))
    return kept, dropped


def pair_model_truth(
    daily: list[DailySurface],
    truth: dict[tuple[str, date], float],
    model_attr: str = "mean_temp_c",
) -> dict[str, list[tuple[float, float]]]:
    """Pair a daily model SST statistic with GLSEA truth keyed by (station_id, day).

    `model_attr` selects the model quantity ('mean_temp_c' or 'max_temp_c') so the
    same pairing serves the daily-mean and daily-max framings. Inner join: a
    cloud-gapped GLSEA day or a thin model day simply drops out.
    """
    out: dict[str, list[tuple[float, float]]] = {}
    for ds in daily:
        t = truth.get((ds.station_id, ds.day))
        if t is None:
            continue
        out.setdefault(ds.station_id, []).append((getattr(ds, model_attr), t))
    return out
