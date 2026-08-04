"""ECCC hydrometric flows — GeoMet realtime + HYDAT history (PLAN task 3, Phase 2).

STATUS: host `api.weather.gc.ca` (GeoMet OGC-API) is blocked by this environment's
egress policy (verified 2026-08-04: proxy 403). Verification #5 (confirm a Kam
station ID; enumerate Current/Neebing/McIntyre) could not run and is a follow-up
once the host is allowlisted or the human supplies station IDs.

River-phase logic is Phase 2, not Phase 0 — this client is a stub placeholder so
the module boundary exists (empty-module-stub only, per the kickoff ground rules).
"""
from __future__ import annotations

import requests

from . import SourceUnavailable

GEOMET_STATIONS = "https://api.weather.gc.ca/collections/hydrometric-stations/items"
# Bounding box around Thunder Bay for station discovery.
TBAY_BBOX = "-89.7,48.2,-89.0,48.7"


def list_stations(bbox: str = TBAY_BBOX, timeout: float = 60.0) -> list[dict]:
    """Enumerate hydrometric stations in a bbox (verification #5). Blocked here."""
    try:
        r = requests.get(GEOMET_STATIONS,
                         params={"bbox": bbox, "limit": 100, "f": "json"},
                         timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"GeoMet unreachable: {e}") from e
    return r.json().get("features", [])
