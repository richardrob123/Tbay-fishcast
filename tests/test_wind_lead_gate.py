"""The wind-lead gate: ECCC conventions, the derived event bar, and the thin-sample guard.

ADR-055. Three of these tests exist because the first run of the gate got the answer wrong in a
way that looked right, which is the only kind of wrong that matters here.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tbay_fishcast.features import site_validity as sv, upwelling_drive as ud
from tbay_fishcast.ingest import eccc_wind

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- ECCC conventions. Each of these silently corrupts the record if read naively. -------------

def test_direction_is_tens_of_degrees():
    """36 is 360 degrees (north), not 36 degrees."""
    w = eccc_wind._parse({"UTC_DATE": "2026-07-01T12:00:00", "WIND_SPEED": 18.52,
                          "WIND_DIRECTION": 36})
    assert w.dir_deg == 360.0
    assert abs(w.speed_kn - 10.0) < 1e-6      # 18.52 km/h == 10.0 kn


def test_direction_zero_means_calm_not_north():
    """Verified against the live record: over 2497 hours, direction 0 occurs 83 times and the
    speed is 0 on every one. Reading it as north would inject 83 phantom northerlies."""
    w = eccc_wind._parse({"UTC_DATE": "2026-07-01T12:00:00", "WIND_SPEED": 0,
                          "WIND_DIRECTION": 0})
    assert w.calm and w.dir_deg is None and w.speed_kn == 0.0
    assert ud.favorable_component(w.speed_kn, w.dir_deg) == 0.0


def test_flagged_observations_are_dropped_not_absorbed():
    assert eccc_wind._parse({"UTC_DATE": "2026-07-01T12:00:00", "WIND_SPEED": 10,
                             "WIND_DIRECTION": 27, "WIND_SPEED_FLAG": "E"}) is None
    assert eccc_wind._parse({"UTC_DATE": "2026-07-01T12:00:00", "WIND_SPEED": None,
                             "WIND_DIRECTION": 27}) is None


# --- The scored quantity ------------------------------------------------------------------------

def test_favorable_component_signs_with_the_upwelling_axis():
    """Due west is fully favorable; due east is fully against. The SIGN is what the map's
    headline claim rests on, so it is pinned rather than assumed."""
    assert abs(ud.favorable_component(10.0, ud.FAVORABLE_AXIS_DEG) - 10.0) < 1e-9
    assert abs(ud.favorable_component(10.0, (ud.FAVORABLE_AXIS_DEG + 180) % 360) + 10.0) < 1e-9
    assert abs(ud.favorable_component(10.0, (ud.FAVORABLE_AXIS_DEG + 90) % 360)) < 1e-9


def test_the_event_bar_is_derived_from_existing_constants():
    """Not a third picked number: a window held at exactly the sustained-blow threshold."""
    assert ud.EVENT_DRIVE_KTH == ud.OBSERVED_THRESHOLD_KN * ud.WINDOW_H
    assert ud.is_event(ud.EVENT_DRIVE_KTH) is True
    assert ud.is_event(ud.EVENT_DRIVE_KTH - 0.1) is False
    assert ud.is_event(None) is None


def test_calms_stay_in_the_drive_window_as_zero_hours():
    """A calm arrives with no direction. Dropping it would shrink the trailing window and inflate
    the drive of every blow that follows a quiet spell — so a run interrupted by calms must score
    BELOW the same run with no interruption."""
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    times = [t0 + timedelta(hours=i) for i in range(25)]
    blow = ([20.0] * 25, [ud.FAVORABLE_AXIS_DEG] * 25)
    half = ([20.0 if i % 2 else 0.0 for i in range(25)],
            [ud.FAVORABLE_AXIS_DEG if i % 2 else None for i in range(25)])
    d_full = ud.drive_series(times, *blow)
    d_half = ud.drive_series(times, *half)
    assert len(d_half) == len(d_full) == 25          # calms kept, not dropped
    assert d_half[times[-1]] < d_full[times[-1]]


# --- The guards -------------------------------------------------------------------------------

def test_event_episodes_merge_across_short_gaps():
    """44 event HOURS in the real record are 3 storms. Counting hours as the sample size is what
    let the first run publish PSS 0.87 CI [0.75, 0.98] off a single April blow."""
    m = _load("awls", "scripts/analyze_wind_lead_skill.py")
    t0 = datetime(2026, 4, 18, tzinfo=timezone.utc)
    hours = ([t0 + timedelta(hours=i) for i in range(24)]              # one blow
             + [t0 + timedelta(days=17, hours=i) for i in range(19)])  # another, weeks later
    eps = m._episodes(hours)
    assert len(eps) == 2
    # a two-hour gap inside a blow is the same blow, not two
    assert len(m._episodes([t0, t0 + timedelta(hours=2), t0 + timedelta(hours=4)])) == 1
    assert m.MIN_EVENT_EPISODES > len(eps), "the real record must not clear the bar by accident"


def test_csi_is_not_inflated_by_correct_negatives():
    """With a 1.5% base rate, PSS ~ POD: a forecast catching most blows scores high while three
    of four of its calls are false alarms. CSI ignores the correct-negative pile."""
    m = _load("awls2", "scripts/analyze_wind_lead_skill.py")
    rows = ([(True, True)] * 10 + [(True, False)] * 1
            + [(False, True)] * 26 + [(False, False)] * 2848)
    r = m._pss(rows)
    assert r["pss"] > 0.89          # looks excellent
    assert r["csi"] < 0.3           # and is not
    assert r["far"] > 0.7


def test_the_reference_guard_names_the_quantity_it_measured():
    """It was written for water temperature and its verdict said so, so the first reuse on WIND
    reported a ratio 'of the real water's'. A guard that misdescribes its own subject invites the
    misreading it exists to prevent."""
    days = [f"2026-05-{d:02d}" for d in range(1, 32)]
    ref = {d: float(i % 7) for i, d in enumerate(days)}
    ins = {d: float(i % 7) for i, d in enumerate(days)}
    out = sv.reference_variability(ref, ins, quantity="wind", unit="kt*h")
    assert out["quantity"] == "wind"
    assert "wind" in out["reason"] and "water" not in out["reason"]
