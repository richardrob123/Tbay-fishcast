import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

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
