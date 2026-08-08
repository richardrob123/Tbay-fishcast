"""Tests for the 'best spots today' ranking (pure, over synthetic per-tier areas)."""
from __future__ import annotations

from dataclasses import dataclass

from tbay_fishcast.features import top_spots as ts


@dataclass
class Sp:
    id: str
    name: str
    temp_cue: str


HA = 1e4  # m² per hectare


def test_weighted_index_orders_by_quality_not_just_size():
    """A stretch with a little optimal+structure beats a larger stretch of only in-range water."""
    per = {
        "big_shallow": {"s1": 10 * HA},                  # 10 ha, all in-range
        "small_prime": {"s2": 2 * HA, "s4": 2 * HA},     # 2 ha optimal + 2 ha strong break
    }
    ranked = ts.rank_species("lake_trout", False, per, {"big_shallow": "Big", "small_prime": "Prime"})
    assert ranked[0]["id"] == "small_prime"
    assert ranked[0]["score"] == 100
    assert ranked[1]["score"] < 100


def test_min_ha_drops_negligible_stretches():
    per = {"tiny": {"s2": 0.1 * HA}, "real": {"s2": 3 * HA}}
    ranked = ts.rank_species("lake_trout", False, per, {"tiny": "T", "real": "R"})
    assert [r["id"] for r in ranked] == ["real"]


def test_empty_when_no_habitat():
    assert ts.rank_species("lake_trout", False, {"a": {"s1": 0.0}}, {"a": "A"}) == []


def test_build_marks_weak_cue_and_adds_caveat():
    species = [Sp("lake_trout", "Lake trout", "strong"), Sp("salmon", "Salmon", "weak")]
    tabs = {
        "silver": {"lake_trout": {"s2": 5 * HA, "s4": 1 * HA}, "salmon": {"s1": 2 * HA}},
        "kam": {"lake_trout": {"s1": 1 * HA}, "salmon": {"s2": 3 * HA}},
    }
    names = {"silver": "Silver", "kam": "Kam"}
    out = ts.build(species, tabs, names, active_runs=[{"id": "chinook_staging"}])
    assert out["lake_trout"]["weak_cue"] is False
    assert out["lake_trout"]["caveat"] is None
    assert out["salmon"]["weak_cue"] is True
    assert "run timing" in out["salmon"]["caveat"]
    assert out["_active_runs"] == [{"id": "chinook_staging"}]
    # laker top spot is the one with optimal + structure
    assert out["lake_trout"]["ranked"][0]["id"] == "silver"


def test_reason_mentions_structure_for_strong_cue():
    per = {"a": {"s2": 4 * HA, "s4": 2 * HA, "s5": 1 * HA}}
    ranked = ts.rank_species("lake_trout", False, per, {"a": "A"})
    assert "structure" in ranked[0]["reason"] or "break" in ranked[0]["reason"]
    assert ranked[0]["glow_ha"] == 3.0


def test_scores_are_relative_to_daily_best():
    per = {"a": {"s2": 8 * HA}, "b": {"s2": 4 * HA}}
    ranked = ts.rank_species("lake_trout", False, per, {"a": "A", "b": "B"})
    assert ranked[0]["score"] == 100
    assert ranked[1]["score"] == 50   # half the weighted area → half the index


def test_low_confidence_stretches_are_never_ranked():
    """Walkthrough #1 (credibility-critical): the ranking must not crown a stretch the system
    itself flags low-confidence/indicative — it is excluded from ranked and listed as unverified."""
    species = [Sp("lake_trout", "Lake trout", "strong")]
    tabs = {"ltb": {"lake_trout": {"s2": 50 * HA, "s5": 10 * HA}},   # would win by area...
            "mck": {"lake_trout": {"s2": 5 * HA}}}
    names = {"ltb": "Little Trout Bay", "mck": "McKellar"}
    out = ts.build(species, tabs, names, low_confidence=frozenset({"ltb"}))
    ranked_ids = [r["id"] for r in out["lake_trout"]["ranked"]]
    assert "ltb" not in ranked_ids and ranked_ids == ["mck"]
    assert out["_unranked_unverified"] == ["Little Trout Bay"]
