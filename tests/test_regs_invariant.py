"""Safety invariant (ADR-007, CLAUDE rule 4): the system is INCAPABLE of
recommending closed or prohibited water. Tested like a security property.

Origin: the seed KML had a community pin inside Kakabeka (closed water). A
recommender must never surface it.
"""
from datetime import date

import pytest

from tbay_fishcast.scoring.regs_gate import RegsGate, load_regs


@pytest.fixture(scope="module")
def gate():
    return RegsGate.load()


def test_kakabeka_always_prohibited(gate):
    for d in [date(2026, 1, 15), date(2026, 7, 1), date(2026, 12, 31)]:
        assert gate.is_prohibited("Kakabeka Falls below the falls", d) is True


def test_kakabeka_never_recommendable(gate):
    waters = ["Kaministiquia River", "Kakabeka Falls Provincial Park", "Boulevard Lake"]
    keep = gate.filter_recommendable(waters, date(2026, 8, 4))
    assert "Kakabeka Falls Provincial Park" not in keep
    assert "Kaministiquia River" in keep  # open water survives


def test_mcintyre_sanctuary_seasonal(gate):
    # closed Apr 1 - May 31, open otherwise
    assert gate.is_prohibited("McIntyre River footbridge to dam", date(2026, 4, 15)) is True
    assert gate.is_prohibited("McIntyre River footbridge to dam", date(2026, 6, 1)) is False
    assert gate.is_prohibited("McIntyre River footbridge to dam", date(2026, 8, 4)) is False


def test_open_water_not_prohibited(gate):
    assert gate.is_prohibited("Silver Harbour outer rocks", date(2026, 8, 4)) is False


def test_validate_flags_below_tier_regs(gate):
    """The seed's marina_dock_zones is T2 and must be flagged as a go-live blocker
    (ADR-007: regs are T1-only)."""
    violations = gate.validate()
    assert any("marina_dock_zones" in v for v in violations)


def test_validate_requires_verified_on():
    regs = load_regs()
    assert all(e.verified_on for e in regs), "every regs entry needs verified_on"


def test_hard_closure_fails_closed_if_backing_entry_removed():
    """If the Kakabeka regs entry vanished, the gate must still refuse that water
    AND report an integrity violation (fail-closed, never silently open)."""
    regs = [e for e in load_regs() if e.id != "kakabeka_park_closed"]
    g = RegsGate(regs)
    assert g.is_prohibited("Kakabeka Falls", date(2026, 8, 4)) is True
    assert any("kakabeka_park_closed" in v for v in g.validate())
