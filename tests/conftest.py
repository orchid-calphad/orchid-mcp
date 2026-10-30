"""Shared pytest setup for the ORCHID test suite: paths, a network guard, and the local-data Orchid."""

from __future__ import annotations

import os
import sys
import urllib.request
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import TestCase

import pytest

if TYPE_CHECKING:
    from orchid_mcp.tools import Orchid

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))

data_dir = Path(os.environ.get("ORCHID_TEST_DATA_DIR", Path.home() / "projects/orchid-data"))
site_dir = Path(os.environ.get("ORCHID_TEST_SITE_DATA_DIR", Path.home() / "projects/orchid-project.org/data"))

local_data_skip_reason = "local ORCHID data not found (set ORCHID_TEST_DATA_DIR / ORCHID_TEST_SITE_DATA_DIR)"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Make every test fail loudly if it tries to open a URL."""

    def refuse(*a, **k):  # tests must never touch the network
        """Raise instead of performing a network request."""
        raise AssertionError("network access attempted during tests")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@cache
def local_orchid() -> Orchid | None:
    """Return the Orchid over the local data, or None when that data is absent.

    The result is cached for the whole test session, as the former session-scoped fixture was.

    Returns:
        An Orchid over ``data_dir`` and ``site_dir``, or None if either index file is missing.
    """
    if not (data_dir / "literature/index.json").is_file() or not (site_dir / "catalog.json").is_file():
        return None
    from orchid_mcp.datasource import DataSource  # noqa: PLC0415 - lazy: only needed once the data check passes
    from orchid_mcp.tools import Orchid  # noqa: PLC0415 - lazy: same reason as the DataSource import above

    return Orchid(DataSource(data_dir), DataSource(site_dir))


class OrchidTestCase(TestCase):
    """Base TestCase whose tests skip when local ORCHID data is absent and expose ``self.orchid``."""

    orchid: Orchid

    def setUp(self) -> None:
        """Skip the test unless local ORCHID data is available, then attach the shared Orchid."""
        super().setUp()
        orchid = local_orchid()
        if orchid is None:
            self.skipTest(local_data_skip_reason)
        self.orchid = orchid


def server_env() -> dict[str, str]:
    """Build the environment for a spawned ORCHID server that reads the local data offline.

    Returns:
        A copy of ``os.environ`` with the data directories, offline mode and ``src`` on ``PYTHONPATH`` set.
    """
    return {
        **os.environ,
        "ORCHID_DATA_DIR": str(data_dir),
        "ORCHID_SITE_DATA_DIR": str(site_dir),
        "ORCHID_OFFLINE": "1",
        "PYTHONPATH": str(root / "src"),
    }
