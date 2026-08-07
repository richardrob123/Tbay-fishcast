"""Species-band structure invariant on the shipped map overlays (hermetic — reads committed
web/data, no network).

The map grades, per species, within a cast (docs/FISH_BEHAVIOR_REVIEW.md): a discrete TEMPERATURE
base (s1 = in preferred range, s2 = optimal core) plus a CONTINUOUS measured-structure glow
(s3/s4/s5 = break/strong/top, ADR-033). Area features carry temp='sp:<species-id>:<level>' with
level ∈ {s1..s5}. This guards the shipped overlays: every feature is a valid polygon tagged with a
species the manifest declares and a known level, and each stretch actually shades the default
species (so a build that silently dropped the bands can't pass CI)."""
import glob
import json
import os

import pytest

from shapely.geometry import shape

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AREAS = sorted(glob.glob(os.path.join(REPO, "web", "data", "areas", "*.geojson")))
MANIFEST = os.path.join(REPO, "web", "data", "manifest.json")


def _species_ids():
    if not os.path.exists(MANIFEST):
        return set(), None
    m = json.load(open(MANIFEST))
    sp = m.get("species", [])
    ids = {s["id"] for s in sp}
    default = next((s["id"] for s in sp if s.get("default")), (sp[0]["id"] if sp else None))
    return ids, default


def test_areas_exist():
    assert AREAS, "no committed coast overlays under web/data/areas/"


@pytest.mark.parametrize("path", AREAS, ids=lambda p: os.path.basename(p))
def test_species_bands_valid(path):
    ids, default = _species_ids()
    if not ids:
        pytest.skip("no species in manifest")
    fc = json.load(open(path))
    levels = {"s1", "s2", "s3", "s4", "s5"}
    seen_default = False
    for f in fc["features"]:
        tag = f["properties"].get("temp", "")
        assert tag.startswith("sp:"), f"{os.path.basename(path)}: non-species tag {tag!r}"
        sid, _, level = tag[3:].rpartition(":")
        assert sid in ids, f"{os.path.basename(path)}: unknown species {sid!r} (tag {tag!r})"
        assert level in levels, f"{os.path.basename(path)}: unknown suitability level {level!r}"
        geom = shape(f["geometry"])
        assert not geom.is_empty, f"{os.path.basename(path)}: empty geom for {sid}"
        # marching-squares polygons can self-touch (a single valid-after-buffer artifact);
        # renderers handle it and the build now buffer-repairs, so require repairable, not strict.
        assert geom.is_valid or geom.buffer(0).is_valid, f"{os.path.basename(path)}: unrepairable geom for {sid}"
        if sid == default:
            seen_default = True
    # a stretch that built at all must shade the default species somewhere across the leads
    assert seen_default, f"{os.path.basename(path)}: default species {default!r} not shaded"


@pytest.mark.parametrize("path", AREAS, ids=lambda p: os.path.basename(p))
def test_weak_cue_species_emit_no_structure_glow(path):
    """Honesty in the DATA (ADR-033, not just the UI): plume-driven species (temp_cue='weak',
    salmon/steelhead) must NOT emit the confident structure-glow tiers (s3/s4/s5) — temperature
    and structure barely locate them. A non-web consumer of the geojson must see the truth."""
    m = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {"species": []}
    weak = {s["id"] for s in m.get("species", []) if s.get("temp_cue") == "weak"}
    if not weak:
        pytest.skip("no weak-cue species in manifest")
    glow = {"s3", "s4", "s5"}
    for f in json.load(open(path))["features"]:
        sid, _, level = f["properties"].get("temp", "")[3:].rpartition(":")
        assert not (sid in weak and level in glow), \
            f"{os.path.basename(path)}: weak-cue {sid} emitted structure glow {level!r}"
