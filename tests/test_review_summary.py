"""Check agent-visible safeguards against the corrected release, not synthetic records."""

import gzip
import json
from unittest import TestCase

from conftest import OrchidTestCase, data_dir

from orchid_mcp.tools import summarize_record


class TestReleaseReview(TestCase):
    """Tests that reviewed, duplicate and identity-bearing records keep their safeguards through summarize_record."""

    def test_release_review_and_identity_survive_summary(self):
        """Every human-reviewed correction, evidence identity and duplicate in the release survives the summary."""
        reviewed = identities = duplicates = 0
        with gzip.open(data_dir / "literature/orchid-literature.jsonl.gz", "rt", encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                corrections = [c for c in record.get("corrections", []) if c.get("rule") == "source_adjudicated_human_review"]
                if not (corrections or record.get("evidence_identity") or record.get("duplicate_of")):
                    continue
                summary = summarize_record(record)
                if corrections:
                    reviewed += 1
                    assert summary["human_review_corrections"] == corrections
                    assert not summary["espei_ready"]
                    assert not summary["espei_certified"]
                    assert "human_review_correction_requires_downstream_revalidation" in summary["specification_gaps"]
                if record.get("evidence_identity"):
                    identities += 1
                    assert summary["evidence_identity"] == record["evidence_identity"]
                if record.get("duplicate_of"):
                    duplicates += 1
                    assert summary["duplicate_of"] == record["duplicate_of"]
        assert reviewed == 19
        assert identities > 0 and duplicates > 0


class TestDefaultToolResponse(OrchidTestCase):
    """Tests that the default get_system_data response shows the human-review state of a record."""

    def test_review_visible_in_default_tool_response(self):
        """A reviewed Al-Fe record is accepted but not ESPEI-ready, and its correction cites the DOI and the review caveat."""
        response = self.orchid.get_system_data("AL-FE", limit=1000)
        record = next(r for r in response["values"] if r["id"] == "ORC-QUJ7N2JEIY")
        assert record["accepted"]
        assert not record["espei_ready"]
        correction = record["human_review_corrections"][0]
        assert correction["evidence"]["doi"] == "10.1007/s11669-024-01124-9"
        assert "not independent human re-review" in correction["visual_check"]["checked"]
