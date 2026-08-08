"""The coast MAP and the station-PIN path must anchor the surface identically (validation
finding #1). Both now go through features.nearshore.nearshore_surface_delta; these tests guard
against a future refactor re-introducing the divergence.
"""
import importlib.util
from pathlib import Path

from tbay_fishcast.features.nearshore import nearshore_surface_delta

ROOT = Path(__file__).resolve().parents[1]


def _load(mod_path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / mod_path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_map_delegates_to_shared_delta():
    """The map's per-class deltas must be exactly the shared helper's (one source of truth)."""
    from tbay_fishcast.features.nearshore import nearshore_delta_by_class
    bcs = _load("scripts/build_coast_site.py", "bcs_ns")
    assert bcs._nearshore_delta_by_class() == nearshore_delta_by_class()


def test_pin_path_applies_the_shared_delta():
    """forecast_window must warm the GLSEA anchor by the shared nearshore delta, not use raw SST."""
    src = (ROOT / "scripts" / "forecast_window.py").read_text()
    assert "delta_for_exposure" in src, "pin path no longer applies the shared nearshore delta"
    assert "sst_c + (_ns_delta" in src, \
        "pin path must add the (class-aware, signed) nearshore delta to the GLSEA anchor"


def test_delta_is_bounded_and_measured():
    """Class deltas are SIGNED (exposed can be slightly negative — measured); all values must be
    physically bounded and carry a sample count."""
    import math
    d, n = nearshore_surface_delta()
    assert math.isfinite(d) and abs(d) < 5.0 and n >= 0


def test_delta_by_exposure_class_is_spatially_structured():
    """ADR-036 follow-through: the backfill measured the delta as class-structured (exposed points
    ~0/negative, sheltered marina warm). The by-class helper must reflect that from the committed
    CSV, and the class values must bracket the regional median."""
    from tbay_fishcast.features.nearshore import (delta_for_exposure,
                                                  nearshore_delta_by_class,
                                                  nearshore_surface_delta)
    by = nearshore_delta_by_class()
    (de, ne), (ds, nsn) = by["exposed"], by["sheltered"]
    assert ne > 0 and nsn > 0
    assert ds > de, "sheltered must read warmer than exposed (measured structure)"
    reg, _ = nearshore_surface_delta()
    assert de <= reg <= ds
    # the exposure-routing helper returns exactly the class values; None -> regional
    assert delta_for_exposure("exposed") == by["exposed"]
    assert delta_for_exposure("sheltered") == by["sheltered"]
    assert delta_for_exposure(None) == (reg, 24) or delta_for_exposure(None)[0] == reg
