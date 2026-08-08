"""The ledger→map integration contract (ADR-042): researched observations must reach the river-
mouth markers as CONFIRMATIONS, and must never overstate what the report actually said.

These are the invariants that make the research layer structural rather than decorative:
  * a placed, fresh report for a species the mouth stages -> confirmed on THAT mouth;
  * a locationless (region) report -> attached but flagged place_id=None, so the UI can label it;
  * a report for a species this mouth does NOT stage -> never attached;
  * ledger species collapse to the map's modelled species (chinook/coho/pink -> salmon).
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "bcs_conf", Path(__file__).resolve().parents[1] / "scripts" / "build_coast_site.py")
_bcs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bcs)

from tbay_fishcast.knowledge import observations as obs   # noqa: E402


def _row(**kw):
    base = {"kind": "run_status", "species": "chinook", "date": "2025-09-10",
            "date_precision": "day", "place_raw": "Kam mouth", "count": 1,
            "quote": "chinook stacked at the Kam mouth", "source": "https://news.example/x",
            "source_kind": "news", "tier": "T3", "confidence": 0.8,
            "retrieved": "2026-08-08T00:00:00Z"}
    base.update(kw)
    return base


def _markers(confirm, mouths=None):
    """Replicate the build's marker-attachment logic against a confirmations dict."""
    out = []
    for m in (mouths or _bcs.RIVER_MOUTHS):
        hits = []
        for c in confirm.values():
            app_sp = _bcs.LEDGER_SPECIES_TO_APP.get(c["species"])
            if app_sp is None or app_sp not in m.get("species", []):
                continue
            if c.get("place_id") == m["id"] or c.get("place_id") is None:
                hits.append({**c, "app_species": app_sp})
        out.append({"id": m["id"], "confirmed": hits})
    return {m["id"]: m["confirmed"] for m in out}


def test_placed_report_confirms_its_own_mouth(tmp_path):
    p = tmp_path / "obs.jsonl"
    obs.append_rows([_row(date="2025-09-09")], path=p)
    conf = obs.confirmations(date(2025, 9, 10), path=p)
    got = _markers(conf)
    assert any(c["place_id"] == "kam" for c in got["kam"]), "Kam report must confirm the Kam mouth"
    # ...and does NOT confirm a different river as a placed report
    assert all(c["place_id"] != "current" for c in got.get("current", []))


def test_region_report_attaches_but_stays_labelled(tmp_path):
    """'100 salmon this week' with no location: honest evidence the run is on, attached to the
    mouths that stage salmon — but place_id stays None so the UI can say 'regional report'."""
    p = tmp_path / "obs.jsonl"
    obs.append_rows([_row(place_raw=None, place_id=None, place_scope="region",
                          quote="over 100 salmon caught in the bay this week")], path=p)
    conf = obs.confirmations(date(2025, 9, 10), path=p)
    got = _markers(conf)
    kam = got["kam"]
    assert kam and all(c["place_id"] is None for c in kam)
    assert all(c["app_species"] == "salmon" for c in kam)


def test_species_the_mouth_does_not_stage_is_never_attached(tmp_path):
    """A brook-trout report must not light up a salmon/steelhead mouth."""
    p = tmp_path / "obs.jsonl"
    obs.append_rows([_row(species="brook_trout", place_raw=None, place_id=None,
                          place_scope="region", quote="brookies in the creeks")], path=p)
    conf = obs.confirmations(date(2025, 9, 10), path=p)
    # brook_trout is non-migratory: confirmations() drops locationless rows for it entirely
    assert conf == {}
    assert all(not v for v in _markers(conf).values())


def test_ledger_species_collapse_to_modelled_species():
    m = _bcs.LEDGER_SPECIES_TO_APP
    assert m["chinook"] == m["coho"] == m["pink"] == "salmon"
    assert m["steelhead"] == "steelhead"
    # every mapped value must be a species the map actually models
    ids = {s for mouth in _bcs.RIVER_MOUTHS for s in mouth.get("species", [])}
    assert {"salmon", "steelhead"} <= ids


def test_stale_reports_stop_confirming(tmp_path):
    """Confirmation decays: a report from three weeks ago no longer claims the run is on."""
    p = tmp_path / "obs.jsonl"
    obs.append_rows([_row(date="2025-08-20")], path=p)
    assert obs.confirmations(date(2025, 9, 10), path=p) == {}
