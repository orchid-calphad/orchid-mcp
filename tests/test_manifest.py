"""The committed tool manifest (openapi/tools_manifest.json) matches the tools in the code, and every tool has a verification level."""

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase

from orchid_mcp import server

manifest_path = Path(__file__).resolve().parents[1] / "openapi" / "tools_manifest.json"


class TestToolManifest(TestCase):
    """Tests that the tool manifest is generated from the code and cannot drift from it."""

    def test_committed_manifest_matches_code(self):
        """The committed manifest is byte-for-byte what the code generates; regenerate with orchid-mcp --list-tools."""
        assert manifest_path.read_text(encoding="utf-8") == server.render_tools_manifest(), (
            "openapi/tools_manifest.json is stale: run orchid-mcp --list-tools > openapi/tools_manifest.json"
        )

    def test_list_tools_flag_prints_the_manifest(self):
        """The --list-tools flag prints exactly the rendered manifest and exits with status 0."""
        out = io.StringIO()
        with redirect_stdout(out):
            status = server.main(["--list-tools"])
        assert status == 0 and out.getvalue() == server.render_tools_manifest()

    def test_every_tool_has_a_verification_level(self):
        """Each advertised tool has a verification level, and only 'smoke' or 'golden' is used."""
        names = {t["name"] for t in server.tool_descriptions()}
        assert set(server.verification_levels) == names
        assert set(server.verification_levels.values()) <= {"smoke", "golden"}
