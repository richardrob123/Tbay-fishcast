"""Overlay-mask logic: cold/reachable + land-aware (mainland) shore distance.

Synthetic fields (a sloping shore) so the invariants are exact and network-free.
These pin the artifact fixes: no fake offshore 'shore', reachability only near the
mainland, castable-depth only. The 12 C line is a rendered contour (isobath_line_rgba)
verified visually, not asserted here.
"""
import numpy as np

from tbay_fishcast.features.overlay import (
    bridge_survey_gaps,
    cold_front_features,
    cold_reachable,
    fill_unsurveyed_water,
    imagery_unsurveyed_water,
    isobath_line_features,
    isobath_line_rgba,
    land_shore_distance,
    reachable_area_features,
)

RES = 10.0  # m/px


def _sloping_shore(rows=30, cols=30, land_cols=5, slope=2.0):
    """cols 0..land_cols-1 = land (NaN); water deepens 2 m per column after that."""
    depth = np.full((rows, cols), np.nan)
    for c in range(land_cols, cols):
        depth[:, c] = (c - (land_cols - 1)) * slope   # col land_cols -> 2 m, etc.
    return depth


def test_reachable_is_shallow_nearshore_only():
    depth = _sloping_shore()
    dist, land = land_shore_distance(depth, RES)
    iso = np.full_like(depth, 3.0)
    cold, reach = cold_reachable(depth, iso, dist, cast_m=75.0,
                                 max_reach_depth_m=22.0, min_reach_px=5)
    assert not reach[:, :5].any()              # nothing reachable on land
    assert not reach[:, 5].any()               # nor in the 2 m warm strip (above target)
    rd = depth[reach]
    assert reach.any()
    assert rd.max() <= 22.0
    assert dist[reach].max() <= 75.0 + 1e-6


def test_no_reachable_in_deep_water():
    depth = _sloping_shore(cols=40)          # extends to ~70 m depth
    dist, _ = land_shore_distance(depth, RES)
    iso = np.full_like(depth, 3.0)
    _, reach = cold_reachable(depth, iso, dist, cast_m=75.0, max_reach_depth_m=22.0)
    assert depth[reach].max() <= 22.0        # never calls 30 m+ water shore-reachable


def test_tiny_nodata_speck_is_not_land():
    depth = _sloping_shore()
    depth[0, 6] = np.nan                      # a 1 px no-data hole beside shallow water
    _, land = land_shore_distance(depth, RES, min_land_px=12)
    assert not land[0, 6]                      # too small to be shore -> no fake island


def test_offshore_island_is_not_mainland_shore():
    """An interior island (real, borders shallow water, big enough) is dropped when
    mainland_only — it isn't walk-to shore, so cold water around it isn't 'reachable'."""
    depth = np.full((30, 30), 10.0)
    depth[:, :5] = np.nan                 # mainland block (touches the left border)
    depth[:, 5] = 1.0                     # shallow waterline so the mainland is 'shore'
    depth[12:19, 12:19] = 1.0             # a shallow patch mid-grid
    depth[13:18, 13:18] = np.nan          # a 5x5 island inside it, off the border
    _, shore_main = land_shore_distance(depth, RES, mainland_only=True)
    _, shore_all = land_shore_distance(depth, RES, mainland_only=False)
    assert shore_all[15, 15]              # island counts as land without the restriction
    assert not shore_main[15, 15]         # dropped as non-mainland
    assert shore_main[10, 2] and shore_all[10, 2]   # the mainland is kept either way


def test_isobath_line_is_transparent_thin_and_placed():
    """The 12 C line renders as a THIN contour on a TRANSPARENT background at the
    iso crossing — not an opaque fill that would wipe the green it's drawn over."""
    depth = _sloping_shore(rows=60, cols=60)
    dist = np.zeros((60, 60))
    for c in range(60):
        dist[:, c] = abs(c - 4) * RES
    iso = np.full_like(depth, 3.0)
    buf = isobath_line_rgba(depth, iso, dist, line_band_m=1000.0)
    assert buf.shape == (60, 60, 4)
    drawn = buf[:, :, 3] > 40
    assert 0 < drawn.sum() < depth.size * 0.1        # a thin line, not a fill
    assert (buf[:, :, 3] == 0).sum() > depth.size * 0.5   # background transparent
    cols = np.where(drawn.any(axis=0))[0]
    assert 4 <= cols.min() and cols.max() <= 8       # at the 3 m crossing (col5->6)


def test_isobath_line_features_are_vector_paths_length_filtered():
    """The 12 C line comes back as lon/lat polylines (for a crisp vector layer), and
    short segments are dropped so tiny loops don't render as stray red specks."""
    depth = _sloping_shore(rows=40, cols=40)
    dist = np.zeros((40, 40))
    for c in range(40):
        dist[:, c] = abs(c - 4) * RES
    iso = np.full_like(depth, 3.0)
    bounds = (-9930000.0, 6190000.0, -9920000.0, 6200000.0)   # mercator, near Thunder Bay
    feats = isobath_line_features(depth, iso, dist, bounds, line_band_m=1000.0, min_len_m=50.0)
    assert len(feats) >= 1
    for path in feats:
        assert len(path) >= 2
        lon, lat = path[0]
        assert -90.5 < lon < -88.0 and 48.0 < lat < 49.0     # plausible lon/lat
    # an impossibly long minimum filters everything out
    assert isobath_line_features(depth, iso, dist, bounds, min_len_m=1e9) == []


def test_reachable_area_features_are_closed_lonlat_polygons():
    """The reachable area vectorizes to closed lon/lat polygon rings (for a fill layer
    that stays crisp at any zoom), and sub-threshold blobs are dropped."""
    depth = _sloping_shore(rows=40, cols=40)
    dist = np.zeros((40, 40))
    for c in range(40):
        dist[:, c] = abs(c - 4) * RES
    iso = np.full_like(depth, 3.0)
    _, reach = cold_reachable(depth, iso, dist, cast_m=75.0, min_reach_px=5)
    bounds = (-9930000.0, 6190000.0, -9920000.0, 6200000.0)
    polys = reachable_area_features(reach, bounds, RES, min_area_m2=100.0)
    assert len(polys) >= 1
    ext = polys[0][0]
    assert len(ext) >= 4 and ext[0] == ext[-1]       # closed ring
    lon, lat = ext[0]
    assert -90.5 < lon < -88.0 and 48.0 < lat < 49.0
    # an impossibly large minimum area drops everything
    assert reachable_area_features(reach, bounds, RES, min_area_m2=1e12) == []


def test_fill_unsurveyed_water_drops_midwater_seam_not_real_coast():
    """A NONNA no-data tendril reaching from the mainland out into open water gets filled
    (so it can't fake a mid-lake shore and spawn reachable cold water out there), while
    the solid mainland is left as land. Mirrors the delta seam that put green mid-bay."""
    depth = np.full((60, 60), 8.0)            # open water, 8 m (cold for iso 3 m, castable)
    depth[:, :10] = np.nan                     # solid mainland on the left
    depth[:, 10] = 1.5                         # a shallow warm strip so the coast is 'shore'
    depth[29:32, 10:45] = np.nan               # unsurveyed tendril from mainland into the bay
    depth[5:8, 50:53] = np.nan                 # a disconnected offshore no-data speck
    iso = np.full_like(depth, 3.0)

    # raw (no fill): the tendril, being part of the shore-qualifying mainland component,
    # fakes a reachable band out in mid-water beside its far end
    dist_raw, _ = land_shore_distance(depth, 10.0)
    _, reach_raw = cold_reachable(depth, iso, dist_raw, cast_m=75.0, min_reach_px=5)
    assert reach_raw[27:34, 38:44].any()       # artifact: green mid-water by the tendril tip

    filled = fill_unsurveyed_water(depth, 10.0, scan_m=90.0)
    assert np.isfinite(filled[30, 40])         # tendril tip filled to water
    assert np.isfinite(filled[6, 51])          # offshore speck filled to water
    assert not np.isfinite(filled[:, :10]).any()   # solid mainland still land

    dist, _ = land_shore_distance(filled, 10.0)
    _, reach = cold_reachable(filled, iso, dist, cast_m=75.0, min_reach_px=5)
    assert reach.any()                             # real nearshore cold water still reachable
    assert not reach[27:34, 38:44].any()           # the mid-water artifact is gone


def test_imagery_prunes_unsurveyed_water_apron_not_inland_dark():
    """The satellite basemap distinguishes NONNA's unsurveyed-water no-data (dark, joined
    to the lake -> water) from real land (bright) and inland dark patches (not joined ->
    land). Excluding the apron moves the cast to the true coast, so green stops appearing
    hundreds of metres offshore off the no-data edge."""
    depth = np.full((60, 60), 8.0)            # open water 8 m
    depth[:, :10] = np.nan                     # solid mainland (left)
    depth[:, 10] = 1.5                         # shallow surveyed water along the coast (shore)
    depth[28:32, 10:26] = np.nan               # unsurveyed no-data apron: a tongue off the coast
    depth[50:55, 2:7] = np.nan                 # (already mainland) an inland pocket, dark below
    iso = np.full_like(depth, 3.0)

    gray = np.full((60, 60), 20.0)             # water is dark everywhere...
    gray[:, :10] = 120.0                        # ...mainland is bright
    gray[50:55, 2:7] = 20.0                     # an inland DARK pocket, surrounded by bright land

    not_land = imagery_unsurveyed_water(depth, gray)
    assert not_land[30, 20]                     # apron: dark + joined to the lake -> water
    assert not not_land[52, 4]                  # inland dark pocket: not joined to lake -> land
    assert not not_land[:, :10].any()           # bright mainland (cols 0-9) never flagged

    # raw shore treats the apron as coast and casts green off its offshore edge, in the bay
    dist_raw, _ = land_shore_distance(depth, 10.0)
    _, reach_raw = cold_reachable(depth, iso, dist_raw, cast_m=75.0, min_reach_px=5)
    assert reach_raw[28:32, 27:32].any()

    # with the imagery mask the apron isn't shore -> that offshore green is gone
    dist_fix, _ = land_shore_distance(depth, 10.0, not_land=not_land)
    _, reach_fix = cold_reachable(depth, iso, dist_fix, cast_m=75.0, min_reach_px=5)
    assert not reach_fix[28:32, 27:32].any()
    assert reach_fix.any()                      # real nearshore cold water still reachable


def test_bridge_survey_gaps_fills_narrow_not_wide():
    """Narrow unsurveyed gaps between soundings get an interpolated depth (so green/front
    connect); wide unsurveyed areas stay no-data (honestly blank)."""
    res = 10.0
    depth = np.full((40, 60), 8.0)
    narrow = np.zeros_like(depth, dtype=bool); narrow[18:22, 20:23] = True   # ~30 m gap
    wide = np.zeros_like(depth, dtype=bool);  wide[8:32, 40:56] = True       # big block
    depth[narrow] = np.nan
    depth[wide] = np.nan
    unsurveyed = narrow | wide
    filled, remaining = bridge_survey_gaps(depth, unsurveyed, res, max_gap_m=45.0)
    # the narrow gap is fully bridged to real water...
    assert np.isfinite(filled[narrow]).all()
    assert not remaining[narrow].any()
    # ...the wide block's interior stays unsurveyed (only its <=45 m rim fills)
    assert remaining[wide].any()
    assert not np.isfinite(filled[20, 48])          # centre of the wide block still no-data
    # bridged depth is a plausible neighbour value (8 m here), never invented out of range
    assert np.nanmin(filled[narrow]) == 8.0


def test_cold_front_traces_shore_and_pulls_offshore_past_a_warm_apron():
    """The 12 C front traces the SHORELINE where cold water reaches the edge, and pulls
    offshore only where a warm shallow apron sits between the shore and the cold water."""
    from tbay_fishcast.features.overlay import merc
    res = 10.0
    depth = np.full((40, 40), 5.0)             # cold everywhere (>= iso 3)
    depth[:, :10] = np.nan                       # mainland (cols 0-9)
    depth[20:40, 10:13] = 1.0                    # a warm shallow apron (rows 20-39, cols 10-12)
    iso = np.full_like(depth, 3.0)
    dist = np.zeros((40, 40))
    for c in range(40):
        dist[:, c] = max(0.0, c - 9) * res       # 0 on land (cols<=9), grows offshore
    bounds = (-9930000.0, 6190000.0, -9920000.0, 6200000.0)
    x0, _, x1, _ = bounds

    feats = cold_front_features(depth, iso, dist, bounds, band_m=1000.0, min_len_m=20.0)
    assert len(feats) >= 1
    cols = [(merc(lat, lon)[0] - x0) / (x1 - x0) * 39.0 for p in feats for lon, lat in p]
    assert min(cols) < 11        # cold-to-shore rows: the front hugs the shoreline (col ~9-10)
    assert max(cols) > 12        # apron rows: the front pulls offshore of the warm strip

    # unsurveyed no-data offshore of shore (dist>0, no depth) produces no front through it
    d2 = depth.copy(); d2[5:9, 20:40] = np.nan   # a mid-water survey gap, not land (dist>0)
    feats2 = cold_front_features(d2, iso, dist, bounds, band_m=1000.0, min_len_m=20.0)
    gap_cols = [(merc(lat, lon)[0] - x0) / (x1 - x0) * 39.0 for p in feats2 for lon, lat in p]
    assert gap_cols and min(gap_cols) < 11       # still traces the shore, not the gap edge


def test_speck_removal_drops_isolated_reachable():
    depth = _sloping_shore()
    dist, _ = land_shore_distance(depth, RES)
    iso = np.full_like(depth, 3.0)
    dist2 = dist.copy()
    dist2[0, 25] = 0.0                          # 1 isolated in-range pixel in deep water
    _, reach = cold_reachable(depth, iso, dist2, min_reach_px=20)
    assert not reach[0, 25]                     # single speck removed
