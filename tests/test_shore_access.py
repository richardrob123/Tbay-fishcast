"""Tests for the shoreline access classifier (ADR-046).

The safety property here is asymmetric and worth stating before the assertions: a false GREEN
sends an angler onto private property, a false RED keeps them off legal water. Both are wrong;
the first is worse. So the tests below are mostly about ambiguity landing in `unknown` instead of
in either colour — and about the two real-world traps (patented parks reading private,
Conservation Authorities holding title as Private) staying fixed.
"""
from __future__ import annotations

from tbay_fishcast.features import shore_access as sa


def E(**kw):
    return sa.Evidence(**kw)


def test_patented_park_is_public_not_private():
    """THE ORIGINAL BUG. 'Patented = private' marks Marina Park and the Mountdale launch red.
    Of 1,538 patented parcels in the domain, 329 are held by a government body."""
    assert sa.decide(E(holders=("Municipal Government",)))[0] == sa.PUBLIC
    assert sa.decide(E(holders=("Other Provincial Government Agency",)))[0] == sa.PUBLIC
    assert sa.decide(E(holders=("Federal Government",)))[0] == sa.PUBLIC
    assert sa.decide(E(holders=("Private",)))[0] == sa.PRIVATE


def test_reserve_land_overrides_every_public_signal():
    """THE FALSE-GREEN GUARD. 2.6 km of the traced shoreline sits inside Fort William 52, and
    reserve land carries exactly the federal / Crown attributes this classifier reads as PUBLIC.
    Nothing may promote it."""
    r = "Fort William 52 reserve land — public access is not implied; ask permission"
    for ev in (E(restricted=r), E(restricted=r, crown=True), E(restricted=r, park=True),
               E(restricted=r, holders=("Federal Government",), fap="somewhere")):
        assert sa.decide(ev) == (sa.PRIVATE, r)


def test_a_community_mapped_conservation_area_demotes_red_but_never_makes_green():
    """OSM is T3 and CLAUDE.md rule 3 puts access-legality at T1-or-field-verified. So a mapped
    Conservation Area may WITHDRAW a red claim (Little Trout Bay is held as Private title) and may
    never manufacture a green one — a false green is the error that puts someone on a lawn."""
    cls, why = sa.decide(E(holders=("Private",), reserve="Little Trout Bay Conservation Area"))
    assert cls == sa.UNKNOWN and "Little Trout Bay" in why
    assert sa.decide(E(reserve="Little Trout Bay Conservation Area"))[0] == sa.UNKNOWN, \
        "on its own it is not evidence of public access"
    assert sa.decide(E(restricted="Fort William 52", reserve="anything"))[0] == sa.PRIVATE


def test_regulated_designation_outranks_title():
    """A conservation reserve is public whoever holds the deed, so the park layer must win even
    when the parcel underneath reads Private."""
    cls, why = sa.decide(E(park=True, holders=("Private",)))
    assert cls == sa.PUBLIC and "park" in why


def test_private_title_next_to_an_official_access_point_is_unknown_not_red():
    """THE CENTRAL HONESTY TEST. Conservation Authorities hold title as 'Private', so Little Trout
    Bay and Silver Harbour — which exist FOR public fishing — look identical to a cottage lot.
    Demoting the conflict instead of resolving it took official access points reading PRIVATE
    from 8 to 0. Resolving it the other way (calling it public) would have been a false green."""
    cls, why = sa.decide(E(holders=("Private",), fap="Little Trout Bay"))
    assert cls == sa.UNKNOWN
    assert "official access" in why and "Little Trout Bay" in why


def test_crown_unpatented_is_public():
    assert sa.decide(E(crown=True)) == (sa.PUBLIC, "Crown unpatented")


def test_a_public_holder_beats_a_private_one_on_the_same_point():
    """Overlapping parcels are common at the water's edge. Positive public evidence wins; the
    alternative is a red segment over a parcel we can see is government-held."""
    assert sa.decide(E(holders=("Private", "Municipal Government")))[0] == sa.PUBLIC


def test_no_tenure_record_is_unknown_rather_than_either_colour():
    assert sa.decide(E()) == (sa.UNKNOWN, None)


def test_an_access_point_alone_vouches_for_public():
    cls, why = sa.decide(E(fap="Mountdale"))
    assert cls == sa.PUBLIC and "Mountdale" in why


def test_unrecognised_holder_string_is_unknown_not_guessed():
    """LIO can carry holder strings we have not seen. Mapping the unknown to either colour is
    inventing tenure."""
    cls, why = sa.decide(E(holders=("Patented - holder unspecified",)))
    assert cls == sa.UNKNOWN and why == "Patented - holder unspecified"


def test_every_outcome_is_one_of_the_three_declared_classes():
    from itertools import product
    seen = set()
    for park, crown, hold, fap in product((0, 1), (0, 1),
                                          ((), ("Private",), ("Municipal Government",), ("X",)),
                                          (None, "site")):
        cls, _ = sa.decide(E(park=bool(park), crown=bool(crown), holders=hold, fap=fap))
        assert cls in (sa.PUBLIC, sa.PRIVATE, sa.UNKNOWN)
        seen.add(cls)
    assert seen == {sa.PUBLIC, sa.PRIVATE, sa.UNKNOWN}, "all three classes must be reachable"


def test_fap_proximity_uses_a_longitude_correction():
    """At 48 deg N a degree of longitude is 2/3 of a degree of latitude. Without the cos(lat) term
    the search box is a third too narrow east-west and misses access points."""
    s = sa.ShoreAccess(faps=[("Silver Harbour", -89.0640, 48.4880)])
    assert s.near_fap(48.4880, -89.0640) == "Silver Harbour"
    assert s.near_fap(48.4880, -89.0640 + 0.0025) == "Silver Harbour"     # ~185 m east
    assert s.near_fap(48.4880, -89.0640 + 0.0060) is None                 # ~445 m east
    assert s.near_fap(48.4880 + 0.0030, -89.0640) is None                 # ~333 m north
