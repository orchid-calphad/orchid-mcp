"""The README tool table names every tool and parameter that the server advertises, and no others."""

import re
from pathlib import Path
from unittest import TestCase

from orchid_mcp import server

readme = Path(__file__).resolve().parents[1] / "README.md"
call_pattern = re.compile(r"`(\w+)\(([^)`]*)\)`")
cell_split = re.compile(r"(?<!\\)\|")  # a table cell ends at a pipe that is not escaped as \|


def _tool_table_calls():
    """Read every ``name(params)`` written in the first column of the README tool table.

    Returns:
        A dict from tool name to the set of parameter names listed for it.
    """
    text = readme.read_text(encoding="utf-8")
    section = text.split("\n## Tools\n", 1)[1].split("\n### ", 1)[0]
    calls: dict[str, set[str]] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        first_cell = cell_split.split(line)[1]
        for name, arguments in call_pattern.findall(first_cell):
            params = {part.split("=", 1)[0].strip() for part in arguments.split(",") if part.strip()}
            calls[name] = params
    return calls


class TestReadmeToolTable(TestCase):
    """Tests that the README tool table cannot drift from the tool signatures."""

    def test_table_lists_every_tool_and_no_other(self):
        """Each advertised tool appears once in the README table, and the table names no unknown tool."""
        documented = _tool_table_calls()
        advertised = {t["name"] for t in server.tool_descriptions()}
        assert set(documented) == advertised, (sorted(advertised - set(documented)), sorted(set(documented) - advertised))

    def test_table_parameters_match_schemas(self):
        """The parameters written for each tool in the README are exactly the parameters of its input schema."""
        documented = _tool_table_calls()
        for tool in server.tool_descriptions():
            schema_params = set(tool["inputSchema"]["properties"])
            assert documented[tool["name"]] == schema_params, tool["name"]
