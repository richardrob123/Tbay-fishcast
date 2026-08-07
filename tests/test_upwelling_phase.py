"""Upwelling-phase classifier — the model's most important correction (fish the relaxation,
not the trough). Deterministic, hermetic (no network): synthetic wind series in, phase out.
"""
from datetime import datetime, timedelta, timezone

import pytest

from tbay_fishcast.features import upwelling_phase as up
from tbay_fishcast.ingest import metar


def _series(start, hours, dir_deg, speed_kn, step_h=1):
    """A uniform hourly series: constant dir/speed unless given per-hour lists."""
    times, dirs, spds = [], [], []
    for i in range(hours):
        times.append(start + timedelta(hours=i * step_h))
        dirs.append(dir_deg[i] if isinstance(dir_deg, list) else dir_deg)
        spds.append(speed_kn[i] if isinstance(speed_kn, list) else speed_kn)
    return times, dirs, spds


T0 = datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc)


def test_no_wind_is_neutral():
    # calm easterly breeze — never favorable
    t, d, s = _series(T0, 48, 90, 4)
    ph = up.classify(t, d, s, now=T0 + timedelta(hours=47))
    assert ph.phase == up.NEUTRAL
    assert not ph.prime and not ph.suppressed


def test_active_short_blow_is_setup():
    # a west blow that has only been going 4 h at 'now' -> setup (building)
    t, d, s = _series(T0, 24, 270, 16)
    now = T0 + timedelta(hours=4)
    ph = up.classify(t, d, s, now=now)
    assert ph.phase == up.SETUP
    assert ph.active and ph.suppressed


def test_active_long_blow_is_peak():
    t, d, s = _series(T0, 24, 270, 16)
    now = T0 + timedelta(hours=18)  # 18 h into a sustained blow
    ph = up.classify(t, d, s, now=now)
    assert ph.phase == up.PEAK
    assert ph.active and ph.suppressed


def test_recently_ended_blow_is_peak_then_relaxation():
    # 14 h W blow on day 0, then calm. Classify at increasing lag.
    dirs = [270] * 14 + [90] * 58
    spds = [16] * 14 + [5] * 58
    t, d, s = _series(T0, 72, dirs, spds)
    end = T0 + timedelta(hours=13)
    # 6 h after end -> cold just parked -> PEAK
    ph_peak = up.classify(t, d, s, now=end + timedelta(hours=6))
    assert ph_peak.phase == up.PEAK
    # 30 h after end -> restratifying -> RELAXATION (prime)
    ph_relax = up.classify(t, d, s, now=end + timedelta(hours=30))
    assert ph_relax.phase == up.RELAXATION
    assert ph_relax.prime and not ph_relax.suppressed
    assert ph_relax.since_end_h == pytest.approx(30, abs=1.5)


def test_fully_relaxed_is_neutral():
    dirs = [270] * 14 + [90] * 120
    spds = [16] * 14 + [5] * 120
    t, d, s = _series(T0, 134, dirs, spds)
    end = T0 + timedelta(hours=13)
    ph = up.classify(t, d, s, now=end + timedelta(hours=90))  # > relax_end_h (72)
    assert ph.phase == up.NEUTRAL


def test_subthreshold_blow_does_not_trigger():
    # west wind but only 8 kn (below observed threshold 11) -> no run -> neutral
    t, d, s = _series(T0, 48, 270, 8)
    ph = up.classify(t, d, s, now=T0 + timedelta(hours=40))
    assert ph.phase == up.NEUTRAL


def test_variable_direction_never_counts():
    # None direction (VRB) at high speed must not form a favorable run
    t, d, s = _series(T0, 48, None, 20)
    ph = up.classify(t, d, s, now=T0 + timedelta(hours=40))
    assert ph.phase == up.NEUTRAL


def test_gap_breaks_run():
    # two 4 h favorable stretches separated by a big data gap: neither reaches min_run_h(6)
    t = [T0, T0 + timedelta(hours=1), T0 + timedelta(hours=2), T0 + timedelta(hours=3),
         T0 + timedelta(hours=40), T0 + timedelta(hours=41), T0 + timedelta(hours=42),
         T0 + timedelta(hours=43)]
    d = [270] * 8
    s = [16] * 8
    runs = up.extract_runs(t, d, s, threshold_kn=11, min_run_h=6)
    assert runs == []


def test_grace_bridges_brief_lull_but_not_long_one():
    # 12 h W blow with a single 1 h dip to 8 kn in the middle: grace bridges it -> one run
    spds = [14, 14, 14, 14, 14, 8, 14, 14, 14, 14, 14, 14]  # hour 5 dips below threshold
    t, d, s = _series(T0, 12, 270, spds)
    runs = up.extract_runs(t, d, s, threshold_kn=10, min_run_h=6)
    assert len(runs) == 1 and runs[0].duration_h == pytest.approx(11, abs=0.1)
    # a 4 h lull (longer than grace) splits it into two sub-min_run pieces -> no run
    spds2 = [14, 14, 14, 8, 8, 8, 8, 14, 14, 14]
    t2, d2, s2 = _series(T0, 10, 270, spds2)
    runs2 = up.extract_runs(t2, d2, s2, threshold_kn=10, min_run_h=6, grace_h=2)
    assert runs2 == []


def test_detail_and_dict_shape():
    dirs = [270] * 14 + [90] * 58
    spds = [16] * 14 + [5] * 58
    t, d, s = _series(T0, 72, dirs, spds)
    end = T0 + timedelta(hours=13)
    ph = up.classify(t, d, s, now=end + timedelta(hours=30), source="observed:CYQT")
    dd = ph.as_dict()
    assert dd["phase"] == up.RELAXATION and dd["prime"] is True
    assert dd["source"] == "observed:CYQT"
    assert "run" in dd and dd["run"]["peak_kn"] == pytest.approx(16, abs=0.1)
    assert "W" in ph.detail and "ended" in ph.detail


# ---- metar parse (pure, no network) ----

def test_metar_parse_units_and_vrb():
    recs = [
        {"reportTime": "2026-08-07T13:00:00.000Z", "wdir": 270, "wspd": 14},
        {"reportTime": "2026-08-07T12:00:00.000Z", "wdir": "VRB", "wspd": 3},
        {"reportTime": "2026-08-07T11:00:00.000Z", "wdir": 50, "wspd": None},  # dropped
        {"obsTime": 1786107600, "wdir": 200, "wspd": 9},
    ]
    obs = metar.parse_metar_json(recs)
    assert len(obs) == 3               # the wspd=None record is dropped
    assert obs[0].time <= obs[-1].time  # sorted oldest-first
    vrb = [o for o in obs if o.dir_deg is None]
    assert len(vrb) == 1 and vrb[0].speed_kn == 3.0  # VRB -> dir None, speed kept
