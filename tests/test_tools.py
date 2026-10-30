"""Tools over the local ORCHID data (each test skips when that data is absent), plus chemistry helpers and resources."""

import hashlib
import json
from unittest import TestCase

from conftest import OrchidTestCase

from orchid_mcp.chem import parse_formula, system_id
from orchid_mcp.tools import OrchidError, cite_orchid_prompt

provenance_keys = {"paper", "citation", "doi", "page", "quote"}
summary_keys = {
    "id",
    "property",
    "value",
    "unit",
    "T_K",
    "phases",
    "evidence_class",
    "accepted",
    "espei_ready",
    "espei_certified",
    "calphad_check",
    "citation",
    "doi",
    "page",
    "quote",
}


def _check_value(v):
    """Assert that a full-mode value record carries its evidence class, flags, and complete source provenance.

    Args:
        v (dict): One value record from a ``fields="full"`` response.
    """
    assert v["evidence_class"] in {"experimental", "model", "dft", "unspecified"}
    assert "flags" in v and "origin" in v
    assert provenance_keys <= set(v["source"])
    assert v["source"]["citation"]
    assert v["source"]["page"] is not None
    if v["record_kind"] == "running_text":
        assert v["source"]["quote"]
    else:
        assert v["locator"].get("label") is not None  # table locator instead of a sentence quote


def _check_summary(v):
    """Assert that a summary-mode value record has its required keys and no full-mode-only fields.

    Args:
        v (dict): One value record from a ``fields="summary"`` response.
    """
    assert summary_keys <= set(v)
    assert v["evidence_class"] in {"experimental", "model", "dft", "unspecified"}
    assert v["citation"] and v["page"] is not None
    assert v["quote"] or v.get("table")  # sentence quote or table locator
    assert v["quote"] is None or len(v["quote"]) <= 240
    assert "flags" not in v and "source" not in v
    assert v["calphad_check"] in {"agrees", "disagrees", "not_available"}
    assert not v["espei_certified"] or (v["espei_ready"] and v["calphad_check"] == "agrees")


class TestChem(TestCase):
    """Tests for the chemistry helpers that normalise system names and parse formulas."""

    def test_chem(self):
        """System names normalise to sorted element pairs, formulas parse to fractions, and ambiguous input returns None."""
        assert system_id("Ni-Al") == "AL-NI" and system_id(["ti", "ni"]) == "NI-TI" and system_id("CuZr") == "CU-ZR"
        assert parse_formula("Al3Ni") == {"AL": 0.75, "NI": 0.25}
        assert parse_formula("γ′-Ni3Al (L12)") == {"AL": 0.25, "NI": 0.75}
        assert parse_formula("AlNi3 and Al3Ni") is None


class TestSearchSystems(OrchidTestCase):
    """Tests for search_systems matching, paging and the response envelope."""

    def test_search_systems(self):
        """Systems match by element set, exact mode, or text; full mode and paging add their fields; the response cites the release."""
        r = self.orchid.search_systems(["Al", "Ni"])
        assert r["systems"][0]["system"] == "AL-NI"
        assert r["systems"][0]["calculated_phase_diagrams"] >= 1
        assert all({"AL", "NI"} <= set(s["system"].split("-")) for s in r["systems"])
        full = self.orchid.search_systems(["Al", "Ni"], fields="full")
        assert all({"AL", "NI"} <= set(s["elements"]) for s in full["systems"])
        assert full["systems"][0]["by_property_group"] and full["systems"][0]["explorer_url"]
        page2 = self.orchid.search_systems(["Al"], limit=5, offset=5)
        assert page2["returned"] == 5 and page2["offset"] == 5 and page2["total"] > 10
        exact = self.orchid.search_systems(["Zr", "Cu"], exact=True)
        assert [s["system"] for s in exact["systems"]] == ["CU-ZR"]
        assert "NI-TI" in [s["system"] for s in self.orchid.search_systems(text="Ni-Ti")["systems"]]
        assert r["orchid"]["release"] and r["orchid"]["licence"]


class TestGetSystemData(OrchidTestCase):
    """Tests for get_system_data values, summary and full modes, paging, and uncertainty and ML fields."""

    def test_get_system_data_al_ni(self):
        """Full Al-Ni values pass the provenance checks, cover both record kinds, and respect the evidence and group filters."""
        r = self.orchid.get_system_data("Ni-Al", limit=500, fields="full")
        assert r["system"] == "AL-NI" and r["returned"] == r["total_matching"] > 100
        for v in r["values"]:
            _check_value(v)
            assert v["flags"]["extraction_accepted"] is True
        kinds = {v["record_kind"] for v in r["values"]}
        assert {"running_text", "invariant_table"} <= kinds
        exp = self.orchid.get_system_data("AL-NI", evidence_class="experimental", property_group="formation", fields="full")
        assert exp["values"] and all(v["evidence_class"] == "experimental" for v in exp["values"])
        assert all(v["property_group"] == "formation" for v in exp["values"])

    def test_get_system_data_summary_default(self):
        """The default response is a summary page of accepted values whose full mode lists the same ids in the same order."""
        r = self.orchid.get_system_data("CU-ZR")
        assert r["fields"] == "summary" and r["returned"] == 50 and r["total"] == r["total_matching"] > 50
        assert r["truncated"] and r["next_offset"] == 50
        for v in r["values"]:
            _check_summary(v)
            assert v["accepted"] is True
        full = self.orchid.get_system_data("CU-ZR", fields="full")
        assert [v["id"] for v in full["values"]] == [v["id"] for v in r["values"]]
        for v in full["values"]:
            _check_value(v)
        inv = [v for v in r["values"] if v.get("reaction")]
        assert inv and all(v["T_K"] for v in inv)
        with self.assertRaises(OrchidError):
            self.orchid.get_system_data("CU-ZR", fields="everything")

    def test_get_system_data_pagination_and_errors(self):
        """Consecutive pages are disjoint with the same total, and an unknown system or evidence class raises OrchidError."""
        a = self.orchid.get_system_data("CU-ZR", limit=10)
        b = self.orchid.get_system_data("CU-ZR", limit=10, offset=10)
        assert a["truncated"] and {v["id"] for v in a["values"]}.isdisjoint({v["id"] for v in b["values"]})
        assert a["total"] == b["total"]
        with self.assertRaises(OrchidError):
            self.orchid.get_system_data("XX-YY")
        with self.assertRaises(OrchidError):
            self.orchid.get_system_data("AL-NI", evidence_class="guess")

    def test_summary_uncertainty_budget(self):
        """Each formation value's uncertainty budget keeps sigma_effective at or above the printed and ESPEI floors, and scatter is used somewhere."""
        r = self.orchid.get_system_data("CU-ZR", evidence_class="experimental", property_group="formation", limit=500)
        vals = [v for v in r["values"] if v["property"] == "HM_FORM"]
        assert vals
        for v in vals:
            ub = v["uncertainty_budget"]
            q = ub["quantities"]["value"]
            assert ub["sigma_rule"].startswith("max(") and ub["calibration"]
            assert set(q) >= {
                "unit",
                "sigma_printed",
                "sigma_reading",
                "sigma_method",
                "sigma_composition",
                "sigma_scatter",
                "sigma_conversion",
                "sigma_effective",
            }
            if q["sigma_effective"] is not None:
                assert q["sigma_effective"] >= 0.5 - 1e-9  # never below ESPEI's default (kJ/mol-atom)
                if q["sigma_printed"] is not None:
                    assert q["sigma_effective"] >= q["sigma_printed"] - 1e-9
        assert any(v["uncertainty_budget"]["quantities"]["value"]["sigma_scatter"] for v in vals)

    def test_summary_ml_folds(self):
        """Machine-learning folds in record summaries: deterministic sha256(key) mod 5, same as the release."""
        r = self.orchid.get_system_data("CU-ZR", limit=50)
        assert r["values"]
        for v in r["values"]:
            assert v["chemical_system"] == "CU-ZR"
            assert v["fold_lso"] == int(hashlib.sha256(b"CU-ZR").hexdigest(), 16) % 5
            assert v["paper_id"] and v["fold_lpo"] == int(hashlib.sha256(v["paper_id"].encode()).hexdigest(), 16) % 5


class TestGetInvariantReactions(OrchidTestCase):
    """Tests for get_invariant_reactions over literature and calculated invariants."""

    def test_invariants_ni_ti(self):
        """Literature and calculated invariants both appear with their checks, and the source filter drops the calculated set."""
        r = self.orchid.get_invariant_reactions("Ni-Ti", fields="full", limit=1000)
        assert r["literature"] and r["calculated"]
        assert len(r["literature"]) == r["literature_total"]
        for v in r["literature"]:
            _check_value(v)
            assert v["unit"] == "K"
        s = self.orchid.get_invariant_reactions("Ni-Ti", limit=5)
        assert len(s["literature"]) == min(5, s["literature_total"])
        for v in s["literature"]:
            _check_summary(v)
        calc = r["calculated"][0]
        assert len(calc["tdb_sha256"]) == 64 and calc["assessment_citation"]
        assert calc["evidence_class"] == "model" and calc["invariants"]
        lit_only = self.orchid.get_invariant_reactions("NI-TI", source="literature")
        assert "calculated" not in lit_only
        with self.assertRaises(OrchidError):
            self.orchid.get_invariant_reactions("NI-TI", source="nope")

    def test_invariant_matching_al_ni(self):
        """At least one calculated Al-Ni invariant lies within 1 K of a literature invariant."""
        r = self.orchid.get_invariant_reactions("AL-NI", assessment="AlNi-05Mie")
        matches = [m for inv in r["calculated"][0]["invariants"] for m in inv.get("literature_matches", [])]
        assert any(abs(m["dT_K"]) < 1 for m in matches)

    def test_results_are_json(self):
        """The Cu-Zr invariant result serialises to JSON without error."""
        json.dumps(self.orchid.get_invariant_reactions("CU-ZR"))


class TestGetFormationEnthalpy(OrchidTestCase):
    """Tests for get_formation_enthalpy on the Al3Ni composition."""

    def test_formation_enthalpy_al3ni(self):
        """Al3Ni returns literature and two DFT references with plausible energies and a summary, and a bare element raises."""
        r = self.orchid.get_formation_enthalpy("Al3Ni", fields="full")
        assert r["system"] == "AL-NI" and r["composition"] == {"AL": 0.75, "NI": 0.25}
        assert r["literature"]
        for v in r["literature"]:
            _check_value(v)
            assert v["unit"] == "kJ/mol-atom"
        for v in self.orchid.get_formation_enthalpy("Al3Ni")["literature"]:
            _check_summary(v)
        for src in ("mp", "oqmd"):
            d = r["dft"][src]
            assert d["available"] and d["entries"] and d["reference"]["kind"].startswith("dft_")
            assert -60 < d["lowest_kJ_per_mol_atom"] < -20
        assert r["dft"]["mp"]["reference"]["kind"] != r["dft"]["oqmd"]["reference"]["kind"]
        assert "literature_experimental" in r["summary"]
        with self.assertRaises(OrchidError):
            self.orchid.get_formation_enthalpy("Al")


class TestGetPhaseDiagram(OrchidTestCase):
    """Tests for get_phase_diagram assessments, phase lookup and DOI resolution."""

    def test_phase_diagram(self):
        """The Al-Ni assessment resolves by name or DOI, reports the stable phase at the query point, and rejects an unknown one."""
        r = self.orchid.get_phase_diagram("AL-NI", assessment="AlNi-05Mie", x=0.5, T=1200)
        a = r["assessments"][0]
        assert a["tdb"] == "AlNi-05Mie.tdb" and a["assessment_doi"] == "10.1016/j.calphad.2005.02.002"
        assert a["phase_at"]["stable_phases"] == ["BCC_A2"]
        assert a["viewer_url"].endswith("#AL-NI/AlNi-05Mie.json")
        assert len(self.orchid.get_phase_diagram("CU-ZR")["assessments"]) >= 2
        assert "regions_summary" not in a
        assert self.orchid.get_phase_diagram("AL-NI", assessment="AlNi-05Mie", fields="full")["assessments"][0]["regions_summary"]
        by_doi = self.orchid.get_phase_diagram("AL-NI", assessment="https://doi.org/10.1016/j.calphad.2005.02.002")
        assert by_doi["assessments"][0]["tdb"] == "AlNi-05Mie.tdb"
        with self.assertRaises(OrchidError):
            self.orchid.get_phase_diagram("AL-NI", assessment="nope")


class TestTdbFiles(OrchidTestCase):
    """Tests for the TDB catalogue and the get_tdb content and licence-tier rules."""

    def test_tdb_catalog_and_licence_tiers(self):
        """Catalogue entries carry licence fields and hashes, and fetched or manual files withhold their content."""
        r = self.orchid.list_tdb_files(["Cu", "Zr"], exact=True)
        assert r["total"] >= 2 and all(f["elements"] == "CU-ZR" for f in r["files"])
        assert all({"name", "tier", "license", "license_status"} <= set(f) for f in r["files"])
        assert (
            "access" not in r["files"][0]
            and "access" in self.orchid.list_tdb_files(["Cu", "Zr"], exact=True, fields="full")["files"][0]
        )
        t = self.orchid.get_tdb("AlNi-05Mie.tdb")
        assert t["content"] and "PHASE" in t["content"].upper()
        assert t["sha256_of_served_bytes"] == t["entry"]["sha256"]
        assert t["sha256_matches_phase_diagram_record"] is True
        allf = self.orchid.list_tdb_files(limit=5000)["files"]
        assert len(allf) == self.orchid.list_tdb_files()["total"]
        for tier in ("fetched", "manual"):
            e = next(f for f in allf if f["tier"] == tier)
            g = self.orchid.get_tdb(e["name"])
            assert g["content"] is None and g["content_withheld"]
            assert g["entry"]["orchid_download_url"] is None


class TestGetPaper(OrchidTestCase):
    """Tests for get_paper on papers with reported values and with experimental context."""

    def test_get_paper(self):
        """A paper resolves by DOI URL with its systems and values, carries quoted context items, and an unknown DOI raises."""
        r = self.orchid.get_paper("https://doi.org/10.1016/j.calphad.2005.02.002")
        assert r["paper"]["doi"] == "10.1016/j.calphad.2005.02.002"
        assert "AL-NI" in r["systems"] and r["n_values_reported"] > 0
        ctx = self.orchid.get_paper("10.1016/j.tca.2010.10.005")
        assert ctx["n_context_items"] > 0
        assert all(i["quote"] and i["page"] for i in ctx["experimental_context"]["items"])
        with self.assertRaises(OrchidError):
            self.orchid.get_paper("10.0000/not-a-doi")


class TestCompareToDft(OrchidTestCase):
    """Tests for compare_to_dft, which sets literature values beside DFT hull data."""

    def test_compare_to_dft(self):
        """Al-Ni compounds carry cited literature values, full mode adds DFT points, and the hull vertices are present."""
        r = self.orchid.compare_to_dft("Al-Ni")
        formulas = {c["formula"] for c in r["compounds"]}
        assert {"Al3Ni", "AlNi", "AlNi3"} <= formulas
        for c in r["compounds"]:
            for v in c["literature"]:
                assert v["citation"] and v["evidence_class"] and v["page"] is not None
                assert v["quote"] is None or len(v["quote"]) <= 240
        full = self.orchid.compare_to_dft("Al-Ni", fields="full")
        assert all("dft_points" in v for c in full["compounds"] for v in c["literature"])
        assert set(r["residual_stats_default_subset"]) <= {"mp", "oqmd"}
        assert r["dft_hull_vertices"]["mp"] and r["dft_hull_vertices"]["oqmd"]


class TestResourcesAndPrompt(OrchidTestCase):
    """Tests for the static resources and the citation prompt served to clients."""

    def test_resources_and_prompt(self):
        """The schema, licence and dataset-card resources carry their key text, and the citation prompt asks for a DOI and no averaging."""
        assert "Record fields" in self.orchid.resource_text("schema")
        assert "CC BY 4.0" in self.orchid.resource_text("licence")
        assert "ORCHID" in self.orchid.resource_text("dataset-card")
        p = cite_orchid_prompt("Al-Ni")
        assert "doi" in p.lower() and "never average" in p.lower()


class TestMeta(OrchidTestCase):
    """Tests for the meta endpoint's notes."""

    def test_meta_mentions_uncertainty_budget(self):
        """The meta notes describe the sigma_effective uncertainty budget."""
        assert any("sigma_effective" in n for n in self.orchid.meta()["notes"])
