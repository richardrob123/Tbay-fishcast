"""Ontario LIO parcel / designation layers — the T1 land-tenure evidence behind shore access.

This module is FETCH ONLY. It knows how to pull a LIO layer completely and how to turn ArcGIS
ring soup into shapely polygons; it holds no opinion about what any of it means. The
public/private decision lives in ``features.shore_access`` so it can be tested without a network.

Two failure modes are engineered against here, because both produce confident WRONG colours
rather than an error:

  1. INCOMPLETE FETCH. ``resultOffset`` paging cannot be trusted on these layers: the Crown-parcel
     layer silently returned a 496-row second page — no ``exceededTransferLimit`` flag, no error —
     so an offset loop that stops on a short page collected 996 of 1,380 parcels. The missing
     polygons then read as `unknown` shoreline, i.e. a fetch bug wearing a data gap's clothes.
     Asking for the id list FIRST and pulling geometries in id batches makes completeness
     checkable, and :func:`fetch_layer` returns the id count so the caller can assert it.

  2. RINGS TREATED AS SOLIDS. ArcGIS packs a feature's exterior AND interior rings into one
     ``rings`` list. Treating every ring as its own solid polygon turns each HOLE into fake
     coverage — which is how Silver Harbour Conservation Area first came out `private`: it sits
     in the hole of the surrounding private parcel.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

LIO = ("https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/LIO_OPEN_DATA/"
       "LIO_Open{svc:02d}/MapServer/{lid}/query")

# The forecast domain: NW Lake Superior from the Minnesota border to Nipigon Bay.
BBOX = "-89.95,47.95,-87.95,48.90"

PATENT = dict(svc=8, lid=35, fields="TITLE_HOLDER_TYPE")   # granted out of the Crown (any holder)
CROWN = dict(svc=8, lid=34, fields="OGF_ID")               # Crown unpatented = public
FAP = dict(svc=7, lid=15, fields="SITE_NAME,FISHING_ACCESS_POINT_TYPE")
# POSITIVE public evidence, independent of who holds title. These are regulated designations, so
# they outrank TITLE_HOLDER_TYPE: a conservation reserve is public whoever the registered owner is.
PARKS = [dict(svc=3, lid=4, fields="OBJECTID"),            # Provincial Park Regulated
         dict(svc=3, lid=2, fields="OBJECTID"),            # Conservation Reserve Regulated
         dict(svc=3, lid=3, fields="OBJECTID")]            # Municipal Park
# NEGATIVE evidence, and the only layer here that can override everything else: reserve land holds
# federal / Crown attributes, which are precisely the ones read as PUBLIC elsewhere.
RESERVE = dict(svc=3, lid=12, fields="OFFICIAL_NAME")      # Indian Reserve

# LAYERS DELIBERATELY NOT USED, and why — this one looks like the fix and is a trap:
#
#   Conservation Authority Admin Area (LIO_Open03/11) — JURISDICTION, NOT OWNERSHIP. Its polygons
#   are whole watersheds (the Hamilton Region CA feature is 450 km2). Treating it as "public land"
#   would paint every private cottage lot inside a CA's jurisdiction green — far more dangerous
#   than the error it fixes, since a false GREEN sends someone onto private property while a
#   false RED only keeps them off legal water.


def _post(url: str, params: dict, timeout: int = 240):
    """POST rather than GET: an objectIds list of a few hundred blows past URL length limits."""
    body = urllib.parse.urlencode(params).encode()
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            if d.get("error"):
                raise RuntimeError(str(d["error"])[:120])
            return d
        except Exception:  # noqa: BLE001
            if attempt == 3:
                raise
            time.sleep(2 ** attempt * 2)
    return {}


def fetch_layer(spec: dict, want_geom: bool = True, where: str = "1=1", chunk: int = 150):
    """Fetch a LIO layer COMPLETELY within :data:`BBOX`.

    Returns ``(rows, n_ids)`` where each row is ``(attributes, rings_or_xy)`` and ``n_ids`` is how
    many object ids the service said exist. ``len(rows) != n_ids`` means the fetch is short and the
    caller must treat the result as unusable rather than as a data gap — see the module docstring.
    """
    url = LIO.format(**spec)
    base = {"where": where, "geometry": BBOX, "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "f": "json"}
    ids = _post(url, {**base, "returnIdsOnly": "true"}).get("objectIds") or []
    out = []
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        d = _post(url, {"objectIds": ",".join(str(x) for x in batch),
                        "outFields": spec["fields"], "returnGeometry": str(want_geom).lower(),
                        "outSR": "4326", "f": "json"})
        for f in d.get("features") or []:
            g = f.get("geometry") or {}
            out.append((f.get("attributes", {}), g.get("rings") or (g.get("x"), g.get("y"))))
    return out, len(ids)


def _signed_area(r) -> float:
    return sum((r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1]) for i in range(len(r) - 1)) / 2.0


def rings_to_polygons(rows, tag: str):
    """ArcGIS rings -> ``[(shapely.Polygon, attributes, tag)]``, honouring interior rings.

    Exteriors are clockwise (negative signed area in x/y order); holes are counter-clockwise.
    A feature with one shell keeps its holes; a multipart feature is split into its shells (its
    holes cannot be attributed to a specific shell without more work, and over-covering by a hole
    is the failure we are avoiding, so multiparts are conservatively left solid).
    """
    from shapely.geometry import Polygon

    out = []
    for at, rings in rows:
        if not rings or isinstance(rings, tuple):
            continue
        shells = [r for r in rings if len(r) >= 4 and _signed_area(r) < 0]
        holes = [r for r in rings if len(r) >= 4 and _signed_area(r) >= 0]
        if not shells:                      # degenerate / unsigned: fall back to all rings
            shells = [r for r in rings if len(r) >= 4]
            holes = []
        for sh in shells:
            try:
                g = Polygon(sh, holes if len(shells) == 1 else None)
                if not g.is_valid:
                    g = g.buffer(0)
                if not g.is_empty:
                    out.append((g, at, tag))
            except Exception:  # noqa: BLE001
                pass
    return out
