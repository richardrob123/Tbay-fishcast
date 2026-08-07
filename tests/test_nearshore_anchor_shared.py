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
    bcs = _load("scripts/build_coast_site.py", "bcs_ns")
    assert bcs._nearshore_delta() == nearshore_surface_delta()


def test_pin_path_applies_the_shared_delta():
    """forecast_window must warm the GLSEA anchor by the shared nearshore delta, not use raw SST."""
    src = (ROOT / "scripts" / "forecast_window.py").read_text()
    assert "nearshore_surface_delta" in src, "pin path no longer applies the nearshore delta"
    assert "_px.sst_c + _ns_delta" in src or "sst_c + _ns_delta" in src, \
        "pin path must add the nearshore delta to the GLSEA anchor"


def test_delta_is_nonnegative_and_measured():
    d, n = nearshore_surface_delta()
    assert d >= 0.0 and n >= 0
