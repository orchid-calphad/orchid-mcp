"""Phase-registry tools over a small fixture (tests/fixtures/phases-data) and, when present, the real data."""

import json
from pathlib import Path
from unittest import TestCase

from conftest import OrchidTestCase

from orchid_mcp.datasource import DataSource
from orchid_mcp.tools import Orchid, OrchidError

fixture = Path(__file__).resolve().parent / "fixtures" / "phases-data"


class _FixtureCase(TestCase):
    """Base TestCase that builds one Orchid over the phases fixture for each test class."""

    fx: Orchid

    @classmethod
    def setUpClass(cls) -> None:
        """Build the fixture-backed Orchid once, before the first test of the class runs."""
        super().setUpClass()
        ds = DataSource(fixture)
        cls.fx = Orchid(ds, ds)


class TestGetPhase(_FixtureCase):
    """Tests for get_phase lookups by id, name and label, in summary and full modes."""

    def test_get_phase_by_id_name_and_label(self):
        """A phase resolves by id, name, label or tdb reference, and its summary carries structure, links and citation text."""
        r = self.fx.get_phase("Al-Ni", "phase:al-ni:fcc-l12")
        p = r["phase"]
        assert p["id"] == "phase:al-ni:fcc-l12" and p["system"] == "AL-NI"
        assert p["structure"]["tier"] == "A" and p["structure"]["prototype"] == "AuCu3"
        assert p["models"] and p["models"][0]["phase_statement"].startswith("PHASE L12_FCC")
        assert "tdb_definition" not in json.dumps(p)
        assert len(p["references"]) <= 5 and all("doi" in x for x in p["references"])
        assert p["viewer_url"].endswith("/phases/#phase:al-ni:fcc-l12") and p["data_url"].endswith("phases/systems/AL-NI.json")
        assert p["static_page_url"].endswith("phases/pages/AL-NI.html#phase-al-ni-fcc-l12")
        assert "Hallstedt" in r["how_to_cite_phase_registry"] and r["orchid"]["release"]
        assert self.fx.get_phase("Ni-Al", "l12_fcc")["phase"]["id"] == "phase:al-ni:fcc-l12"
        assert self.fx.get_phase("AL-NI", "tdb:AlNi-01Dup-LB.tdb:L12_FCC")["phase"]["id"] == "phase:al-ni:fcc-l12"
        assert self.fx.get_phase("", "phase:ni-ti:ni3ti-d024")["phase"]["system"] == "NI-TI"
        assert self.fx.get_phase("Ni-Ti", "NI3TI")["phase"]["id"] == "phase:ni-ti:ni3ti-d024"

    def test_get_phase_full_and_errors(self):
        """Full mode adds verbatim TDB text and provenance, and unknown phases, bad fields or systems raise OrchidError."""
        full = self.fx.get_phase("Al-Ni", "phase:al-ni:fcc-l12", fields="full")["phase"]
        td = full["models"][0]["tdb_definition"]
        assert td["verbatim"]["phase"]["text"].startswith("PHASE L12_FCC")
        assert td["verbatim"]["constituent"]["text"].startswith("CONST")
        assert td["provenance"]["sha256"] and full["references"][0]["citation"]
        amb = self.fx.get_phase("Al-Ni", "fcc")
        assert amb.get("ambiguous") or amb["phase"]["id"].startswith("phase:al-ni:fcc")
        with self.assertRaises(OrchidError):
            self.fx.get_phase("Al-Ni", "no-such-phase")
        with self.assertRaises(OrchidError):
            self.fx.get_phase("Al-Ni", "fcc", fields="everything")
        with self.assertRaises(OrchidError):
            self.fx.get_phase("Cu-Zr", "liquid")


class TestSearchPhases(_FixtureCase):
    """Tests for search_phases filtering and paging over the phase registry fixture."""

    def test_search_phases(self):
        """Phases filter by element, prototype, tier and name, page correctly, and reject an unknown tier."""
        allp = self.fx.search_phases()
        assert allp["total"] == 5 and not allp["truncated"]
        assert {p["id"] for p in self.fx.search_phases(["Ti"])["phases"]} == {"phase:ni-ti:ni3ti-d024", "phase:ni-ti:niti-b2"}
        assert [p["id"] for p in self.fx.search_phases(prototype="L1_2")["phases"]] == ["phase:al-ni:fcc-l12"]
        assert [p["id"] for p in self.fx.search_phases(prototype="l12")["phases"]] == ["phase:al-ni:fcc-l12"]
        assert self.fx.search_phases(prototype="cP2")["total"] == 1
        assert {p["id"] for p in self.fx.search_phases(tier="liquid")["phases"]} == {"phase:al-ni:liquid"}
        assert self.fx.search_phases(name="ni3ti")["total"] == 1
        page = self.fx.search_phases(limit=2, offset=2)
        assert page["returned"] == 2 and page["offset"] == 2 and page["truncated"]
        with self.assertRaises(OrchidError):
            self.fx.search_phases(tier="Z")


class TestGetStructureType(_FixtureCase):
    """Tests for get_structure_type lookups by prototype label, alias or id."""

    def test_get_structure_type(self):
        """Each alias resolves to the same structure type, which lists its example and registry phases, and unknown labels raise."""
        for q in ("L1_2", "L12", "AuCu3", "cP4", "st:l1-2-aucu3-cp4"):
            r = self.fx.get_structure_type(q)
            assert r["structure_types"][0]["id"] == "st:l1-2-aucu3-cp4", q
        r = self.fx.get_structure_type("L12")["structure_types"][0]
        assert r["example_phases"] == ["phase:al-ni:fcc-l12"] and r["registry_phases"] >= 1
        assert self.fx.get_structure_type("D0_19")["structure_types"][0]["id"] == "st:d0-19-ni3sn-hp8"
        with self.assertRaises(OrchidError):
            self.fx.get_structure_type("zz99")


class TestPhaseToolsRealData(OrchidTestCase):
    """Tests for the phase tools against the real local ORCHID data."""

    def test_phase_tools_real_data(self):
        """Summary is smaller than full, and the real registry has enough phases for element and prototype searches."""
        if not self.orchid.data.exists("phases/registry.json"):
            self.skipTest("local data has no phase registry")
        s = self.orchid.get_phase("Al-Ni", "phase:al-ni:fcc-l12")
        f = self.orchid.get_phase("Al-Ni", "phase:al-ni:fcc-l12", fields="full")
        assert len(json.dumps(s)) < len(json.dumps(f)) < 200_000
        assert self.orchid.search_phases(["Al", "Ni"])["total"] >= 10
        assert self.orchid.get_structure_type("L1_2")["structure_types"][0]["registry_phases"] > 10
