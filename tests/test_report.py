"""report_issue_url: pre-filled "Report an error in a value" issue links.

The vectors match orchid-project.org tools/tests/test_report_links.py. Only non-personal fields are included, and nothing is posted.
"""

from unittest import TestCase
from urllib.parse import parse_qs, urlsplit

from conftest import OrchidTestCase

from orchid_mcp.report import report_issue_url, report_url_for

vectors = [
    (
        ("ORC-RZXYDDJGWB", "AG-AL", "10.1016/j.jallcom.2004.04.126", 2, "590.15 K"),
        "https://github.com/orchid-calphad/orchid-data/issues/new?template=report-value-error.yml"
        "&title=%5Bvalue%5D%20ORC-RZXYDDJGWB%20%28AG-AL%29&record_id=ORC-RZXYDDJGWB&system=AG-AL"
        "&source=doi%3A10.1016%2Fj.jallcom.2004.04.126%2C%20p.%202&value_shown=590.15%20K",
    ),
    (
        ("ORC-5D324PIVW6.310d73", "AL-CR", "https://doi.org/10.1007/bf02735058", "5", "1.6386 nm"),
        "https://github.com/orchid-calphad/orchid-data/issues/new?template=report-value-error.yml"
        "&title=%5Bvalue%5D%20ORC-5D324PIVW6.310d73%20%28AL-CR%29&record_id=ORC-5D324PIVW6.310d73&system=AL-CR"
        "&source=doi%3A10.1007%2Fbf02735058%2C%20p.%205&value_shown=1.6386%20nm",
    ),
    (
        ("ORC-ABCDEFGHIJ", None, None, None, None),
        "https://github.com/orchid-calphad/orchid-data/issues/new?template=report-value-error.yml"
        "&title=%5Bvalue%5D%20ORC-ABCDEFGHIJ&record_id=ORC-ABCDEFGHIJ",
    ),
]


class TestReportIssueUrl(TestCase):
    """Tests for building pre-filled issue URLs from raw value fields."""

    def test_vectors(self):
        """Each known input tuple produces exactly its documented issue URL."""
        for args, url in vectors:
            with self.subTest(args=args):
                assert report_issue_url(*args) == url

    def test_validation_and_whitelist(self):
        """Only whitelisted query fields survive, the value is truncated, and malformed ids are rejected."""
        u = report_issue_url("ORC-RZXYDDJGWB", "ag-al; drop", "not a doi", "p2<script>", "x" * 500)
        q = parse_qs(urlsplit(u).query)
        assert set(q) == {"template", "title", "record_id", "value_shown"}
        assert len(q["value_shown"][0]) == 120
        assert report_issue_url("not-an-id") is None and report_issue_url(None) is None


class TestReportUrlFor(TestCase):
    """Tests for building the issue URL from an expanded record dict."""

    def test_report_url_for_expanded_record(self):
        """An expanded record yields the matching vector URL and leaks no contributor name or ORCID."""
        r = {
            "id": "ORC-RZXYDDJGWB",
            "system": "AG-AL",
            "value": None,
            "temperature_K": 590.15,
            "source": {"doi": "10.1016/j.jallcom.2004.04.126", "page": 2, "citation": "x"},
            "contributors": [{"name": "Someone", "orcid": "0000-0002-1825-0097"}],
        }
        assert report_url_for(r) == vectors[0][1]
        assert "Someone" not in report_url_for(r) and "orcid" not in report_url_for(r)


class TestToolOutputsCarryLink(OrchidTestCase):
    """Tests that the data tools attach a report link to every value they return."""

    def test_tool_outputs_carry_the_link(self):
        """Every value from a system query carries a report link keyed to its own record id."""
        d = self.orchid.get_system_data("Ag-Al", limit=5)
        vals = d.get("values") or d.get("records") or []
        assert vals
        for v in vals:
            u = v["report_issue_url"]
            assert u.startswith("https://github.com/orchid-calphad/orchid-data/issues/new?template=report-value-error.yml")
            assert parse_qs(urlsplit(u).query)["record_id"] == [v["id"]]
        assert any("report_issue_url" in n for n in d["orchid"]["notes"])
