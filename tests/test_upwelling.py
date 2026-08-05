"""Ensemble-wind upwelling-probability tests (pure logic only, no network)."""
from datetime import datetime, timedelta, timezone

from tbay_fishcast.features.upwelling import member_favorable, upwelling_probability

BASE = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
N_HOURS = 48  # two forecast days


def _t(n=N_HOURS):
    return [BASE + timedelta(hours=i) for i in range(n)]


def _constant(speed: float, direction: float, n=N_HOURS):
    return [speed] * n, [direction] * n


def _blow_window(n, start_h, run_h, speed, direction, base_speed=5.0, base_dir=90.0):
    """n-hour series that is calm/off-sector except a run_h window of favorable
    wind starting at start_h."""
    speed_series = [base_speed] * n
    dir_series = [base_dir] * n
    for h in range(start_h, min(start_h + run_h, n)):
        speed_series[h] = speed
        dir_series[h] = direction
    return speed_series, dir_series


def test_member_favorable_flags_sustained_west_blow():
    # 8 consecutive hours of strong west wind starting hour 0 (day 0).
    speed, direction = _blow_window(N_HOURS, 0, 8, 15.0, 270.0)
    days = member_favorable(_t(), speed, direction, threshold_kn=13.0, min_run_h=6)
    assert BASE.date() in days


def test_member_favorable_short_run_does_not_count():
    # only 3 hours of favorable wind -- below min_run_h=6.
    speed, direction = _blow_window(N_HOURS, 10, 3, 20.0, 270.0)
    days = member_favorable(_t(), speed, direction, threshold_kn=13.0, min_run_h=6)
    assert days == set()


def test_member_favorable_wrong_direction_does_not_count():
    # strong, sustained, but from the east -- not west quadrant.
    speed, direction = _blow_window(N_HOURS, 5, 10, 20.0, 90.0)
    days = member_favorable(_t(), speed, direction, threshold_kn=13.0, min_run_h=6)
    assert days == set()


def test_member_favorable_below_threshold_does_not_count():
    # sustained west-quadrant wind, but under threshold_kn.
    speed, direction = _blow_window(N_HOURS, 5, 10, 8.0, 270.0)
    days = member_favorable(_t(), speed, direction, threshold_kn=13.0, min_run_h=6)
    assert days == set()


def test_upwelling_probability_fraction_across_members():
    times = _t()
    day0 = BASE.date()
    day1 = (BASE + timedelta(days=1)).date()

    # member A: sustained favorable blow on day 0 (hours 0-7).
    sA, dA = _blow_window(N_HOURS, 0, 8, 15.0, 270.0)
    # member B: sustained favorable blow on day 0 as well (hours 2-9).
    sB, dB = _blow_window(N_HOURS, 2, 8, 16.0, 250.0)
    # member C: calm the whole time -- no favorable day at all.
    sC, dC = _constant(3.0, 270.0)
    # member D: short favorable run on day 0 (4 h, below min_run_h) -- doesn't count.
    sD, dD = _blow_window(N_HOURS, 0, 4, 20.0, 270.0)

    members = [
        {"speed_kn": sA, "dir_deg": dA},
        {"speed_kn": sB, "dir_deg": dB},
        {"speed_kn": sC, "dir_deg": dC},
        {"speed_kn": sD, "dir_deg": dD},
    ]

    probs = upwelling_probability(times, members, threshold_kn=13.0, min_run_h=6)

    # exactly A and B are favorable on day 0 -> 2/4 = 0.5
    assert probs[day0] == 0.5
    # nobody is favorable on day 1
    assert probs[day1] == 0.0


def test_upwelling_probability_all_members_agree():
    times = _t()
    day0 = BASE.date()
    speed, direction = _blow_window(N_HOURS, 0, 10, 15.0, 280.0)
    members = [
        {"speed_kn": speed, "dir_deg": direction},
        {"speed_kn": speed, "dir_deg": direction},
        {"speed_kn": speed, "dir_deg": direction},
    ]
    probs = upwelling_probability(times, members, threshold_kn=13.0, min_run_h=6)
    assert probs[day0] == 1.0


def test_upwelling_probability_no_members_all_zero():
    times = _t()
    probs = upwelling_probability(times, [])
    assert all(p == 0.0 for p in probs.values())
    assert len(probs) == 2  # two calendar days spanned by the 48h series
