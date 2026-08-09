"""Tests for the CHS water-level gauge layer (ADR-043) — the observed check on the wind-derived
upwelling phase. Pure classify() tests, no network (golden-series discipline, CLAUDE rule 2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tbay_fishcast.ingest import water_level as wl


def _series(vals_by_hours_ago):
    """[(hours_ago, level_m)] -> the (utc, level) series classify() expects."""
    t0 = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    return [(t0 - timedelta(hours=h), v) for h, v in vals_by_hours_ago]


def _flat_then(recent_level, base_level=0.30):
    # 30 h baseline at 20-min spacing, then 6 h recent
    s = [(h / 3.0 + 6.0, base_level) for h in range(90)]      # 6..36 h ago
    s += [(h / 3.0, recent_level) for h in range(18)]          # 0..6 h ago
    return _series(s)


def test_falling_shore_level_reads_as_upwelling_favorable_drawdown():
    """The physical fingerprint: offshore wind pushes surface water away, the shore level drops,
    the thermocline tilts up. A 10 cm drawdown must register."""
    st = wl.classify(_flat_then(0.20))
    assert st.trend == "drawdown"
    assert st.upwelling_favorable is True
    assert st.anomaly_m is not None and st.anomaly_m < -wl.ANOMALY_M


def test_rebound_is_not_upwelling_favorable():
    st = wl.classify(_flat_then(0.40))
    assert st.trend == "rebound" and st.upwelling_favorable is False


def test_noise_below_the_bar_reads_steady():
    """1 cm is inside gauge/wave noise and must NOT be called an event."""
    st = wl.classify(_flat_then(0.31))
    assert st.trend == "steady" and st.upwelling_favorable is False


def test_short_series_is_unknown_not_guessed():
    st = wl.classify(_series([(0.0, 0.3), (0.5, 0.3)]))
    assert st.trend == "unknown" and st.anomaly_m is None
    assert "insufficient" in st.note


def test_empty_series_degrades_quietly():
    st = wl.classify([])
    assert st.trend == "unknown" and st.n == 0 and st.latest_m is None


def test_state_always_carries_the_attribution_caveat():
    """The anomaly mixes wind setup, seiche and barometric pressure — the output must say so, so
    the phase banner can never present it as proof of upwelling."""
    st = wl.classify(_flat_then(0.20))
    assert "seiche" in st.note and "not attribution" in st.note
    assert set(st.as_dict()) >= {"trend", "anomaly_m", "upwelling_favorable", "note"}


def test_both_map_gauges_are_registered():
    assert {"thunder_bay", "mission_river"} <= set(wl.GAUGES)
    for _sid, _code, _label, lat, lon in wl.GAUGES.values():
        assert 48.0 < lat < 48.9 and -89.9 < lon < -88.0     # inside the mapped shore
