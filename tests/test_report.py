"""report_issue_url: pre-filled "Report an error in a value" issue links (same vectors as orchid-project.org
tools/tests/test_report_links.py); only non-personal fields, never posted."""
from urllib.parse import parse_qs, urlsplit

from orchid_mcp.report import report_issue_url, report_url_for

VECTORS = [
    (("ORC-RZXYDDJGWB", "AG-AL", "10.1016/j.jallcom.2004.04.126", 2, "590.15 K"),
     "https://github.com/orchid-calphad/orchid-data/issues/new?template=report-value-error.yml"
     "&title=%5Bvalue%5D%20ORC-RZXYDDJGWB%20%28AG-AL%29&record_id=ORC-RZXYDDJGWB&system=AG-AL"
     "&source=doi%3A10.1016%2Fj.jallcom.2004.04.126%2C%20p.%202&value_shown=590.15%20K"),
    (("ORC-5D324PIVW6.310d73", "AL-CR", "https://doi.org/10.1007/bf02735058", "5", "1.6386 nm"),
     "https://github.com/orchid-calphad/orchid-data/issues/new?template=report-value-error.yml"
     "&title=%5Bvalue%5D%20ORC-5D324PIVW6.310d73%20%28AL-CR%29&record_id=ORC-5D324PIVW6.310d73&system=AL-CR"
     "&source=doi%3A10.1007%2Fbf02735058%2C%20p.%205&value_shown=1.6386%20nm"),
    (("ORC-ABCDEFGHIJ", None, None, None, None),
     "https://github.com/orchid-calphad/orchid-data/issues/new?template=report-value-error.yml"
     "&title=%5Bvalue%5D%20ORC-ABCDEFGHIJ&record_id=ORC-ABCDEFGHIJ"),
]


def test_vectors():
    for args, url in VECTORS:
        assert report_issue_url(*args) == url


def test_validation_and_whitelist():
    u = report_issue_url("ORC-RZXYDDJGWB", "ag-al; drop", "not a doi", "p2<script>", "x" * 500)
    q = parse_qs(urlsplit(u).query)
    assert set(q) == {"template", "title", "record_id", "value_shown"}
    assert len(q["value_shown"][0]) == 120
    assert report_issue_url("not-an-id") is None and report_issue_url(None) is None


def test_report_url_for_expanded_record():
    r = {"id": "ORC-RZXYDDJGWB", "system": "AG-AL", "value": None, "temperature_K": 590.15,
         "source": {"doi": "10.1016/j.jallcom.2004.04.126", "page": 2, "citation": "x"},
         "contributors": [{"name": "Someone", "orcid": "0000-0002-1825-0097"}]}
    assert report_url_for(r) == VECTORS[0][1]
    assert "Someone" not in report_url_for(r) and "orcid" not in report_url_for(r)


def test_tool_outputs_carry_the_link(orchid):
    d = orchid.get_system_data("Ag-Al", limit=5)
    vals = d.get("values") or d.get("records") or []
    assert vals
    for v in vals:
        u = v["report_issue_url"]
        assert u.startswith("https://github.com/orchid-calphad/orchid-data/issues/new?template=report-value-error.yml")
        assert parse_qs(urlsplit(u).query)["record_id"] == [v["id"]]
    assert any("report_issue_url" in n for n in d["orchid"]["notes"])
