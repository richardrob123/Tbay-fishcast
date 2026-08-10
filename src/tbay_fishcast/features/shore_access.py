"""Public / private / unknown shoreline access from Ontario land tenure (ADR-046).

The map colours the very edge of the shore so you can see where you may legally stand. Three
classes, and UNKNOWN is a real answer rather than a failure to decide.

WHY THE OBVIOUS RULE IS WRONG. "Patented land = private" is the natural reading and it marks
Marina Park, Silver Harbour Conservation Area and the Mountdale boat launch as PRIVATE — three of
the most-used legal access points in Thunder Bay. Patented merely means granted out of the Crown;
a city park is patented land. Of 1,538 patented parcels in this domain, 1,209 are Private and 329
are Municipal / provincial-agency / Federal. So ``TITLE_HOLDER_TYPE`` is necessary.

WHY IT IS ALSO NOT SUFFICIENT. Title records who OWNS land, not who may walk on it, and
Conservation Authorities hold theirs as "Private": Little Trout Bay, Hazelwood Lake and Silver
Harbour exist FOR public recreation and every one of them reads Private. No layer in this open
dataset carries accessibility directly. So private title that CONFLICTS with an official fishing
access point is demoted to `unknown` rather than resolved — see :func:`decide`.

THE ASYMMETRY THAT SETS EVERY TIE-BREAK. A false GREEN sends someone onto private property; a
false RED only keeps them off legal water. Both are errors, but they are not the same size, and
this module is tuned so that ambiguity lands in `unknown` (yellow) rather than in either colour.

Provenance: T1 (Ontario LIO parcel fabric + regulated park/reserve + Fishing Access Points).
Ownership is measured; ACCESSIBILITY is not — a public bank can still be a cliff. That is why the
green class means "public land", never "you can get down to the water here".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Holders that make land public on their face. "Provincial Government" and its agencies, the
# municipality, and the federal Crown. Everything else is either "Private" or an unrecognised
# string, and an unrecognised string must not be guessed at.
PUBLIC_HOLDERS = frozenset({"Municipal Government", "Other Provincial Government Agency",
                            "Federal Government", "Provincial Government"})

# An official access point vouches for the shoreline immediately around it. 250 m is the distance
# at which the province's own point geometry stops being informative about a parcel: FAP records
# are single points for sites that are commonly a few hundred metres of frontage.
FAP_PUBLIC_M = 250.0

PUBLIC, PRIVATE, UNKNOWN = "public", "private", "unknown"


@dataclass
class Evidence:
    """Everything known about one location, before any judgement is applied."""
    park: bool = False                    # inside a regulated park / conservation reserve
    crown: bool = False                   # inside Crown unpatented land
    holders: tuple = ()                   # TITLE_HOLDER_TYPE of every patented parcel covering it
    fap: str | None = None                # name of an official fishing access point within 250 m
    restricted: str | None = None         # named land where public access is NOT implied (T1)
    reserve: str | None = None            # named conservation area, community-mapped (T3)


def decide(ev: Evidence) -> tuple[str, str | None]:
    """Evidence -> (class, reason). Pure; no network, no geometry.

    Precedence, strongest evidence first:

      0. Restricted land — a First Nation reserve. Overrides everything, including Crown and
         federal title, because those are exactly the attributes reserve land carries. 2.6 km of
         our traced shoreline lies inside Fort William 52, and nothing about federal title there
         implies a right to walk in and fish.
      1. Regulated public designation (park / conservation reserve). Outranks ordinary title,
         because a conservation reserve is public whoever holds the deed.
      2. Crown unpatented, or title held by a government body.
      3. Private title — but it only becomes RED if nothing contradicts it.

    Step 3 is what the design turns on. When private title sits next to an official fishing access
    point, that is CONFLICTING evidence, and the honest output is `unknown`, not a confident red
    telling someone to stay off water the province publishes as a place to fish. Demoting the
    conflict rather than resolving it took the count of official access points reading PRIVATE
    from 8 to 0. ``reserve`` works the same way and is likewise demotion-only — it is T3, and
    CLAUDE.md rule 3 puts access-legality at T1-or-field-verified, so it may withdraw a red claim
    but may never manufacture a green one.
    """
    if ev.restricted:
        return PRIVATE, ev.restricted
    if ev.park:
        return PUBLIC, "regulated park/reserve"
    public_holder = next((h for h in ev.holders if h in PUBLIC_HOLDERS), None)
    if public_holder:
        return PUBLIC, public_holder
    if ev.crown:
        return PUBLIC, "Crown unpatented"
    if not ev.holders:
        # No parcel at all. An official access point is still positive public evidence; otherwise
        # we simply have no tenure record here and must say so.
        return (PUBLIC, f"official access: {ev.fap}") if ev.fap else (UNKNOWN, None)
    if "Private" in ev.holders:
        if ev.fap:
            return UNKNOWN, f"private title but official access nearby ({ev.fap})"
        if ev.reserve:
            return UNKNOWN, f"private title inside {ev.reserve}"
        return PRIVATE, "Private"
    # A patented parcel whose holder string we do not recognise. Guessing either way would be
    # inventing tenure; the class exists for exactly this.
    return UNKNOWN, next((h for h in ev.holders if h), None)


def classify_evidence(ev: Evidence) -> str:
    return decide(ev)[0]


@dataclass
class ShoreAccess:
    """Spatial index over the LIO layers, answering :meth:`classify` per point.

    Build with :func:`from_layers`; it is deliberately a plain container so the decision logic in
    :func:`decide` stays testable without shapely or a network.
    """
    parcels: list = field(default_factory=list)     # [(Polygon, attrs, "patent"|"crown")]
    parks: list = field(default_factory=list)       # [Polygon]
    faps: list = field(default_factory=list)        # [(name, lon, lat)]
    restricted: list = field(default_factory=list)  # [(Polygon, name)] First Nation reserve
    reserves: list = field(default_factory=list)    # [(Polygon, name)] community-mapped, T3
    _tree: object = None
    _park_tree: object = None
    _restricted_tree: object = None
    _reserve_tree: object = None

    def _named_hit(self, tree, items, p) -> str | None:
        if tree is None:
            return None
        for i in tree.query(p):
            if items[i][0].covers(p):
                return items[i][1]
        return None

    def evidence(self, lat: float, lon: float) -> Evidence:
        from shapely.geometry import Point

        p = Point(lon, lat)
        restricted = self._named_hit(self._restricted_tree, self.restricted, p)
        if restricted:
            # short-circuit: nothing below can change the answer, and the parcel query is the
            # expensive part of a 4,000-sample build
            return Evidence(restricted=restricted)
        park = False
        if self._park_tree is not None:
            for i in self._park_tree.query(p):
                if self.parks[i].covers(p):
                    park = True
                    break
        crown = False
        holders: list[str] = []
        if self._tree is not None:
            for i in self._tree.query(p):
                geom, at, tag = self.parcels[i]
                if not geom.covers(p):
                    continue
                if tag == "crown":
                    crown = True
                else:
                    th = at.get("TITLE_HOLDER_TYPE")
                    if th:
                        holders.append(str(th))
        return Evidence(park=park, crown=crown, holders=tuple(holders),
                        fap=self.near_fap(lat, lon),
                        reserve=self._named_hit(self._reserve_tree, self.reserves, p))

    def near_fap(self, lat: float, lon: float) -> str | None:
        dlat = FAP_PUBLIC_M / 111000.0
        dlon = dlat / max(0.2, math.cos(math.radians(lat)))
        for name, flon, flat in self.faps:
            if abs(flat - lat) <= dlat and abs(flon - lon) <= dlon:
                return name
        return None

    def classify(self, lat: float, lon: float) -> tuple[str, str | None]:
        return decide(self.evidence(lat, lon))


def from_layers(patent_rows, crown_rows, park_rows, fap_rows,
                reserve_rows=(), osm_reserves=()) -> ShoreAccess:
    """Assemble a :class:`ShoreAccess` from raw ``ingest.lio_parcels.fetch_layer`` output.

    ``reserve_rows`` is the LIO Indian Reserve layer (T1, restricts); ``osm_reserves`` is
    ``[(name, ring)]`` from ``ingest.osm_protected`` (T3, demotes only).
    """
    from shapely.geometry import Polygon
    from shapely.strtree import STRtree

    from ..ingest import lio_parcels as lio

    parcels = lio.rings_to_polygons(patent_rows, "patent") + \
        lio.rings_to_polygons(crown_rows, "crown")
    parks = [g for g, _, _ in lio.rings_to_polygons(park_rows, "park")]
    restricted = [(g, f"{at.get('OFFICIAL_NAME') or 'First Nation'} reserve land — "
                      f"public access is not implied; ask permission")
                  for g, at, _ in lio.rings_to_polygons(reserve_rows, "reserve")]
    reserves = []
    for name, ring in osm_reserves:
        try:
            g = Polygon(ring)
            if not g.is_valid:
                g = g.buffer(0)
            if not g.is_empty:
                reserves.append((g, name))
        except Exception:  # noqa: BLE001
            pass
    faps = []
    for at, xy in fap_rows:
        if isinstance(xy, tuple) and xy[0] is not None:
            faps.append((at.get("SITE_NAME") or "access point", float(xy[0]), float(xy[1])))
    sa = ShoreAccess(parcels=parcels, parks=parks, faps=faps,
                     restricted=restricted, reserves=reserves)
    sa._tree = STRtree([g for g, _, _ in parcels]) if parcels else None
    sa._park_tree = STRtree(parks) if parks else None
    sa._restricted_tree = STRtree([g for g, _ in restricted]) if restricted else None
    sa._reserve_tree = STRtree([g for g, _ in reserves]) if reserves else None
    return sa
