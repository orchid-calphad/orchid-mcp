"""Phase-registry tools over a small fixture (tests/fixtures/phases-data) and, when present, the real data."""

import json
from pathlib import Path

import pytest

from orchid_mcp.datasource import DataSource
from orchid_mcp.tools import Orchid, OrchidError

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "phases-data"


@pytest.fixture(scope="module")
def fx():
    ds = DataSource(FIXTURE)
    return Orchid(ds, ds)


def test_get_phase_by_id_name_and_label(fx):
    r = fx.get_phase("Al-Ni", "phase:al-ni:fcc-l12")
    p = r["phase"]
    assert p["id"] == "phase:al-ni:fcc-l12" and p["system"] == "AL-NI"
    assert p["structure"]["tier"] == "A" and p["structure"]["prototype"] == "AuCu3"
    assert p["models"] and p["models"][0]["phase_statement"].startswith("PHASE L12_FCC")
    assert "tdb_definition" not in json.dumps(p)
    assert len(p["references"]) <= 5 and all("doi" in x for x in p["references"])
    assert p["viewer_url"].endswith("/phases/#phase:al-ni:fcc-l12") and p["data_url"].endswith("phases/systems/AL-NI.json")
    assert p["static_page_url"].endswith("phases/pages/AL-NI.html#phase-al-ni-fcc-l12")
    assert "Hallstedt" in r["how_to_cite_phase_registry"] and r["orchid"]["release"]
    assert fx.get_phase("Ni-Al", "l12_fcc")["phase"]["id"] == "phase:al-ni:fcc-l12"
    assert fx.get_phase("AL-NI", "tdb:AlNi-01Dup-LB.tdb:L12_FCC")["phase"]["id"] == "phase:al-ni:fcc-l12"
    assert fx.get_phase("", "phase:ni-ti:ni3ti-d024")["phase"]["system"] == "NI-TI"
    assert fx.get_phase("Ni-Ti", "NI3TI")["phase"]["id"] == "phase:ni-ti:ni3ti-d024"


def test_get_phase_full_and_errors(fx):
    full = fx.get_phase("Al-Ni", "phase:al-ni:fcc-l12", fields="full")["phase"]
    td = full["models"][0]["tdb_definition"]
    assert td["verbatim"]["phase"]["text"].startswith("PHASE L12_FCC")
    assert td["verbatim"]["constituent"]["text"].startswith("CONST")
    assert td["provenance"]["sha256"] and full["references"][0]["citation"]
    amb = fx.get_phase("Al-Ni", "fcc")
    assert amb.get("ambiguous") or amb["phase"]["id"].startswith("phase:al-ni:fcc")
    with pytest.raises(OrchidError):
        fx.get_phase("Al-Ni", "no-such-phase")
    with pytest.raises(OrchidError):
        fx.get_phase("Al-Ni", "fcc", fields="everything")
    with pytest.raises(OrchidError):
        fx.get_phase("Cu-Zr", "liquid")


def test_search_phases(fx):
    allp = fx.search_phases()
    assert allp["total"] == 5 and not allp["truncated"]
    assert {p["id"] for p in fx.search_phases(["Ti"])["phases"]} == {"phase:ni-ti:ni3ti-d024", "phase:ni-ti:niti-b2"}
    assert [p["id"] for p in fx.search_phases(prototype="L1_2")["phases"]] == ["phase:al-ni:fcc-l12"]
    assert [p["id"] for p in fx.search_phases(prototype="l12")["phases"]] == ["phase:al-ni:fcc-l12"]
    assert fx.search_phases(prototype="cP2")["total"] == 1
    assert {p["id"] for p in fx.search_phases(tier="liquid")["phases"]} == {"phase:al-ni:liquid"}
    assert fx.search_phases(name="ni3ti")["total"] == 1
    page = fx.search_phases(limit=2, offset=2)
    assert page["returned"] == 2 and page["offset"] == 2 and page["truncated"]
    with pytest.raises(OrchidError):
        fx.search_phases(tier="Z")


def test_get_structure_type(fx):
    for q in ("L1_2", "L12", "AuCu3", "cP4", "st:l1-2-aucu3-cp4"):
        r = fx.get_structure_type(q)
        assert r["structure_types"][0]["id"] == "st:l1-2-aucu3-cp4", q
    r = fx.get_structure_type("L12")["structure_types"][0]
    assert r["example_phases"] == ["phase:al-ni:fcc-l12"] and r["registry_phases"] >= 1
    assert fx.get_structure_type("D0_19")["structure_types"][0]["id"] == "st:d0-19-ni3sn-hp8"
    with pytest.raises(OrchidError):
        fx.get_structure_type("zz99")


def test_phase_tools_real_data(orchid):
    if not orchid.data.exists("phases/registry.json"):
        pytest.skip("local data has no phase registry")
    s = orchid.get_phase("Al-Ni", "phase:al-ni:fcc-l12")
    f = orchid.get_phase("Al-Ni", "phase:al-ni:fcc-l12", fields="full")
    assert len(json.dumps(s)) < len(json.dumps(f)) < 200_000
    assert orchid.search_phases(["Al", "Ni"])["total"] >= 10
    assert orchid.get_structure_type("L1_2")["structure_types"][0]["registry_phases"] > 10
