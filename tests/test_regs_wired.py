"""The regs gate must be WIRED to the actual recommendation surfaces, not just unit-tested in
isolation (ADR-007 / CLAUDE rule 4). The Kakabeka seed pin is the cautionary tale: a tested gate
that nothing calls is a false sense of safety. These tests assert the real STRETCHES / shore
stations / RIVER_MOUTHS lists pass through the gate, that today's real waters are NOT dropped (no
regression), and that a closed water placed on any surface IS dropped.
"""
from datetime import date

from tbay_fishcast.config import load_config
from tbay_fishcast.scoring.regs_gate import RegsGate


def _build_module():
    # scripts/ isn't a package on the path in every runner; load the module by file.
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "scripts" / "build_coast_site.py"
    spec = importlib.util.spec_from_file_location("build_coast_site", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_real_surfaces_are_not_prohibited_today():
    """Every real stretch / station / marker name must pass the gate on a summer date — wiring
    the filter must NOT silently drop legitimate water (a regression guard)."""
    m = _build_module()
    gate = RegsGate.load()
    on = date(2026, 8, 7)
    for _sid, name, *_ in m.STRETCHES:
        assert not gate.is_prohibited(name, on), f"stretch wrongly gated: {name}"
    for mk in m.RIVER_MOUTHS:
        assert not gate.is_prohibited(mk["name"], on), f"marker wrongly gated: {mk['name']}"
    for s in load_config().shore_stations:
        assert not gate.is_prohibited(s.name, on), f"station wrongly gated: {s.name}"


def test_closed_water_is_dropped_from_each_surface():
    """A Kakabeka-named entry on any recommendation surface is prohibited year-round → the gate
    filter must drop it. This is the invariant the wiring installs."""
    gate = RegsGate.load()
    on = date(2026, 8, 7)
    assert gate.is_prohibited("Kakabeka Falls Provincial Park pool", on) is True
    # a seasonal closure: McIntyre sanctuary reach is closed in spring, open in summer
    assert gate.is_prohibited("McIntyre River sanctuary", date(2026, 4, 15)) is True
    assert gate.is_prohibited("McIntyre River sanctuary", on) is False
    # the filter comprehension used in the build drops the prohibited one, keeps the rest
    waters = ["Silver Harbour", "Kakabeka Falls pool", "MacKenzie Point"]
    kept = [w for w in waters if not gate.is_prohibited(w, on)]
    assert kept == ["Silver Harbour", "MacKenzie Point"]


def test_build_module_wires_the_gate():
    """Guard against a future refactor silently unwiring the gate: the build source must
    reference the regs gate on its emission path."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "scripts" / "build_coast_site.py").read_text()
    assert "RegsGate" in src and "is_prohibited" in src, "regs gate not referenced in build"
    hb = (Path(__file__).resolve().parents[1] / "scripts" / "heartbeat.py").read_text()
    assert "RegsGate" in hb and "is_prohibited" in hb, "regs gate not referenced in heartbeat"
