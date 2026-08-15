"""Guard the build's phase/markers wiring (hermetic — the observed-wind fetch is monkeypatched,
no network). Complements test_upwelling_phase (the classifier) and test_geojson_nesting (the
committed overlays): this checks that build_coast_site stitches observed+forecast wind into a
per-lead phase block and that the river-mouth markers are well-formed.
"""
import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

bcs = importlib.import_module("build_coast_site")
from tbay_fishcast.ingest import metar, swob  # noqa: E402


def _lead_valid(issue):
    base = issue.replace(hour=18)
    lv = {0: base.strftime("%Y-%m-%dT%H:%M:%SZ")}
    for h in (24, 48, 72, 96, 120):
        lv[h] = (base + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return lv


def test_bottom_temp_inversion():
    import numpy as np
    targets = [12.0, 10.0, 8.0, 6.0]
    iso = {12.0: np.full((3, 3), 4.0), 10.0: np.full((3, 3), 8.0),
           8.0: np.full((3, 3), 12.0), 6.0: np.full((3, 3), 16.0)}
    depth = np.array([[2., 6., 10.], [14., 4., 8.], [12., 16., 20.]])
    b = bcs._bottom_temp_field(iso, targets, depth)
    assert b[0, 0] == 12.0          # shallower than warmest isotherm -> clamp warm
    assert b[0, 1] == pytest.approx(11.0)   # interpolated 12↔10
    assert b[0, 2] == pytest.approx(9.0)
    assert b[2, 1] == pytest.approx(6.0)
    assert b[2, 2] == 6.0           # deeper than coldest isotherm -> clamp cold
    # sentinel (column never reaches 6 °C) must not corrupt the interpolation
    iso2 = {12.0: np.full((1, 1), 4.0), 10.0: np.full((1, 1), 8.0),
            8.0: np.full((1, 1), 12.0), 6.0: np.full((1, 1), 999.0)}
    b2 = bcs._bottom_temp_field(iso2, targets, np.array([[10.0]]))
    assert b2[0, 0] == pytest.approx(9.0)
    # land / no-water depth -> NaN
    b3 = bcs._bottom_temp_field(iso, targets, np.array([[np.nan]]))
    assert np.isnan(b3[0, 0])


def test_bottom_temp_cold_lake_clamps_to_crossing_not_extreme_target():
    """Regression (ADR-036): at a cold stretch the extreme targets (18/4 °C) never cross, so the
    clamp must key off the WARMEST/COLDEST isotherm that ACTUALLY crosses — not the fixed extreme.
    The old code clamped only on the 18/4 fields; when those were sentinel it left the reachable
    band NaN, and the NaN coverage swung between forecast leads (the "blobs moving between days").
    Here only 10/8/6 °C cross (a 6–10 °C column); every water pixel must get a finite bottom temp."""
    import numpy as np
    S = 999.0
    targets = [18.0, 16.0, 14.0, 12.0, 10.0, 8.0, 6.0, 4.0]
    iso = {18.0: np.full((1, 4), S), 16.0: np.full((1, 4), S), 14.0: np.full((1, 4), S),
           12.0: np.full((1, 4), S), 10.0: np.full((1, 4), 2.0), 8.0: np.full((1, 4), 6.0),
           6.0: np.full((1, 4), 10.0), 4.0: np.full((1, 4), S)}
    depth = np.array([[1.0, 4.0, 8.0, 14.0]])   # shallower / mid / mid / deeper than crossings
    b = bcs._bottom_temp_field(iso, targets, depth)
    assert np.isfinite(b).all(), f"cold-lake band left NaN gaps: {b}"   # the core fix
    assert b[0, 0] == 10.0                       # above the 10 °C crossing -> warmest crossing
    assert b[0, 3] == 6.0                        # below the 6 °C crossing -> coldest crossing
    assert 6.0 <= b[0, 1] <= 10.0 and 6.0 <= b[0, 2] <= 10.0   # interpolated within the crossings


def test_markers_well_formed():
    sp_ids = {"lake_trout", "brook_trout", "salmon", "steelhead"}
    assert bcs.RIVER_MOUTHS, "no river-mouth markers"
    for m in bcs.RIVER_MOUTHS:
        assert {"id", "name", "lat", "lon", "note", "species"} <= set(m)
        assert 47.5 < m["lat"] < 49.0 and -90.5 < m["lon"] < -88.0  # in the Thunder Bay region
        assert m["species"] and set(m["species"]) <= sp_ids


def test_phase_block_observed_now_and_forecast_relaxation(monkeypatch):
    issue = datetime(2026, 8, 7, tzinfo=timezone.utc)
    # observed: a west blow that ended ~30 h before the nowcast valid time -> relaxation at day 0
    now_valid = datetime(2026, 8, 7, 18, tzinfo=timezone.utc)
    blow_end = now_valid - timedelta(hours=30)
    obs = []
    for i in range(72):
        t = now_valid - timedelta(hours=71 - i)
        favorable = (blow_end - timedelta(hours=8)) <= t <= blow_end
        obs.append(metar.WindObs(time=t, dir_deg=(270.0 if favorable else 90.0),
                                 speed_kn=(15.0 if favorable else 5.0)))
    # ADR-056: the in-bay station is the primary observed source now, so it is what this test
    # supplies. `swob.WindRecord` and `metar.WindObs` are deliberately the same shape.
    swob_obs = [swob.WindRecord(o.time, o.dir_deg, o.speed_kn) for o in obs]
    monkeypatch.setattr(swob, "fetch_recent_wind", lambda *a, **k: swob_obs)
    monkeypatch.setattr(swob, "age_hours", lambda *a, **k: 0.5)
    monkeypatch.setattr(metar, "fetch_recent_wind", lambda *a, **k: obs)

    # ensemble control with a fresh W blow landing on the day-4 (96 h) frame
    ens_t = [(issue + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(120)]
    cdir = [90.0] * 120
    cspd = [5.0] * 120
    for i in range(92, 104):   # ~12 h blow ~day 4
        cdir[i] = 270.0
        cspd[i] = 16.0
    ens = {"time": ens_t, "members": [{"dir_deg": cdir, "speed_kn": cspd}]}

    ph = bcs._build_phase(_lead_valid(issue), ens)
    assert ph is not None and ph["obs_available"] is True
    assert ph["obs_station"] == "WELCOME ISLAND (AUT)"
    assert ph["obs_in_bay"] is True and ph["obs_fallback"] is False
    # day 0 is observed and in the relaxation window
    now = ph["now"]
    assert now["source"] == "observed:WELCOME ISLAND (AUT)"
    assert now["phase"] == "relaxation" and now["prime"] is True
    # every lead present; forecast leads are labelled forecast
    assert set(ph["by_lead"]) == {"0", "24", "48", "72", "96", "120"}
    assert ph["by_lead"]["96"]["source"] == "forecast:gfs025"
    # the forecast blow drives a peak then relaxation across the later leads
    later = [ph["by_lead"][k]["phase"] for k in ("72", "96", "120")]
    assert "relaxation" in later or "peak" in later


def test_phase_forecast_only_when_metar_down(monkeypatch):
    issue = datetime(2026, 8, 7, tzinfo=timezone.utc)

    def _boom(*a, **k):
        from tbay_fishcast.ingest import SourceUnavailable
        raise SourceUnavailable("metar down")

    monkeypatch.setattr(metar, "fetch_recent_wind", _boom)
    monkeypatch.setattr(swob, "fetch_recent_wind", _boom)
    ens_t = [(issue + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(120)]
    ens = {"time": ens_t, "members": [{"dir_deg": [90.0] * 120, "speed_kn": [5.0] * 120}]}
    ph = bcs._build_phase(_lead_valid(issue), ens)
    assert ph is not None and ph["obs_available"] is False
    # with no observed wind, even lead 0 falls back to forecast (flagged), never silently "observed"
    assert ph["by_lead"]["0"]["source"].startswith("forecast")


def test_the_airport_fallback_announces_itself(monkeypatch):
    """ADR-056/rule 5. The airport misses ~71% of the hours the lake is actually blowing, so a
    SILENT revert to it would look identical to a map reading the lake. It must not be silent."""
    issue = datetime(2026, 8, 7, tzinfo=timezone.utc)
    now_valid = issue.replace(hour=18)

    def _down(*a, **k):
        from tbay_fishcast.ingest import SourceUnavailable
        raise SourceUnavailable("swob down")

    obs = [metar.WindObs(time=now_valid - timedelta(hours=71 - i), dir_deg=90.0, speed_kn=5.0)
           for i in range(72)]
    monkeypatch.setattr(swob, "fetch_recent_wind", _down)
    monkeypatch.setattr(metar, "fetch_recent_wind", lambda *a, **k: obs)

    ens_t = [(issue + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(120)]
    ens = {"time": ens_t, "members": [{"dir_deg": [90.0] * 120, "speed_kn": [5.0] * 120}]}
    ph = bcs._build_phase(_lead_valid(issue), ens)
    assert ph["obs_available"] is True
    assert ph["obs_station"] == metar.CYQT
    assert ph["obs_fallback"] is True and ph["obs_in_bay"] is False
    assert ph["obs_note"] and "71%" in ph["obs_note"]


def test_a_stale_in_bay_reading_falls_back_rather_than_passing_as_a_nowcast(monkeypatch):
    """An hourly station that last reported yesterday is not a nowcast, however in-bay it is."""
    issue = datetime(2026, 8, 7, tzinfo=timezone.utc)
    now_valid = issue.replace(hour=18)
    stale = [swob.WindRecord(now_valid - timedelta(hours=71 - i), 90.0, 5.0) for i in range(72)]
    obs = [metar.WindObs(time=now_valid - timedelta(hours=71 - i), dir_deg=90.0, speed_kn=5.0)
           for i in range(72)]
    monkeypatch.setattr(swob, "fetch_recent_wind", lambda *a, **k: stale)
    monkeypatch.setattr(swob, "age_hours", lambda *a, **k: 30.0)
    monkeypatch.setattr(metar, "fetch_recent_wind", lambda *a, **k: obs)

    ens_t = [(issue + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(120)]
    ens = {"time": ens_t, "members": [{"dir_deg": [90.0] * 120, "speed_kn": [5.0] * 120}]}
    ph = bcs._build_phase(_lead_valid(issue), ens)
    assert ph["obs_fallback"] is True
    assert "stale" in (ph["obs_note"] or "")


def test_lead_confidence_rides_along_with_every_forecast_lead(monkeypatch):
    """ADR-055: the label must come from the measurement file, never from `lead <= 48`."""
    issue = datetime(2026, 8, 7, tzinfo=timezone.utc)
    now_valid = issue.replace(hour=18)
    obs = [swob.WindRecord(now_valid - timedelta(hours=71 - i), 90.0, 5.0) for i in range(72)]
    monkeypatch.setattr(swob, "fetch_recent_wind", lambda *a, **k: obs)
    monkeypatch.setattr(swob, "age_hours", lambda *a, **k: 0.5)
    ens_t = [(issue + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(120)]
    ens = {"time": ens_t, "members": [{"dir_deg": [90.0] * 120, "speed_kn": [5.0] * 120}]}
    ph = bcs._build_phase(_lead_valid(issue), ens)
    for k, v in ph["by_lead"].items():
        assert "confidence_detail" in v, f"lead {k} carries no measured confidence"
        assert v["confidence_detail"]["skill_vs_baseline"]
    assert ph["by_lead"]["0"]["confidence_detail"]["skill_vs_baseline"] == "observed"
