"""Upwelling-event detector tests (PLAN task 4)."""
from datetime import datetime, timedelta, timezone

from tbay_fishcast.features.events import detect_events

BASE = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)


def _series(temps):
    times = [BASE + timedelta(hours=i) for i in range(len(temps))]
    return times, temps


def test_detects_sharp_drop():
    # steady 18C, then a >4C drop within 24h
    temps = [18.0] * 12 + [13.0] * 12  # 5C drop
    ev = detect_events(*_series(temps), cold_band_c=-99)  # disable cold trigger
    assert len(ev) == 1
    assert ev[0].trigger == "drop"
    assert ev[0].drop_c_24h >= 4.0


def test_detects_cold_band():
    temps = [18.0] * 6 + [11.5] * 6  # crosses 12C
    ev = detect_events(*_series(temps), drop_threshold_c=99)  # disable drop trigger
    assert len(ev) == 1
    assert ev[0].trigger == "cold_band"
    assert ev[0].min_temp_c <= 12.0


def test_no_event_on_stable_warm_water():
    temps = [18.0, 18.1, 17.9, 18.2, 18.0, 17.8]
    assert detect_events(*_series(temps)) == []


def test_dedups_multi_hour_spell_into_one_event():
    # one long cold spell -> a single onset, not one per hour
    temps = [18.0] * 6 + [10.0] * 10
    ev = detect_events(*_series(temps), drop_threshold_c=99)
    assert len(ev) == 1


def test_both_triggers_flagged():
    temps = [18.0] * 6 + [11.0] * 6  # big drop AND below cold band
    ev = detect_events(*_series(temps))
    assert len(ev) == 1
    assert ev[0].trigger == "both"


def test_unsorted_input_handled():
    times, temps = _series([18.0] * 6 + [11.0] * 6)
    # shuffle order
    pairs = list(zip(times, temps))[::-1]
    t2 = [p[0] for p in pairs]
    y2 = [p[1] for p in pairs]
    ev = detect_events(t2, y2)
    assert len(ev) == 1
