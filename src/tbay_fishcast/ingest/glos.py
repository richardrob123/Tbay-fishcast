"""GLOS Seagull ERDDAP thermistor-chain client — REAL in-situ vertical temp profiles.

Great Lakes Observing System (GLOS) runs the "Seagull" ERDDAP server, which
re-serves NDBC and partner thermistor-chain moorings as tabledap datasets. Each
chain reports temperature at several fixed depths, giving a full vertical
profile at a mooring rather than the single-depth reading NDBC's own `.ocean`
files carry (see ndbc.py). That profile is what cross-shore thermocline work
needs: it lets us bracket a target depth between two real sensors instead of
extrapolating from one.

Source: https://seagull-erddap.glos.org/erddap/tabledap/ (tabledap CSV
protocol). ERDDAP CSV responses carry a units row directly under the header
row (`UTC,m,K`) — both must be skipped before parsing data. Temperature is
reported in KELVIN; every value here is converted to Celsius (- 273.15) at
parse time so downstream code never touches raw Kelvin.

Two layers, same split as ndbc.py / nonna.py:
  * PURE (`parse_chain_csv`, `profile_at`) — no network, unit-tested against a
    literal ERDDAP-shaped fixture.
  * NETWORK (`fetch_chain`) — curl via subprocess (like nonna.py's fetch_patch),
    so no extra HTTP dependency is required for this module alone.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone

ERDDAP_BASE = "https://seagull-erddap.glos.org/erddap/tabledap"
MISSING = {"", "NaN", "nan"}


@dataclass(frozen=True)
class ChainStation:
    station_id: str
    dataset: str  # ERDDAP tabledap dataset id
    lat: float
    lon: float
    name: str


# Thermistor-chain moorings with multi-depth profiles (verified 2026-08-05).
CHAINS = {
    "45216": ChainStation("45216", "obs_577_thermistor_latest", 46.932, -89.35,
                          "Ontonagon (45216) chain 0-12m"),
    "llo1": ChainStation("llo1", "obs_42_thermistor_latest", 46.86, -91.93,
                         "Duluth LLO1 chain 0-43m"),
}


@dataclass(frozen=True)
class ProfileSample:
    time: datetime  # UTC
    depth_m: float
    temp_c: float


def parse_chain_csv(text: str) -> list[ProfileSample]:
    """Parse a GLOS/ERDDAP tabledap CSV (header row + units row + data rows).

    Columns are time,depth,sea_water_temperature; temperature arrives in Kelvin
    and is converted to Celsius. Rows with empty/NaN temperature are dropped.
    """
    out: list[ProfileSample] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for line in lines[2:]:  # skip column-name row and units row
        parts = line.split(",")
        if len(parts) < 3:
            continue
        t_raw, depth_raw, temp_raw = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if temp_raw in MISSING:
            continue
        try:
            t = datetime.fromisoformat(t_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
            depth = float(depth_raw)
            temp_k = float(temp_raw)
        except ValueError:
            continue
        out.append(ProfileSample(time=t, depth_m=depth, temp_c=temp_k - 273.15))
    return out


def _erddap_url(dataset: str, start: date, end: date) -> str:
    return (f"{ERDDAP_BASE}/{dataset}.csv?time,depth,sea_water_temperature"
            f"&time%3E={start}T00:00:00Z&time%3C={end}T00:00:00Z")


def fetch_chain(station_id: str, start: date, end: date,
                timeout: float = 60.0) -> list[ProfileSample]:
    """Fetch a chain's profile time series over [start, end] from GLOS ERDDAP.

    Network — curl via subprocess (matches nonna.py's fetch_patch pattern, no
    extra HTTP dependency). Raises RuntimeError if the response isn't the
    expected CSV (ERDDAP returns an HTML/plain-text error page on bad requests
    or a missing/stale dataset).
    """
    station = CHAINS[station_id]
    url = _erddap_url(station.dataset, start, end)
    raw = subprocess.run(["curl", "-s", "-m", str(int(timeout)), url],
                         capture_output=True).stdout
    text = raw.decode("utf-8", "replace")
    if not text.strip().lower().startswith("time,depth,sea_water_temperature"):
        head = text[:200]
        raise RuntimeError(f"GLOS ERDDAP did not return CSV for {station_id}: {head!r}")
    return parse_chain_csv(text)


def profile_at(samples: list[ProfileSample], target: datetime,
              tol_h: float = 1.5) -> dict[float, float]:
    """Reduce samples to a single depth->temp_c profile near `target` time.

    Keeps samples within `tol_h` hours of `target`, then averages temperature
    across duplicate depths (a chain can log more than one reading per depth
    within the tolerance window). Returns {} if nothing falls in tolerance.
    """
    tol = tol_h * 3600.0
    near = [s for s in samples if abs((s.time - target).total_seconds()) <= tol]
    if not near:
        return {}
    by_depth: dict[float, list[float]] = {}
    for s in near:
        by_depth.setdefault(s.depth_m, []).append(s.temp_c)
    return {d: sum(ts) / len(ts) for d, ts in by_depth.items()}
