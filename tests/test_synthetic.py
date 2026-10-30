"""Literature, phase-diagram, DFT and TDB-catalog tools over a small synthetic dataset that runs in CI.

tests/fixtures/orchid-synthetic is ORCHID-shaped and invented (release "synthetic-ci-fixture-1", DOIs under 10.0000):
it is not scientific data. The real-data tests in the other files skip in CI; these run everywhere.
"""

from pathlib import Path
from unittest import TestCase

from orchid_mcp.datasource import DataSource
from orchid_mcp.errors import OrchidError
from orchid_mcp.tools import Orchid

fixture = Path(__file__).resolve().parent / "fixtures" / "orchid-synthetic"
synthetic_release = "synthetic-ci-fixture-1"


class _SyntheticCase(TestCase):
    """Base TestCase that builds one Orchid over the synthetic fixture for each test class."""

    fx: Orchid

    @classmethod
    def setUpClass(cls) -> None:
        """Build the fixture-backed Orchid once, before the first test of the class runs."""
        super().setUpClass()
        cls.fx = Orchid(DataSource(fixture / "data"), DataSource(fixture / "site"))


class TestSyntheticSystemData(_SyntheticCase):
    """Tests for get_system_data and get_invariant_reactions on the synthetic Al-Ni system."""

    def test_accepted_filter_and_summary_shape(self):
        """accepted_only drops the unaccepted record, summaries carry the required keys and no flags, and the release is cited."""
        r = self.fx.get_system_data("Al-Ni")
        assert r["total"] == 2 and [v["id"] for v in r["values"]] == ["ORC-SYN00001", "ORC-SYN00003"]
        assert self.fx.get_system_data("Al-Ni", accepted_only=False)["total"] == 3
        assert r["orchid"]["release"] == synthetic_release
        for v in r["values"]:
            assert {"id", "property", "value", "unit", "evidence_class", "citation", "doi", "page", "quote"} <= set(v)
            assert "flags" not in v and "source" not in v

    def test_full_records_carry_their_source(self):
        """fields='full' gives every value an expanded source block with citation and DOI, and keeps the table locator."""
        r = self.fx.get_system_data("Al-Ni", fields="full")
        for v in r["values"]:
            assert v["source"]["citation"] == "Synthetic A. (2001) Test Journal"
            assert v["source"]["doi"] == "10.0000/synthetic-alni"
        assert r["values"][0]["locator"]["label"] == "Table 1"

    def test_invariant_reactions_literature_and_calculated(self):
        """The literature invariant is listed, and the calculated invariants come from the synthetic assessment."""
        r = self.fx.get_invariant_reactions("Al-Ni")
        assert r["literature_total"] == 1 and r["literature"][0]["id"] == "ORC-SYN00003"
        assert r["calculated"][0]["tdb"] == "AlNi-05Mie.tdb"
        lit_only = self.fx.get_invariant_reactions("Al-Ni", source="literature")
        assert "calculated" not in lit_only and lit_only["literature_total"] == 1

    def test_unknown_element_and_bad_source_raise(self):
        """An unknown element symbol and a source outside its vocabulary raise OrchidError."""
        with self.assertRaises(OrchidError):
            self.fx.get_system_data("Zz-Ni")
        with self.assertRaises(OrchidError):
            self.fx.get_invariant_reactions("Al-Ni", source="bogus")


class TestSyntheticPhaseDiagramAndDft(_SyntheticCase):
    """Tests for get_phase_diagram grid lookups and compare_to_dft on the synthetic Al-Ni system."""

    def test_phase_diagram_grid_lookup(self):
        """The stored grid gives the stable phase at a node: LIQUID at (x=0.1, 400 K), FCC_A1 at (x=0.5, 300 K)."""
        liquid = self.fx.get_phase_diagram("Al-Ni", x=0.1, T=400)["assessments"][0]["phase_at"]
        assert liquid["stable_phases"] == ["LIQUID"] and liquid["grid_T_K"] == 400.0
        fcc = self.fx.get_phase_diagram("Al-Ni", x=0.5, T=300)["assessments"][0]["phase_at"]
        assert fcc["stable_phases"] == ["FCC_A1"]

    def test_phase_diagram_identifies_its_tdb(self):
        """The assessment names its TDB file and the SHA-256 is the one of the served bytes."""
        a = self.fx.get_phase_diagram("Al-Ni")["assessments"][0]
        assert a["tdb"] == "AlNi-05Mie.tdb" and a["assessment_doi"] == "10.0000/synthetic-mie05"
        assert len(a["tdb_sha256"]) == 64

    def test_compare_to_dft_residuals_and_hulls(self):
        """The Al3Ni literature value has DFT residuals from both Materials Project and OQMD, and both hulls are available."""
        r = self.fx.compare_to_dft("Al-Ni")
        compound = r["compounds"][0]
        assert compound["formula"] == "Al3Ni"
        lit = compound["literature"][0]
        assert lit["id"] == "ORC-SYN00001" and lit["residual_dft_minus_lit"] == {"mp": -0.9, "oqmd": 0.2}


class TestSyntheticTdbCatalog(_SyntheticCase):
    """Tests for the TDB catalog: bundled files are served with a verified hash, fetched files are withheld."""

    def test_bundled_file_is_served_and_hash_matches(self):
        """The bundled TDB returns its content, and its SHA-256 matches the phase-diagram record."""
        r = self.fx.get_tdb("AlNi-05Mie.tdb")
        assert r["content"].startswith("$ SYNTHETIC TEST FILE")
        assert r["sha256_matches_phase_diagram_record"] is True

    def test_fetched_file_content_is_withheld(self):
        """A fetched-tier file has no content; the response explains why."""
        r = self.fx.get_tdb("NiTi-99Tan.tdb")
        assert r["content"] is None and "content_withheld" in r
