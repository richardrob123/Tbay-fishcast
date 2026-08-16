"""Named conservation areas / nature reserves from OSM — DEMOTION EVIDENCE ONLY (ADR-046).

Why this exists. Ontario's parcel fabric records who holds title, and Conservation Authorities
hold theirs as "Private". Little Trout Bay and Hurkett Cove are Conservation Areas that exist for
public recreation, and both read `Private` in LIO. Painting them red is the safe direction of
error, but it is still wrong, and they are places this forecast already models.

Why it is demotion-only. CLAUDE.md rule 3 puts access-legality at T1-or-field-verified, and OSM is
T3. So this layer is never allowed to make anything GREEN. Its only power is to turn a would-be
RED into UNKNOWN — which is not an access claim at all, it is declining to make one in the face of
contradicting evidence. That is the same move the Fishing Access Point cross-check already makes,
and by construction it cannot produce a false green.

KNOWN GAP, stated rather than hidden: Silver Harbour Conservation Area is not tagged as a nature
reserve in OSM, so it is not demoted by this layer. Part of its frontage is already demoted by the
province's own "Lake Superior - Silver Harbour Access" point.
"""
from __future__ import annotations

import hashlib

from .watermask import _overpass

# Deliberately a tag query, not a name regex: `nwr["name"~"Conservation Area"]` over this bbox
# times out on the public Overpass instance (504), and tag queries are what the index is for.
_QUERY = """[out:json][timeout:120];
(way["leisure"="nature_reserve"]({s},{w},{n},{e});
 relation["leisure"="nature_reserve"]({s},{w},{n},{e});
 way["boundary"="protected_area"]({s},{w},{n},{e});
 relation["boundary"="protected_area"]({s},{w},{n},{e}););
out geom;"""


def fetch_reserves(bbox=(47.95, -89.95, 48.90, -87.95)):
    """-> ``[(name, [(lon, lat), ...] ring), ...]`` for every named reserve in the bbox.

    Unnamed features are dropped: an anonymous polygon cannot be shown to the user as a reason,
    and a demotion the map cannot explain is indistinguishable from a bug.
    """
    s, w, n, e = bbox
    # THE QUERY IS PART OF THE KEY, not just the bbox. watermask._overpass returns a cached
    # result with no TTL and no query fingerprint, so editing _QUERY — say to add
    # leisure=park / boundary=national_park, which this module's own docstring notes is needed
    # because Silver Harbour Conservation Area is not tagged leisure=nature_reserve — would
    # return the PRE-CHANGE element set with no HTTP request and no visible difference between
    # "the new query found nothing new" and "the new query never ran". This is the
    # access-legality layer, which CLAUDE.md rule 4 requires be tested like a security
    # invariant: the system must be incapable of recommending closed water.
    qh = hashlib.sha256(_QUERY.encode()).hexdigest()[:10]
    key = f"protected_{s}_{w}_{n}_{e}_{qh}"
    data = _overpass(_QUERY.format(s=s, w=w, n=n, e=e), timeout=180, cache_key=key)
    out = []
    for el in data.get("elements", []):
        name = (el.get("tags") or {}).get("name")
        if not name:
            continue
        if el["type"] == "way":
            rings = [el.get("geometry") or []]
        else:
            rings = [m.get("geometry") or [] for m in el.get("members", [])
                     if m.get("role") in ("outer", "")]
        for g in rings:
            if len(g) >= 4:
                out.append((name, [(p["lon"], p["lat"]) for p in g]))
    return out
