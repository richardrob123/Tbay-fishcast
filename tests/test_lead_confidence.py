"""The in-bay live feed and the measured lead ladder (ADR-055/056)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tbay_fishcast.features import lead_confidence as lc
from tbay_fishcast.ingest import swob

SKILL = {"per_lead": {
    "24":  {"ci95": [0.76, 1.19], "ratio_vs_persistence": 0.58, "skill_ratio": 0.95,
            "favorable_sign_agreement": 0.76},
    "48":  {"ci95": [0.57, 0.94], "ratio_vs_persistence": 0.41, "skill_ratio": 0.73,
            "favorable_sign_agreement": 0.73},
    "168": {"ci95": [1.17, 1.58], "ratio_vs_persistence": 1.06, "skill_ratio": 1.34,
            "favorable_sign_agreement": 0.63},
}}


def _feat(spd, drc, *, spd_qa=100, dir_qa=100, t="2026-08-15T12:00:00.000Z"):
    return {"properties": {"date_tm-value": t, swob.SPD: spd, swob.DIR: drc,
                           f"{swob.SPD}-qa": spd_qa, f"{swob.DIR}-qa": dir_qa}}


# --- the live in-bay feed -----------------------------------------------------------------------

def test_km_per_hour_becomes_knots():
    w = swob.parse_feature(_feat(18.52, 270))
    assert abs(w.speed_kn - 10.0) < 1e-6 and w.dir_deg == 270.0


def test_a_flagged_reading_is_refused():
    """This feed decides whether the product tells someone the lake is turning over."""
    assert swob.parse_feature(_feat(20.0, 270, spd_qa=10)) is None
    assert swob.parse_feature(_feat(20.0, 270, dir_qa=-1)) is None
    assert swob.parse_feature(_feat(None, 270)) is None


def test_calm_carries_no_direction():
    assert swob.parse_feature(_feat(0.0, 0)).dir_deg is None


def test_age_is_reported_so_staleness_can_be_loud():
    now = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
    obs = [swob.WindRecord(now - timedelta(hours=5), 270.0, 10.0)]
    assert swob.age_hours(obs, now=now) == 5.0
    assert swob.age_hours([], now=now) is None


# --- the measured ladder ------------------------------------------------------------------------

def test_the_nowcast_is_not_labelled_as_a_forecast():
    c = lc.for_lead(SKILL, 0, observed=True)
    assert c["skill_vs_baseline"] == lc.OBSERVED


def test_a_lead_whose_interval_clears_the_bar_is_measured():
    assert lc.for_lead(SKILL, 48)["skill_vs_baseline"] == lc.MEASURED


def test_a_lead_that_beats_persistence_but_not_the_oracle_is_informative():
    assert lc.for_lead(SKILL, 24)["skill_vs_baseline"] == lc.INFORMATIVE


def test_a_lead_that_beats_nothing_is_shown_as_weak_not_hidden():
    """Hiding a weak lead is how a map comes to imply confidence it never earned."""
    assert lc.for_lead(SKILL, 168)["skill_vs_baseline"] == lc.WEAK


def test_the_ladder_is_not_an_accuracy_ranking_and_says_so():
    """+24 h ranks BELOW +48 h because persistence is hardest to beat at short lead — not
    because a one-day forecast is worse. Anything rendering this must be told."""
    c24, c48 = lc.for_lead(SKILL, 24), lc.for_lead(SKILL, 48)
    assert c24["skill_vs_baseline"] != c48["skill_vs_baseline"]
    assert c24["sign_agreement"] > c48["sign_agreement"]      # accuracy runs the other way
    assert "NOT how accurate" in c24["not_an_accuracy_ranking"]


def test_an_unmeasured_lead_is_never_silently_borrowed_from_a_distant_one():
    assert lc.for_lead(SKILL, 500)["skill_vs_baseline"] == lc.UNMEASURED
    assert lc.for_lead(None, 48)["skill_vs_baseline"] == lc.UNMEASURED
    assert lc.for_lead({}, 48)["skill_vs_baseline"] == lc.UNMEASURED


def test_a_nearby_lead_may_speak_for_one_within_the_bin_spacing():
    c = lc.for_lead(SKILL, 36)
    assert c["measured_at_lead_h"] in (24, 48)
    assert c["skill_vs_baseline"] != lc.UNMEASURED
