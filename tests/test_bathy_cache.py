"""Cached bathymetry loader tests."""
import json

from tbay_fishcast.features import bathy


def _write(tmp_path, sid, **over):
    d = {"station_id": sid, "dist_m": [0, 75, 300], "depth_m": [0, 6, 12],
         "bearing_deg": 130.0, "resolution_ground_m": 7.0, "source": "CHS NONNA-10 (WCS)",
         "tier": "T1", "retrieved": "2026-08-05", "attribution": "CHS", "coverage_frac": 0.6,
         "note": "ok"}
    d.update(over)
    (tmp_path / f"{sid}.json").write_text(json.dumps(d))


def test_load_returns_profile(tmp_path):
    _write(tmp_path, "s1")
    p = bathy.load("s1", bathy_dir=tmp_path)
    assert p is not None
    assert p.dist_m == [0, 75, 300] and p.depth_m == [0, 6, 12]
    assert p.tier == "T1" and not p.is_coarse
    assert p.max_depth_m == 12


def test_missing_returns_none(tmp_path):
    assert bathy.load("nope", bathy_dir=tmp_path) is None


def test_too_short_returns_none(tmp_path):
    _write(tmp_path, "s2", dist_m=[0], depth_m=[0])
    assert bathy.load("s2", bathy_dir=tmp_path) is None


def test_ncei_fallback_flagged_coarse(tmp_path):
    _write(tmp_path, "s3", resolution_ground_m=92.0,
           source="NCEI Lake Superior grid (~92 m, coarse fallback)", tier="T2")
    p = bathy.load("s3", bathy_dir=tmp_path)
    assert p.is_coarse and p.tier == "T2"


def test_committed_profiles_load_and_are_shore_anchored():
    """The real profiles built from CHS NONNA / NCEI must load and start at shore."""
    for sid in ("silver_harbour_outer", "marina_east_mcvicar", "mackenzie_point"):
        p = bathy.load(sid)
        assert p is not None, f"missing committed profile for {sid}"
        assert p.dist_m[0] == 0.0 and p.depth_m[0] == 0.0   # shore origin
        assert all(b >= a for a, b in zip(p.dist_m, p.dist_m[1:]))  # ascending distance
        assert p.max_depth_m and p.max_depth_m > 0
        assert p.attribution  # provenance carried for the map footer
