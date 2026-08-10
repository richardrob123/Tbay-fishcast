"""Regression tests over the SHIPPED coloured shoreline (ADR-046).

``web/data/shore_access.geojson`` is a frozen, committed artifact — the map reads it directly, so
a bad rebuild reaches users without any further gate. These tests are that gate. They assert the
properties that make the layer safe rather than merely well-formed, because a syntactically
perfect file that paints reserve land green is the failure mode that matters.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ART = Path(__file__).resolve().parents[1] / "web" / "data" / "shore_access.geojson"
CLASSES = {"public", "private", "unknown"}


@pytest.fixture(scope="module")
def doc():
    if not ART.exists():
        pytest.skip("shore_access.geojson not built (python scripts/build_shore_access.py)")
    return json.loads(ART.read_text())


def test_every_segment_is_a_drawable_line_in_one_of_the_three_classes(doc):
    assert doc["features"], "an empty layer would silently render as no shoreline at all"
    for f in doc["features"]:
        assert f["geometry"]["type"] == "LineString"
        assert len(f["geometry"]["coordinates"]) >= 2, "a 1-point LineString draws nothing"
        assert f["properties"]["cls"] in CLASSES


def test_coordinates_are_inside_the_forecast_domain(doc):
    """A sign flip or a mercator/degree mix-up puts the coast in the Atlantic, and at overview
    zoom that reads as 'the layer is missing' rather than as an error."""
    for f in doc["features"]:
        for lon, lat in f["geometry"]["coordinates"]:
            assert -90.5 < lon < -87.5 and 47.5 < lat < 49.5, (lon, lat)


def test_reserve_land_is_never_painted_public(doc):
    """THE FALSE-GREEN GUARD, checked on the shipped bytes. Fort William 52 fronts water this
    forecast models, and reserve land carries the federal/Crown attributes the classifier reads as
    public. If a rebuild ever promotes it, the map invites people onto it."""
    res = [f for f in doc["features"] if "reserve land" in (f["properties"].get("why") or "")]
    assert res, "the First Nation reserve layer must still be reaching the output"
    assert all(f["properties"]["cls"] == "private" for f in res)


def test_a_public_segment_never_cites_private_title_as_its_reason(doc):
    for f in doc["features"]:
        if f["properties"]["cls"] == "public":
            assert (f["properties"].get("why") or "") != "Private"


def test_no_class_has_collapsed_or_taken_over(doc):
    """A silent classifier failure shows up as one class swallowing the coast — all-unknown when a
    fetch is short, all-private when TITLE_HOLDER_TYPE stops being read. Neither raises."""
    km = doc["meta"]["coverage_km"]
    total = sum(km.values())
    assert total > 100, f"only {total:.0f} km of shoreline classified"
    for cls in CLASSES:
        assert km.get(cls, 0) / total > 0.01, f"{cls} has all but vanished: {km}"
        assert km.get(cls, 0) / total < 0.90, f"{cls} has swallowed the coast: {km}"


def test_the_layer_ships_its_own_caveats(doc):
    """Green means PUBLIC LAND, not 'you can reach the water', and accuracy is unvalidated against
    surveyed points. Those two sentences travel WITH the data, so no consumer can strip them by
    forgetting to read the docs."""
    meta = doc["meta"]
    assert meta["tier"] == "T1" and "LIO" in meta["source"]
    assert "not mean" in meta["means"] or "does NOT" in meta["means"]
    assert "UNVALIDATED" in meta["validation"]
