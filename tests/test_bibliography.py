"""Bibliography tools over a small fixture (tests/fixtures/bibliography-data: the NI-TI papers) and the real data."""

import json
from pathlib import Path
from unittest import TestCase

from conftest import OrchidTestCase

from orchid_mcp.datasource import DataSource
from orchid_mcp.tools import Orchid, OrchidError

fixture = Path(__file__).resolve().parent / "fixtures" / "bibliography-data"
pov = "10.1016/j.calphad.2013.02.004"  # Povoden-Karadeniz et al. 2013, Ni-Ti (NiTi-13Pov.tdb)
tan = "10.1016/s1359-6454(99)00193-7"  # Tang et al. 1999, Ni-Ti (NiTi-99Tan.tdb)


class _FixtureCase(TestCase):
    """Base TestCase that builds one Orchid over the bibliography fixture for each test class."""

    fx: Orchid

    @classmethod
    def setUpClass(cls) -> None:
        """Build the fixture-backed Orchid once, before the first test of the class runs."""
        super().setUpClass()
        ds = DataSource(fixture)
        cls.fx = Orchid(ds, ds)


class TestGetPaper(_FixtureCase):
    """Tests for get_paper on a bibliography-only record in the fixture."""

    def test_get_paper_bibliography_only(self):
        """A DOI with no paper record still returns its bibliography block, full mode and key or DOI lookups agree."""
        r = self.fx.get_paper(f"https://doi.org/{pov.upper()}")
        b = r["bibliography"]
        assert r["paper"] is None and b["doi"] == pov and "tdb_source_assessment" in b["roles"]
        assert "NI-TI" in b["systems"] and b["role_details"]["tdb_source_assessment"]["tdb_files"][0]["file"] == "NiTi-13Pov.tdb"
        assert b["evidence_summary"]["reported"]["n_values"] > 0 and "relations" not in b
        assert b["urls"]["json"].endswith(f"bibliography/papers/{b['key']}.json")
        full = self.fx.get_paper(pov, fields="full")["bibliography"]
        assert full["record"]["id"] == f"doi:{pov}" and full["relations"]
        assert all({"type", "direction", "other", "provenance"} <= set(x) for x in full["relations"])
        assert self.fx.get_paper(b["key"])["bibliography"]["id"] == f"doi:{pov}"
        assert self.fx.get_paper(f"doi:{pov}")["bibliography"]["key"] == b["key"]
        with self.assertRaises(OrchidError):
            self.fx.get_paper("10.0000/none")
        with self.assertRaises(OrchidError):
            self.fx.get_paper(pov, fields="everything")


class TestSearchPapers(_FixtureCase):
    """Tests for search_papers filtering, ordering and paging over the fixture."""

    def test_search_papers(self):
        """Filters by system, role, element, year and evidence class return the expected papers, with paging and bad-input errors."""
        allp = self.fx.search_papers()
        assert allp["total"] == len(json.loads((fixture / "bibliography/index.json").read_text(encoding="utf-8"))["rows"])
        tdb = self.fx.search_papers(system="Ti-Ni", role="tdb_source_assessment")
        assert tdb["total"] >= 10 and all("tdb_source_assessment" in p["roles"] for p in tdb["papers"])
        years = [p["year"] or 0 for p in tdb["papers"]]
        assert years == sorted(years)
        assert self.fx.search_papers(elements=["Ni"])["total"] == allp["total"]
        assert self.fx.search_papers(elements=["Cu"])["total"] == 0
        y = self.fx.search_papers(year_from=2010, year_to=2015)
        assert y["total"] > 0 and all(2010 <= p["year"] <= 2015 for p in y["papers"])
        assert (
            any(p["doi"] == pov for p in self.fx.search_papers(text="ni-ti")["papers"])
            or self.fx.search_papers(text="ni")["total"]
        )
        assert self.fx.search_papers(evidence_class="model", role="tdb_source_assessment")["total"] >= 1
        page = self.fx.search_papers(limit=2, offset=2)
        assert page["returned"] == 2 and page["offset"] == 2 and page["truncated"]
        with self.assertRaises(OrchidError):
            self.fx.search_papers(role="author")
        with self.assertRaises(OrchidError):
            self.fx.search_papers(evidence_class="assessed")


class TestGetPaperRelations(_FixtureCase):
    """Tests for get_paper_relations over the fixture citation and reassessment graph."""

    def test_get_paper_relations(self):
        """Relations are filtered by type and direction, carry provenance, and reject unknown relation or direction values."""
        r = self.fx.get_paper_relations(pov, relation="cites", direction="out")
        assert r["total"] >= 1 and all(x["type"] == "cites" and x["direction"] == "out" for x in r["relations"])
        assert all(x["provenance"]["source"] == "orchid_extraction" and x["count"] >= 1 for x in r["relations"])
        re_ = self.fx.get_paper_relations(pov, relation="reassesses")
        assert any(
            x["other"]["id"] == f"doi:{tan}" and x["provenance"]["method"] == "citation+same-system" for x in re_["relations"]
        )
        inv = self.fx.get_paper_relations(tan, relation="reassesses", direction="in")
        assert any(x["other"]["id"] == f"doi:{pov}" for x in inv["relations"])
        tdb = self.fx.get_paper_relations(pov, relation="models_system_in_tdb")
        assert tdb["relations"][0]["other"]["id"] == "tdb:NiTi-13Pov.tdb"
        with self.assertRaises(OrchidError):
            self.fx.get_paper_relations(pov, relation="likes")
        with self.assertRaises(OrchidError):
            self.fx.get_paper_relations(pov, direction="sideways")


class TestGetAssessmentEvidence(_FixtureCase):
    """Tests for get_assessment_evidence, which lists assessments and their reassessment links for a system."""

    def test_get_assessment_evidence(self):
        """The Ni-Ti evidence lists its assessments, caps data papers per assessment, and rejects a system with none."""
        r = self.fx.get_assessment_evidence("Ti-Ni", max_data_papers=3)
        assert r["system"] == "NI-TI" and r["n_assessments"] >= 10
        pov_a = next(a for a in r["assessments"] if a["doi"] == pov)
        assert pov_a["tdb_files"] == ["NiTi-13Pov.tdb"] and pov_a["n_data_papers_used"] >= 1
        assert len(pov_a["data_papers_used"]) <= 3 and pov_a["data_papers_truncated"] == (pov_a["n_data_papers_used"] > 3)
        assert {"later": f"doi:{pov}", "earlier": f"doi:{tan}", "method": "citation+same-system"} in r["reassessment_links"]
        with self.assertRaises(OrchidError):
            self.fx.get_assessment_evidence("Cu-Zr")


class TestBibliographyRealData(OrchidTestCase):
    """Tests for the bibliography tools against the real local ORCHID data."""

    def test_bibliography_real_data(self):
        """The real data resolves a known paper's bibliography, its roles, search, evidence and relations within size limits."""
        if not self.orchid.data.exists("bibliography/index.json"):
            self.skipTest("local data has no bibliography")
        r = self.orchid.get_paper("10.1016/j.calphad.2005.02.002")
        assert r["paper"]["doi"] == "10.1016/j.calphad.2005.02.002" and "bibliography" in r
        assert "corpus_assessment_read" in r["bibliography"]["roles"]
        assert self.orchid.search_papers(system="Al-Ni", role="tdb_source_assessment")["total"] >= 3
        ev = self.orchid.get_assessment_evidence("Al-Ni")
        assert ev["n_assessments"] >= 3 and len(json.dumps(ev)) < 400_000
        rel = self.orchid.get_paper_relations("10.1016/j.calphad.2005.02.002", direction="out", limit=5)
        assert rel["returned"] <= 5
