import json
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """ENFORCE hermeticity (CLAUDE rule 2 / AUDIT_ROUND3): the suite claims to be
    fixture-only, but nothing guarded it — a test that silently hit the network would
    pass locally and flake in CI. Any socket connection now fails loudly."""
    def _blocked(*a, **k):
        raise RuntimeError("network access blocked in tests — use a fixture")
    monkeypatch.setattr(socket.socket, "connect", _blocked)

FIXTURES = REPO / "tests" / "fixtures"
LSOFS_FIXTURE = FIXTURES / "lsofs_tbay_subset.nc"
GRID_META = FIXTURES / "grid_meta.json"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture(scope="session")
def lsofs_fixture() -> Path:
    assert LSOFS_FIXTURE.exists(), "run scripts/bootstrap_grid.py to build the fixture"
    return LSOFS_FIXTURE


@pytest.fixture(scope="session")
def grid_meta() -> dict:
    return json.loads(GRID_META.read_text())


@pytest.fixture(scope="session")
def config():
    from tbay_fishcast.config import load_config
    return load_config()
