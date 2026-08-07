"""Thermal-band nesting invariant on the shipped map overlays (hermetic — reads committed
web/data, no network).

The map shades nested cold-water bands (t12 ⊇ t10 ⊇ t8) plus a faint "past a cast" ring
(t12far) that must sit strictly OUTSIDE the within-cast fills. Colder bands are clipped into
their warmer parent at build time (shapely intersection); without that, independent
polygon smoothing let a colder band's edge drift outside its parent, painting teal/blue
slivers with no green under them — the "colour blotch" artifact the operator caught. This
locks the invariant so it can't silently regress: every colder band must be ~entirely inside
its parent, and the faint ring must not overlap the solid fills.
"""
import glob
import json
import os

import pytest

from shapely.geometry import shape
from shapely.ops import unary_union

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AREAS = sorted(glob.glob(os.path.join(REPO, "web", "data", "areas", "*.geojson")))
TOL = 0.01   # ≤1% of a band may poke outside its parent (polygon-smoothing slack); the
             # build clips with intersection so measured worst case is ~0.2%. Pre-fix: 2–6%.


def _union(feats, lead, tag):
    geoms = [shape(f["geometry"]) for f in feats
             if f["properties"].get("lead") == lead and f["properties"].get("temp") == tag]
    return unary_union(geoms) if geoms else None


def _outside_frac(child, parent):
    if child is None or child.is_empty or child.area == 0:
        return 0.0
    if parent is None or parent.is_empty:
        return 1.0
    return child.difference(parent).area / child.area


def test_areas_exist():
    assert AREAS, "no committed coast overlays under web/data/areas/"


@pytest.mark.parametrize("path", AREAS, ids=lambda p: os.path.basename(p))
def test_bands_nest_and_faint_ring_is_outside(path):
    fc = json.load(open(path))
    feats = fc["features"]
    leads = sorted({f["properties"]["lead"] for f in feats})
    for lead in leads:
        t12 = _union(feats, lead, "t12")
        t10 = _union(feats, lead, "t10")
        t8 = _union(feats, lead, "t8")
        far = _union(feats, lead, "t12far")
        assert _outside_frac(t10, t12) <= TOL, f"{os.path.basename(path)} lead {lead}: t10 pokes outside t12"
        assert _outside_frac(t8, t10) <= TOL, f"{os.path.basename(path)} lead {lead}: t8 pokes outside t10"
        if far is not None and t12 is not None and not far.is_empty and far.area > 0:
            overlap = far.intersection(t12).area / far.area
            assert overlap <= TOL, f"{os.path.basename(path)} lead {lead}: past-cast ring overlaps within-cast t12"
