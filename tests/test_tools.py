import json

import pytest

from orchid_mcp.chem import parse_formula, system_id
from orchid_mcp.tools import OrchidError, cite_orchid_prompt

PROVENANCE_KEYS = {"paper", "citation", "doi", "page", "quote"}


def _check_value(v):
    assert v["evidence_class"] in {"experimental", "model", "dft", "unspecified"}
    assert "flags" in v and "origin" in v
    assert PROVENANCE_KEYS <= set(v["source"])
    assert v["source"]["citation"]
    assert v["source"]["page"] is not None
    if v["record_kind"] == "running_text":
        assert v["source"]["quote"]
    else:
        assert v["locator"].get("label") is not None  # table locator instead of a sentence quote


def test_chem():
    assert system_id("Ni-Al") == "AL-NI" and system_id(["ti", "ni"]) == "NI-TI" and system_id("CuZr") == "CU-ZR"
    assert parse_formula("Al3Ni") == {"AL": 0.75, "NI": 0.25}
    assert parse_formula("γ′-Ni3Al (L12)") == {"AL": 0.25, "NI": 0.75}
    assert parse_formula("AlNi3 and Al3Ni") is None


def test_search_systems(orchid):
    r = orchid.search_systems(["Al", "Ni"])
    assert r["systems"][0]["system"] == "AL-NI"
    assert r["systems"][0]["calculated_phase_diagrams"] >= 1
    assert all({"AL", "NI"} <= set(s["elements"]) for s in r["systems"])
    exact = orchid.search_systems(["Zr", "Cu"], exact=True)
    assert [s["system"] for s in exact["systems"]] == ["CU-ZR"]
    assert "NI-TI" in [s["system"] for s in orchid.search_systems(text="Ni-Ti")["systems"]]
    assert r["orchid"]["release"] and r["orchid"]["licence"]


def test_get_system_data_al_ni(orchid):
    r = orchid.get_system_data("Ni-Al", limit=500)
    assert r["system"] == "AL-NI" and r["returned"] == r["total_matching"] > 100
    for v in r["values"]:
        _check_value(v)
        assert v["flags"]["extraction_accepted"] is True
    kinds = {v["record_kind"] for v in r["values"]}
    assert {"running_text", "invariant_table"} <= kinds
    exp = orchid.get_system_data("AL-NI", evidence_class="experimental", property_group="formation")
    assert exp["values"] and all(v["evidence_class"] == "experimental" for v in exp["values"])
    assert all(v["property_group"] == "formation" for v in exp["values"])


def test_get_system_data_pagination_and_errors(orchid):
    a = orchid.get_system_data("CU-ZR", limit=10)
    b = orchid.get_system_data("CU-ZR", limit=10, offset=10)
    assert a["truncated"] and {v["id"] for v in a["values"]}.isdisjoint({v["id"] for v in b["values"]})
    with pytest.raises(OrchidError):
        orchid.get_system_data("XX-YY")
    with pytest.raises(OrchidError):
        orchid.get_system_data("AL-NI", evidence_class="guess")


def test_invariants_ni_ti(orchid):
    r = orchid.get_invariant_reactions("Ni-Ti")
    assert r["literature"] and r["calculated"]
    for v in r["literature"]:
        _check_value(v)
        assert v["unit"] == "K"
    calc = r["calculated"][0]
    assert len(calc["tdb_sha256"]) == 64 and calc["assessment_citation"]
    assert calc["evidence_class"] == "model" and calc["invariants"]
    lit_only = orchid.get_invariant_reactions("NI-TI", source="literature")
    assert "calculated" not in lit_only
    with pytest.raises(OrchidError):
        orchid.get_invariant_reactions("NI-TI", source="nope")


def test_invariant_matching_al_ni(orchid):
    r = orchid.get_invariant_reactions("AL-NI", assessment="AlNi-05Mie")
    matches = [m for inv in r["calculated"][0]["invariants"] for m in inv.get("literature_matches", [])]
    assert any(abs(m["dT_K"]) < 1 for m in matches)


def test_formation_enthalpy_al3ni(orchid):
    r = orchid.get_formation_enthalpy("Al3Ni")
    assert r["system"] == "AL-NI" and r["composition"] == {"AL": 0.75, "NI": 0.25}
    assert r["literature"]
    for v in r["literature"]:
        _check_value(v)
        assert v["unit"] == "kJ/mol-atom"
    for src in ("mp", "oqmd"):
        d = r["dft"][src]
        assert d["available"] and d["entries"] and d["reference"]["kind"].startswith("dft_")
        assert -60 < d["lowest_kJ_per_mol_atom"] < -20
    assert r["dft"]["mp"]["reference"]["kind"] != r["dft"]["oqmd"]["reference"]["kind"]
    assert "literature_experimental" in r["summary"]
    with pytest.raises(OrchidError):
        orchid.get_formation_enthalpy("Al")


def test_phase_diagram(orchid):
    r = orchid.get_phase_diagram("AL-NI", assessment="AlNi-05Mie", x=0.5, T=1200)
    a = r["assessments"][0]
    assert a["tdb"] == "AlNi-05Mie.tdb" and a["assessment_doi"] == "10.1016/j.calphad.2005.02.002"
    assert a["phase_at"]["stable_phases"] == ["BCC_A2"]
    assert a["viewer_url"].endswith("#AL-NI/AlNi-05Mie.json")
    assert len(orchid.get_phase_diagram("CU-ZR")["assessments"]) >= 2
    by_doi = orchid.get_phase_diagram("AL-NI", assessment="https://doi.org/10.1016/j.calphad.2005.02.002")
    assert by_doi["assessments"][0]["tdb"] == "AlNi-05Mie.tdb"
    with pytest.raises(OrchidError):
        orchid.get_phase_diagram("AL-NI", assessment="nope")


def test_tdb_catalog_and_licence_tiers(orchid):
    r = orchid.list_tdb_files(["Cu", "Zr"], exact=True)
    assert r["total"] >= 2 and all(f["elements"] == "CU-ZR" for f in r["files"])
    t = orchid.get_tdb("AlNi-05Mie.tdb")
    assert t["content"] and "PHASE" in t["content"].upper()
    assert t["sha256_of_served_bytes"] == t["entry"]["sha256"]
    assert t["sha256_matches_phase_diagram_record"] is True
    allf = orchid.list_tdb_files(limit=5000)["files"]
    for tier in ("fetched", "manual"):
        e = next(f for f in allf if f["tier"] == tier)
        g = orchid.get_tdb(e["name"])
        assert g["content"] is None and g["content_withheld"]
        assert g["entry"]["orchid_download_url"] is None


def test_get_paper(orchid):
    r = orchid.get_paper("https://doi.org/10.1016/j.calphad.2005.02.002")
    assert r["paper"]["doi"] == "10.1016/j.calphad.2005.02.002"
    assert "AL-NI" in r["systems"] and r["n_values_reported"] > 0
    ctx = orchid.get_paper("10.1016/j.tca.2010.10.005")
    assert ctx["n_context_items"] > 0
    assert all(i["quote"] and i["page"] for i in ctx["experimental_context"]["items"])
    with pytest.raises(OrchidError):
        orchid.get_paper("10.0000/not-a-doi")


def test_compare_to_dft(orchid):
    r = orchid.compare_to_dft("Al-Ni")
    formulas = {c["formula"] for c in r["compounds"]}
    assert {"Al3Ni", "AlNi", "AlNi3"} <= formulas
    for c in r["compounds"]:
        for v in c["literature"]:
            assert v["citation"] and v["evidence_class"] and v["page"] is not None
    assert set(r["residual_stats_default_subset"]) <= {"mp", "oqmd"}
    assert r["dft_hull_vertices"]["mp"] and r["dft_hull_vertices"]["oqmd"]


def test_resources_and_prompt(orchid):
    assert "Record fields" in orchid.resource_text("schema")
    assert "CC BY 4.0" in orchid.resource_text("licence")
    assert "ORCHID" in orchid.resource_text("dataset-card")
    p = cite_orchid_prompt("Al-Ni")
    assert "doi" in p.lower() and "never average" in p.lower()


def test_results_are_json(orchid):
    json.dumps(orchid.get_invariant_reactions("CU-ZR"))
