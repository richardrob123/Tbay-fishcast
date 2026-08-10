"""GLOS thermistor chains from the ARCHIVE dataset, with QC flags (ADR-049).

WHY A SECOND GLOS READER. `ingest/glos.py` reads `obs_<n>_thermistor_latest`, a tall
(time, depth, temperature) table. It is the right shape and the wrong dataset for validation:

  * it is a THREE-DAY ROLLING WINDOW (`time_coverage_start` moves with the clock), so no
    hindcast is possible and the accuracy record can only ever grow forwards at 1 profile/day;
  * it carries NO QUALITY FLAGS, so a stuck or spiking thermistor enters the gate as truth.

The same buoy's archive dataset (`obs_42` for Duluth LLO1) carries the chain WIDE — one column
per sensor, `sea_water_temperature_1..N` — and each sensor brings its own `_depth` and a full
QARTOD flag set (`gross_range_test`, `spike_test`, `flat_line_test`, `rate_of_change_test`, and
an `aggregate`). It reaches back years. That is the difference between validating on 5 usable
rows and validating on thousands.

TWO THINGS THIS MODULE REFUSES TO ASSUME:
  * Sensor DEPTHS. The chain is re-deployed each season and the configuration changes — the 2025
    deployment reports sensor 1 at 1.0 m, the 2026 one at 3.0 m with sensor 0 dead. Depth is read
    per sample from the sensor's own `_depth` column, never from a table in this file.
  * Which sensors EXIST. Columns exist for sensors that are absent from a given deployment and
    return NaN for the whole season. They are discovered from the data.

QARTOD flags: 1 GOOD, 2 NOT_EVALUATED, 3 SUSPECT, 4 BAD, 9 MISSING. We keep 1 and 2 and drop
3/4/9. Dropping NOT_EVALUATED as well would discard almost everything — this network leaves the
aggregate at 2 for most historical rows — so 2 is kept and the count of each flag is REPORTED,
so a future reader can see how much of a sample rested on unevaluated data rather than assuming
it was screened.
"""
from __future__ import annotations

import csv
import io
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone

ERDDAP_BASE = "https://seagull-erddap.glos.org/erddap/tabledap"

# Archive datasets that carry a WIDE thermistor chain. Ontonagon (obs_577) is deliberately
# absent: its archive has no per-sensor columns at all, only a single surface temperature, so
# the chain there exists ONLY in the 3-day rolling dataset and cannot be hindcast.
ARCHIVE_CHAINS = {
    "llo1": "obs_42",       # Duluth-Nearshore Buoy (LLO1), U. Minnesota-Duluth
}
MAX_SENSORS = 16
QC_ACCEPT = {1, 2}          # GOOD, NOT_EVALUATED
KELVIN_ZERO = 273.15


@dataclass(frozen=True)
class ChainSample:
    time: datetime
    sensor: int
    depth_m: float
    temp_c: float
    qc: int


def _url(dataset: str, sensors, start: date, end: date) -> str:
    cols = ["time"]
    for i in sensors:
        cols += [f"sea_water_temperature_{i}",
                 f"sea_water_temperature_{i}_depth",
                 f"sea_water_temperature_{i}_aggregate"]
    return (f"{ERDDAP_BASE}/{dataset}.csv?{','.join(cols)}"
            f"&time%3E={start.isoformat()}T00:00:00Z"
            f"&time%3C={end.isoformat()}T00:00:00Z")


def _get(url: str, timeout: float) -> str:
    # curl via subprocess, matching glos.py/nonna.py — no extra HTTP dependency.
    raw = subprocess.run(["curl", "-s", "-m", str(int(timeout)), url],
                         capture_output=True).stdout
    return raw.decode("utf-8", "replace")


def fetch_chain(station_id: str, start: date, end: date, *, sensors=None,
                timeout: float = 180.0) -> tuple[list[ChainSample], dict]:
    """Archive chain samples over [start, end), plus a QC/coverage report.

    Returns ``(samples, stats)``. `stats` carries the flag histogram and the rejected count —
    a silent filter is how a QC step turns into an unexamined assumption.
    """
    dataset = ARCHIVE_CHAINS[station_id]
    sensors = list(sensors) if sensors else list(range(1, MAX_SENSORS + 1))
    text = _get(_url(dataset, sensors, start, end), timeout)
    if not text.lstrip().lower().startswith("time,"):
        # ERDDAP answers a request for a column that does not exist with an error page rather
        # than by omitting it, so narrow the request to the columns this dataset really has.
        avail = available_sensors(dataset, timeout=timeout)
        if not avail:
            raise RuntimeError(f"{dataset}: no thermistor columns ({text[:120]!r})")
        sensors = avail
        text = _get(_url(dataset, sensors, start, end), timeout)
        if not text.lstrip().lower().startswith("time,"):
            raise RuntimeError(f"{dataset}: unexpected response {text[:160]!r}")

    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 3:
        return [], {"rows": 0, "kept": 0, "flags": {}}
    head, units = rows[0], rows[1]
    idx = {c: i for i, c in enumerate(head)}
    kelvin = {c: (units[i].strip().upper() == "K") for c, i in idx.items()}
    out: list[ChainSample] = []
    flags: dict[int, int] = {}
    rejected = nan_rows = 0
    for r in rows[2:]:
        if not r or not r[0]:
            continue
        try:
            t = datetime.strptime(r[idx["time"]], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
        for i in sensors:
            tc, dc, qcc = (f"sea_water_temperature_{i}",
                           f"sea_water_temperature_{i}_depth",
                           f"sea_water_temperature_{i}_aggregate")
            if tc not in idx:
                continue
            traw, draw = r[idx[tc]].strip(), r[idx[dc]].strip() if dc in idx else ""
            if traw in ("", "NaN") or draw in ("", "NaN"):
                nan_rows += 1
                continue
            try:
                temp, depth = float(traw), float(draw)
            except ValueError:
                nan_rows += 1
                continue
            qraw = r[idx[qcc]].strip() if qcc in idx else ""
            try:
                qc = int(float(qraw)) if qraw not in ("", "NaN") else 2
            except ValueError:
                qc = 2
            flags[qc] = flags.get(qc, 0) + 1
            if qc not in QC_ACCEPT:
                rejected += 1
                continue
            out.append(ChainSample(t, i, depth,
                                   temp - KELVIN_ZERO if kelvin.get(tc) else temp, qc))
    stats = {"rows": len(rows) - 2, "kept": len(out), "rejected_qc": rejected,
             "empty": nan_rows, "flags": dict(sorted(flags.items())),
             "dataset": dataset,
             "sensors_seen": sorted({s.sensor for s in out}),
             "depths_seen": sorted({round(s.depth_m, 2) for s in out})}
    return out, stats


def available_sensors(dataset: str, timeout: float = 60.0) -> list[int]:
    """Which `sea_water_temperature_<n>` columns this dataset actually declares."""
    text = _get(f"https://seagull-erddap.glos.org/erddap/info/{dataset}/index.csv", timeout)
    ns = {int(m.group(1))
          for m in re.finditer(r"sea_water_temperature_(\d+)(?:,|\s)", text)}
    return sorted(n for n in ns if n <= MAX_SENSORS)


def profile_at(samples, when: datetime, tol_min: float = 45.0) -> dict[float, float]:
    """depth -> temperature at the sample time nearest `when`, within `tol_min`.

    Averages duplicate depths (two sensors can be logged at the same nominal depth) and takes
    the single nearest TIME rather than a window mean: a window straddling an internal-wave
    passage would smear a real 2-3 C swing into an artificial average, which then looks like
    model skill when the model is compared against it.
    """
    if not samples:
        return {}
    best_t = min({s.time for s in samples}, key=lambda t: abs((t - when).total_seconds()))
    if abs((best_t - when).total_seconds()) > tol_min * 60.0:
        return {}
    by_depth: dict[float, list[float]] = {}
    for s in samples:
        if s.time == best_t:
            by_depth.setdefault(round(s.depth_m, 2), []).append(s.temp_c)
    return {d: sum(v) / len(v) for d, v in by_depth.items()}
