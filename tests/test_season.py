"""Seasonal regime — the context that tells the angler the map is a summer model."""
from datetime import date

from tbay_fishcast.features import season


def test_regimes_by_month():
    assert season.regime(date(2026, 8, 7)).season == "summer"
    assert season.regime(date(2026, 8, 7)).upwelling == "primary"
    assert season.regime(date(2026, 5, 20)).season == "spring"
    assert season.regime(date(2026, 5, 20)).upwelling == "minimal"      # cold shoulder — upwelling matters least
    assert season.regime(date(2026, 10, 15)).season == "fall"
    assert season.regime(date(2026, 2, 1)).season == "winter"


def test_every_month_has_a_regime():
    for m in range(1, 13):
        r = season.regime(date(2026, m, 15))
        assert r.season in {"winter", "spring", "summer", "fall"}
        assert r.upwelling in {"primary", "secondary", "minimal"}
        assert r.note and r.label
