"""Authoritative land/water mask for the Thunder Bay shore — OSM geometry, not pixels.

The isotherm/reachability overlays need to know which pixels are water. The old approach
guessed from imagery brightness/texture, which flipped on hard cases: dark forested
outcrops (the treed MacKenzie / Silver Harbour points) read as "water", and dark teal deep
water risked reading as "land". This module replaces that guess with authoritative geometry.

Source: OpenStreetMap via Overpass. Lake Superior on the Thunder Bay shore is NOT tagged
`natural=coastline` (a MacKenzie-sized bbox returns zero coastline ways). It is the interior
of a single giant `natural=water`/`water=lake` multipolygon RELATION (id 4039486). Its
member ways are the shoreline; small islands are inner-ring member ways. We fetch the member
ways crossing the bbox (plus standalone `natural=water` ways for inland ponds and any
`natural=coastline` ways so the same code generalizes), rasterise them as impermeable
barriers, flood-fill the free space into connected components, and label each component
water/land with one authoritative Overpass `is_in` probe at its most-interior pixel (the lake
registers as area 3604039486 == 3.6e9 + relation id). Vector shoreline traces the treed
points exactly — the whole reason to prefer it over a 30 m raster.

Documented fallback (not used): JRC Global Surface Water 30 m
(/vsicurl/https://storage.googleapis.com/global-surface-water/downloads2021/occurrence/
occurrence_90W_50Nv1_4_2021.tif — note: no '_' before 'v'); 30 m rounds off the small treed
points this mask exists to get right.

Deterministic and offline-safe once fetched: every Overpass response is cached under the
gitignored data/ dir, keyed by rounded coords. No LLM in this path.

    water_mask(bounds_3857, shape_hw) -> np.ndarray[bool]   # True == water
Raises WaterMaskError on any fetch/parse failure so the caller can fall back.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from pathlib import Path

import numpy as np

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Retry rotates through these — a sibling instance often answers when the main one is
# rate-limiting. Extra hosts are kept commented because this environment's egress proxy only
# reaches overpass-api.de (the public mirrors return connection-refused here, verified
# 2026-08-07); rotating onto an unreachable host just burns a retry. Re-add if a mirror
# becomes reachable.
OVERPASS_ENDPOINTS = [
    OVERPASS_URL,
    # "https://overpass.kumi.systems/api/interpreter",   # unreachable via this proxy
    # "https://overpass.private.coffee/api/interpreter",  # unreachable via this proxy
]
_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _ROOT / "data" / "watermask_cache"
# FROZEN masks are COMMITTED (unlike _CACHE_DIR, which is gitignored). The Lake Superior
# shoreline is a static geographic fact; re-fetching it live from Overpass on every 4x-daily
# runner build put a flaky, rate-limit-prone network dependency in the data path (CLAUDE.md
# rule #1) — a partial fetch silently shifted the shoreline and dropped the within-cast cold
# wedge off narrow points (user-caught: Silver Harbour tip flickering solid/faint between
# builds). Freezing the verified masks makes every build — runner and local — identical.
_FROZEN_DIR = _ROOT / "data" / "watermask_frozen"
_R = 6378137.0
_MIN_COMPONENT_PX = 16
_MAX_IS_IN_CALLS = 24


def _frozen_key(bounds_3857, H, W) -> str:
    x0, y0, x1, y1 = (round(float(v), 3) for v in bounds_3857)
    return "wm_" + hashlib.md5(f"{x0}_{y0}_{x1}_{y1}_{H}x{W}".encode()).hexdigest()[:16]


def _frozen_get(bounds_3857, H, W):
    f = _FROZEN_DIR / f"{_frozen_key(bounds_3857, H, W)}.npz"
    if not f.exists():
        return None
    try:
        with np.load(f) as z:
            if int(z["h"]) != H or int(z["w"]) != W:
                return None
            return np.unpackbits(z["packed"], count=H * W).astype(bool).reshape(H, W)
    except Exception:  # noqa: BLE001
        return None


def save_frozen_mask(bounds_3857, mask) -> Path:
    """Persist a COMMITTED water mask so builds reuse it instead of re-fetching Overpass."""
    H, W = mask.shape
    _FROZEN_DIR.mkdir(parents=True, exist_ok=True)
    f = _FROZEN_DIR / f"{_frozen_key(bounds_3857, H, W)}.npz"
    np.savez_compressed(f, packed=np.packbits(mask.astype(bool).ravel()),
                        h=np.int32(H), w=np.int32(W),
                        bounds=np.asarray([float(v) for v in bounds_3857], dtype=np.float64))
    return f


def iter_frozen():
    """Yield (bounds_3857, (H, W), mask) for every committed frozen mask — offline; lets
    tests round-trip and re-key each one without any network fetch."""
    if not _FROZEN_DIR.exists():
        return
    for f in sorted(_FROZEN_DIR.glob("wm_*.npz")):
        with np.load(f) as z:
            H, W = int(z["h"]), int(z["w"])
            mask = np.unpackbits(z["packed"], count=H * W).astype(bool).reshape(H, W)
            bounds = tuple(float(v) for v in z["bounds"]) if "bounds" in z else None
        yield bounds, (H, W), mask


class WaterMaskError(RuntimeError):
    """Raised when the authoritative mask cannot be built — caller should fall back."""


def _merc_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(x / _R)
    lat = math.degrees(2.0 * math.atan(math.exp(y / _R)) - math.pi / 2.0)
    return lon, lat


def _lonlat_to_merc(lon: float, lat: float) -> tuple[float, float]:
    x = _R * math.radians(lon)
    y = _R * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))
    return x, y


def _cache_get(key: str):
    f = _CACHE_DIR / f"{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            return None
    return None


def _cache_put(key: str, obj) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.json").write_text(json.dumps(obj))
    except Exception:  # noqa: BLE001
        pass


_MIN_OVERPASS_INTERVAL = 1.1   # s between requests — pace steadily so the public instance
_last_overpass = [0.0]         # never sees a burst and never rate-limits us (freezing many
                               # is_in probes back-to-back otherwise triggers 429s that the
                               # hammer-then-back-off loop made worse). One-time freeze cost.


def _pace_overpass():
    import time as _t
    dt = _MIN_OVERPASS_INTERVAL - (_t.monotonic() - _last_overpass[0])
    if dt > 0:
        _t.sleep(dt)
    _last_overpass[0] = _t.monotonic()


def _overpass(query: str, *, timeout: float, cache_key: str, retries: int = 4):
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    last = None
    # Rotate mirrors AND back off exponentially: a non-JSON body is almost always an Overpass
    # rate-limit/overload page (HTTP 429/504), which freezing many is_in probes triggers in
    # bursts. Longer waits + a second host let a batch freeze of new stretches finish instead
    # of skipping the stretch (which silently drops coverage). Once frozen+committed, builds
    # never call Overpass at all, so this cost is one-time.
    for attempt in range(retries + 1):
        raw = b""
        endpoint = OVERPASS_ENDPOINTS[attempt % len(OVERPASS_ENDPOINTS)]
        _pace_overpass()
        try:
            proc = subprocess.run(
                ["curl", "-sS", "-m", str(int(timeout)),
                 "--data-urlencode", f"data={query}", endpoint],
                capture_output=True, timeout=timeout + 10)
            raw = proc.stdout
            if not raw:
                last = (f"empty response (curl rc={proc.returncode}: "
                        f"{proc.stderr[:160].decode('latin-1', 'replace')})")
            else:
                data = json.loads(raw.decode("utf-8", "replace"))
                _cache_put(cache_key, data)
                return data
        except json.JSONDecodeError:
            last = f"non-JSON response: {raw[:160].decode('latin-1', 'replace')!r}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(min(2.0 * (2 ** attempt), 12.0))   # 2,4,8,12s (pacing already avoids most 429s)
    raise WaterMaskError(f"Overpass request failed ({cache_key}): {last}")


def _fetch_barrier_ways(lon0, lat0, lon1, lat1):
    south, west, north, east = lat0, lon0, lat1, lon1
    b = f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"
    query = (
        "[out:json][timeout:90];"
        f"rel({b})[natural=water]->.wr;"
        f"way(r.wr)({b})->.rw;"
        f"way({b})[natural=water]->.ww;"
        f"way({b})[natural=coastline]->.cw;"
        "(.rw;.ww;.cw;);"
        "out geom;")
    key = "ways_" + hashlib.md5(b.encode()).hexdigest()[:16]
    data = _overpass(query, timeout=90, cache_key=key)
    ways = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        g = el.get("geometry")
        if not g:
            continue
        ways.append([(p["lon"], p["lat"]) for p in g])
    return ways


def _is_in_water(lat, lon):
    key = f"isin_{lat:.5f}_{lon:.5f}"
    query = f"[out:json][timeout:40];is_in({lat:.6f},{lon:.6f});out tags;"
    data = _overpass(query, timeout=40, cache_key=key)
    for el in data.get("elements", []):
        if el.get("tags", {}).get("natural") == "water":
            return True
    return False


def _rasterise_barriers(ways, bounds_3857, H, W):
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = bounds_3857
    dx = (x1 - x0) or 1e-9
    dy = (y1 - y0) or 1e-9
    img = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(img)
    for way in ways:
        pts = []
        for lon, lat in way:
            mx, my = _lonlat_to_merc(lon, lat)
            col = (mx - x0) / dx * W
            row = (y1 - my) / dy * H
            pts.append((col, row))
        if len(pts) >= 2:
            draw.line(pts, fill=1, width=1)
    return np.asarray(img, dtype=bool)


def _pixel_lonlat(row, col, bounds_3857, H, W):
    x0, y0, x1, y1 = bounds_3857
    mx = x0 + (col + 0.5) / W * (x1 - x0)
    my = y1 - (row + 0.5) / H * (y1 - y0)
    return _merc_to_lonlat(mx, my)


def water_mask(bounds_3857, shape_hw):
    """Authoritative boolean water mask (True == water) co-registered with (H, W) imagery.

    Raises WaterMaskError on any fetch/parse failure so the caller can fall back to the
    old imagery heuristic.
    """
    from scipy import ndimage

    H, W = int(shape_hw[0]), int(shape_hw[1])
    if H <= 0 or W <= 0:
        raise WaterMaskError(f"bad shape_hw {shape_hw!r}")
    frozen = _frozen_get(bounds_3857, H, W)   # COMMITTED mask wins — deterministic, offline
    if frozen is not None:
        return frozen
    x0, y0, x1, y1 = [float(v) for v in bounds_3857]
    lon0, lat0 = _merc_to_lonlat(min(x0, x1), min(y0, y1))
    lon1, lat1 = _merc_to_lonlat(max(x0, x1), max(y0, y1))

    ways = _fetch_barrier_ways(lon0, lat0, lon1, lat1)
    barrier = _rasterise_barriers(ways, (x0, y0, x1, y1), H, W)
    free = ~barrier
    lbl, n = ndimage.label(free)   # 4-connectivity: an 8-connected barrier can't leak
    if n == 0:
        raise WaterMaskError("no free space after rasterising barriers")

    dist = ndimage.distance_transform_edt(free)
    known = np.full((H, W), -1, dtype=np.int8)
    sizes = ndimage.sum(np.ones_like(lbl), lbl, index=np.arange(1, n + 1))
    order = np.argsort(sizes)[::-1]
    calls = 0
    classified_any = False
    for idx in order:
        label_id = idx + 1
        if sizes[idx] < _MIN_COMPONENT_PX:
            continue
        comp = lbl == label_id
        d = dist * comp
        r, c = np.unravel_index(int(np.argmax(d)), d.shape)
        lon, lat = _pixel_lonlat(r, c, (x0, y0, x1, y1), H, W)
        if calls >= _MAX_IS_IN_CALLS:
            break
        is_water = _is_in_water(lat, lon)
        calls += 1
        known[comp] = 1 if is_water else 0
        classified_any = True

    if not classified_any:
        raise WaterMaskError("no component large enough to classify")

    unknown = known < 0
    if unknown.any():
        _, (ir, ic) = ndimage.distance_transform_edt(unknown, return_indices=True)
        known = known[ir, ic]
    mask = known == 1
    try:                                  # freeze on first successful live build
        save_frozen_mask((x0, y0, x1, y1), mask)
    except Exception:  # noqa: BLE001
        pass
    return mask
