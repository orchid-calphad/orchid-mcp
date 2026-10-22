import os
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DATA_DIR = Path(os.environ.get("ORCHID_TEST_DATA_DIR", Path.home() / "projects/orchid-data"))
SITE_DIR = Path(os.environ.get("ORCHID_TEST_SITE_DATA_DIR", Path.home() / "projects/orchid-project.org/data"))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*a, **k):  # tests must never touch the network
        raise AssertionError("network access attempted during tests")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture(scope="session")
def orchid():
    if not (DATA_DIR / "literature/index.json").is_file() or not (SITE_DIR / "catalog.json").is_file():
        pytest.skip("local ORCHID data not found (set ORCHID_TEST_DATA_DIR / ORCHID_TEST_SITE_DATA_DIR)")
    from orchid_mcp.datasource import DataSource
    from orchid_mcp.tools import Orchid

    return Orchid(DataSource(DATA_DIR), DataSource(SITE_DIR))


@pytest.fixture()
def server_env():
    return {
        **os.environ,
        "ORCHID_DATA_DIR": str(DATA_DIR),
        "ORCHID_SITE_DATA_DIR": str(SITE_DIR),
        "ORCHID_OFFLINE": "1",
        "PYTHONPATH": str(ROOT / "src"),
    }
