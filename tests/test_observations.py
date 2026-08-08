"""Tests for the observation ledger (ADR-042) — the deterministic gate between the research
agent's extracted claims and everything that consumes them. Security-invariant style: the agent
must be UNABLE to mint new water, inject regs claims, or duplicate its way into fake volume."""
from __future__ import annotations

from datetime import date

import pytest

from tbay_fishcast.knowledge import observations as obs


def _row(**kw):
    base = {
        "kind": "catch", "species": "chinook", "date": "2025-09-10",
        "date_precision": "day", "place_raw": "the Kam mouth", "count": 2,
        "quote": "two chinook off the Kam mouth Tuesday evening",
        "source": "https://example-news.ca/fishing-report", "source_kind": "news",
        "tier": "T3", "confidence": 0.8, "retrieved": "2026-08-08T17:00:00Z",
    }
    base.update(kw)
    return base


def test_valid_row_passes_and_resolves_place():
    r = _row()
    r["place_id"] = obs.resolve_place(r["place_raw"])
    assert r["place_id"] == "kam"
    assert obs.validate_row(r) == []


def test_unknown_place_is_kept_but_never_minted():
    """A place the gazetteer doesn't know stays place_id=None — recorded, region-scope, never a
    new spot. And a fabricated place_id is rejected outright."""
    assert obs.resolve_place("secret honey hole off the highway") is None
    r = _row(place_raw="secret honey hole", place_id=None, place_scope="region")
    assert obs.validate_row(r) == []
    bad = _row(place_id="my_new_spot")
    assert any("gazetteer" in e for e in obs.validate_row(bad))


def test_locationless_regional_report_is_valid():
    """Operator 2026-08-08: '100 salmon in a week' with no location still matters — recorded at
    region scope; 'point' scope without a resolved place is invalid."""
    r = _row(place_raw=None, place_id=None, place_scope="region",
             quote="over 100 salmon caught in the bay this week")
    assert obs.validate_row(r) == []
    bad = _row(place_raw=None, place_id=None, place_scope="point")
    assert any("place_id" in e for e in obs.validate_row(bad))


def test_regs_claims_have_no_kind():
    """Rule 4: legality is never research-ingested — there is no 'regs' kind to smuggle one in."""
    assert "regs" not in obs.KINDS and "closure" not in obs.KINDS
    assert any("kind" in e for e in obs.validate_row(_row(kind="regs")))


def test_rejects_bad_species_date_confidence_quote():
    assert any("species" in e for e in obs.validate_row(_row(species="kraken")))
    assert any("date" in e for e in obs.validate_row(_row(date="Sept 10")))
    assert any("date" in e for e in obs.validate_row(_row(date="2031-01-01")))
    assert any("confidence" in e for e in obs.validate_row(_row(confidence=1.7)))
    assert any("quote" in e for e in obs.validate_row(_row(quote="")))
    assert any("source" in e for e in obs.validate_row(_row(source="facebook post")))


def test_append_dedupes_and_sorts(tmp_path):
    p = tmp_path / "obs.jsonl"
    r1 = _row(); r2 = _row(date="2025-09-08"); dup = _row()
    out = obs.append_rows([r1, r2, dup], path=p)
    assert out["added"] == 2 and out["skipped_dupe"] == 1 and not out["rejected"]
    rows = obs.load(p)
    assert [r["date"] for r in rows] == ["2025-09-08", "2025-09-10"]   # sorted
    # a second run with the same rows adds nothing (idempotent daily agent)
    out2 = obs.append_rows([r1, r2], path=p)
    assert out2["added"] == 0 and out2["skipped_dupe"] == 2


def test_rejected_rows_never_reach_the_ledger(tmp_path):
    p = tmp_path / "obs.jsonl"
    out = obs.append_rows([_row(kind="regs"), _row(species="kraken")], path=p)
    assert out["added"] == 0 and len(out["rejected"]) == 2
    assert obs.load(p) == []


def test_confirmations_fresh_placed_and_regional(tmp_path):
    p = tmp_path / "obs.jsonl"
    obs.append_rows([
        _row(date="2025-09-09"),                                        # kam chinook, fresh
        _row(date="2025-08-20"),                                        # too old
        _row(place_raw=None, place_id=None, place_scope="region",
             date="2025-09-07", species="pink",
             quote="pinks everywhere this week"),                       # regional, migratory
        _row(place_raw=None, place_id=None, place_scope="region",
             date="2025-09-07", species="lake_trout",
             quote="lakers deep"),                                      # regional non-migratory: no key
        _row(date="2025-09-06", confidence=0.3),                        # low confidence: excluded
    ], path=p)
    c = obs.confirmations(date(2025, 9, 10), path=p)
    assert c["kam:chinook"]["date"] == "2025-09-09"
    assert c["kam:chinook"]["source_domain"] == "example-news.ca"
    assert c["_region:pink"]["scope"] == "region"
    assert "_region:lake_trout" not in c
    # decay: same ledger 9 days later has nothing fresh
    assert obs.confirmations(date(2025, 9, 20), path=p) == {}
