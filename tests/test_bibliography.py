"""Bibliography tools over a small fixture (tests/fixtures/bibliography-data: the NI-TI papers) and the real data."""

import json
from pathlib import Path

import pytest

from orchid_mcp.datasource import DataSource
from orchid_mcp.tools import Orchid, OrchidError

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bibliography-data"
POV = "10.1016/j.calphad.2013.02.004"  # Povoden-Karadeniz et al. 2013, Ni-Ti (NiTi-13Pov.tdb)
TAN = "10.1016/s1359-6454(99)00193-7"  # Tang et al. 1999, Ni-Ti (NiTi-99Tan.tdb)


@pytest.fixture(scope="module")
def fx():
    ds = DataSource(FIXTURE)
    return Orchid(ds, ds)


def test_get_paper_bibliography_only(fx):
    r = fx.get_paper(f"https://doi.org/{POV.upper()}")
    b = r["bibliography"]
    assert r["paper"] is None and b["doi"] == POV and "tdb_source_assessment" in b["roles"]
    assert "NI-TI" in b["systems"] and b["role_details"]["tdb_source_assessment"]["tdb_files"][0]["file"] == "NiTi-13Pov.tdb"
    assert b["evidence_summary"]["reported"]["n_values"] > 0 and "relations" not in b
    assert b["urls"]["json"].endswith(f"bibliography/papers/{b['key']}.json")
    full = fx.get_paper(POV, fields="full")["bibliography"]
    assert full["record"]["id"] == f"doi:{POV}" and full["relations"]
    assert all({"type", "direction", "other", "provenance"} <= set(x) for x in full["relations"])
    assert fx.get_paper(b["key"])["bibliography"]["id"] == f"doi:{POV}"
    assert fx.get_paper(f"doi:{POV}")["bibliography"]["key"] == b["key"]
    with pytest.raises(OrchidError):
        fx.get_paper("10.0000/none")
    with pytest.raises(OrchidError):
        fx.get_paper(POV, fields="everything")


def test_search_papers(fx):
    allp = fx.search_papers()
    assert allp["total"] == len(json.loads((FIXTURE / "bibliography/index.json").read_text())["rows"])
    tdb = fx.search_papers(system="Ti-Ni", role="tdb_source_assessment")
    assert tdb["total"] >= 10 and all("tdb_source_assessment" in p["roles"] for p in tdb["papers"])
    years = [p["year"] or 0 for p in tdb["papers"]]
    assert years == sorted(years)
    assert fx.search_papers(elements=["Ni"])["total"] == allp["total"]
    assert fx.search_papers(elements=["Cu"])["total"] == 0
    y = fx.search_papers(year_from=2010, year_to=2015)
    assert y["total"] > 0 and all(2010 <= p["year"] <= 2015 for p in y["papers"])
    assert any(p["doi"] == POV for p in fx.search_papers(text="ni-ti")["papers"]) or fx.search_papers(text="ni")["total"]
    assert fx.search_papers(evidence_class="model", role="tdb_source_assessment")["total"] >= 1
    page = fx.search_papers(limit=2, offset=2)
    assert page["returned"] == 2 and page["offset"] == 2 and page["truncated"]
    with pytest.raises(OrchidError):
        fx.search_papers(role="author")
    with pytest.raises(OrchidError):
        fx.search_papers(evidence_class="assessed")


def test_get_paper_relations(fx):
    r = fx.get_paper_relations(POV, relation="cites", direction="out")
    assert r["total"] >= 1 and all(x["type"] == "cites" and x["direction"] == "out" for x in r["relations"])
    assert all(x["provenance"]["source"] == "orchid_extraction" and x["count"] >= 1 for x in r["relations"])
    re_ = fx.get_paper_relations(POV, relation="reassesses")
    assert any(x["other"]["id"] == f"doi:{TAN}" and x["provenance"]["method"] == "citation+same-system"
               for x in re_["relations"])
    inv = fx.get_paper_relations(TAN, relation="reassesses", direction="in")
    assert any(x["other"]["id"] == f"doi:{POV}" for x in inv["relations"])
    tdb = fx.get_paper_relations(POV, relation="models_system_in_tdb")
    assert tdb["relations"][0]["other"]["id"] == "tdb:NiTi-13Pov.tdb"
    with pytest.raises(OrchidError):
        fx.get_paper_relations(POV, relation="likes")
    with pytest.raises(OrchidError):
        fx.get_paper_relations(POV, direction="sideways")


def test_get_assessment_evidence(fx):
    r = fx.get_assessment_evidence("Ti-Ni", max_data_papers=3)
    assert r["system"] == "NI-TI" and r["n_assessments"] >= 10
    pov = next(a for a in r["assessments"] if a["doi"] == POV)
    assert pov["tdb_files"] == ["NiTi-13Pov.tdb"] and pov["n_data_papers_used"] >= 1
    assert len(pov["data_papers_used"]) <= 3 and pov["data_papers_truncated"] == (pov["n_data_papers_used"] > 3)
    assert {"later": f"doi:{POV}", "earlier": f"doi:{TAN}", "method": "citation+same-system"} in r["reassessment_links"]
    with pytest.raises(OrchidError):
        fx.get_assessment_evidence("Cu-Zr")


def test_bibliography_real_data(orchid):
    if not orchid.data.exists("bibliography/index.json"):
        pytest.skip("local data has no bibliography")
    r = orchid.get_paper("10.1016/j.calphad.2005.02.002")
    assert r["paper"]["doi"] == "10.1016/j.calphad.2005.02.002" and "bibliography" in r
    assert "corpus_assessment_read" in r["bibliography"]["roles"]
    assert orchid.search_papers(system="Al-Ni", role="tdb_source_assessment")["total"] >= 3
    ev = orchid.get_assessment_evidence("Al-Ni")
    assert ev["n_assessments"] >= 3 and len(json.dumps(ev)) < 400_000
    rel = orchid.get_paper_relations("10.1016/j.calphad.2005.02.002", direction="out", limit=5)
    assert rel["returned"] <= 5
