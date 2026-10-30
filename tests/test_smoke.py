"""Every MCP tool, called through the built-in dispatch on committed fixtures: the response and error shapes."""

import json
from pathlib import Path
from unittest import TestCase, mock

from orchid_mcp import server, stdio
from orchid_mcp.datasource import DataSource
from orchid_mcp.tools import Orchid

fixtures = Path(__file__).resolve().parent / "fixtures"
bib_dir = fixtures / "bibliography-data"
phase_dir = fixtures / "phases-data"
synthetic_dir = fixtures / "orchid-synthetic"
pov = "10.1016/j.calphad.2013.02.004"

# (fixture, tool, arguments, expected outcome): "ok" returns a payload, "error" returns {"error": ...}
cases = [
    ("synthetic", "search_systems", {"elements": ["Al", "Ni"]}, "ok"),
    ("synthetic", "get_system_data", {"system": "Al-Ni"}, "ok"),
    ("synthetic", "get_system_data", {"system": "Zz-Ni"}, "error"),
    ("synthetic", "get_invariant_reactions", {"system": "Al-Ni"}, "ok"),
    ("synthetic", "get_formation_enthalpy", {"formula": "Al3Ni"}, "ok"),
    ("synthetic", "get_phase_diagram", {"system": "Al-Ni", "x": 0.5, "T": 300}, "ok"),
    ("synthetic", "list_tdb_files", {"elements": ["Al", "Ni"]}, "ok"),
    ("synthetic", "get_tdb", {"file": "AlNi-05Mie.tdb"}, "ok"),
    ("synthetic", "compare_to_dft", {"system": "Al-Ni"}, "ok"),
    ("bibliography", "get_paper", {"doi": pov}, "ok"),
    ("bibliography", "get_paper", {"doi": "10.0000/none"}, "error"),
    ("bibliography", "search_papers", {"system": "NI-TI"}, "ok"),
    ("bibliography", "get_paper_relations", {"doi": pov, "relation": "cites", "direction": "out"}, "ok"),
    ("bibliography", "get_assessment_evidence", {"system": "NI-TI"}, "ok"),
    ("phases", "get_phase", {"system": "Al-Ni", "phase": "phase:al-ni:fcc-l12"}, "ok"),
    ("phases", "get_phase", {"system": "Al-Ni", "phase": "no-such-phase"}, "error"),
    ("phases", "search_phases", {"elements": ["Al", "Ni"]}, "ok"),
    ("phases", "get_structure_type", {"query": "L1_2"}, "ok"),
]
fixture_dirs = {"synthetic": synthetic_dir, "bibliography": bib_dir, "phases": phase_dir}


def _orchid(name: str) -> Orchid:
    """Build an Orchid over one committed fixture (the synthetic one has separate data and site folders).

    Args:
        name (str): Key of ``fixture_dirs``.

    Returns:
        An Orchid reading only that fixture.
    """
    if name == "synthetic":
        return Orchid(DataSource(synthetic_dir / "data"), DataSource(synthetic_dir / "site"))
    ds = DataSource(fixture_dirs[name])
    return Orchid(ds, ds)


class TestToolDispatch(TestCase):
    """Tests that every tool answers through the built-in dispatch with a well-formed result or error."""

    def test_every_tool_has_a_smoke_case(self):
        """The smoke cases name every advertised tool, so no tool can be added without a dispatch check."""
        assert {case[1] for case in cases} == {t["name"] for t in server.tool_descriptions()}

    def test_every_tool_answers_with_its_response_shape(self):
        """Each tool returns a payload or a tool error of the documented shape, with the compact JSON text matching it."""
        for path in fixture_dirs.values():
            if not path.is_dir():
                self.skipTest(f"fixture missing: {path}")
        for fixture, tool, arguments, expected in cases:
            with self.subTest(tool=tool, arguments=arguments, expected=expected):
                with mock.patch.object(server, "_ORCHID", _orchid(fixture)):
                    result = stdio._rpc_tools_call(1, {"name": tool, "arguments": arguments})["result"]
                payload = result["structuredContent"]
                assert json.loads(result["content"][0]["text"]) == payload
                if expected == "ok":
                    assert result["isError"] is False and isinstance(payload, dict) and "error" not in payload
                    assert "orchid" in payload
                else:
                    assert result["isError"] is True and set(payload) == {"error"} and payload["error"]

    def test_unknown_tool_is_a_protocol_error(self):
        """A tool name that is not advertised gets a JSON-RPC invalid-params error, not a tool result."""
        reply = stdio._rpc_tools_call(2, {"name": "no_such_tool", "arguments": {}})
        assert reply["error"]["code"] == -32602 and "result" not in reply
