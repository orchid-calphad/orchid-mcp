"""MCP server surface: tool schemas, plus a stdio smoke test that drives a real server subprocess over JSON-RPC."""

import io
import json
import subprocess
import sys
from unittest import TestCase

from conftest import OrchidTestCase, server_env

from orchid_mcp import server, stdio

expected = {
    "search_systems",
    "get_system_data",
    "get_invariant_reactions",
    "get_formation_enthalpy",
    "get_phase_diagram",
    "list_tdb_files",
    "get_tdb",
    "get_paper",
    "compare_to_dft",
    "get_phase",
    "search_phases",
    "get_structure_type",
    "search_papers",
    "get_paper_relations",
    "get_assessment_evidence",
}


def _rpc(proc, mid, method, params=None):
    """Send one JSON-RPC request to a running server and return its parsed reply line.

    Args:
        proc (subprocess.Popen): Server process with text-mode stdin and stdout pipes.
        mid (int): JSON-RPC request id.
        method (str): JSON-RPC method name, such as ``tools/list``.
        params (dict | None): Optional JSON-RPC params; an empty dict is sent when omitted.

    Returns:
        The decoded JSON-RPC response object.
    """
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


class TestToolSchemas(TestCase):
    """Tests for the tool descriptions advertised by the server."""

    def test_tool_schemas(self):
        """The advertised tool set and each tool's required fields, enums and defaults match the contract."""
        tools = {t["name"]: t for t in server.tool_descriptions()}
        assert set(tools) == expected
        assert tools["get_system_data"]["inputSchema"]["required"] == ["system"]
        assert tools["search_systems"]["inputSchema"]["properties"]["elements"]["anyOf"][0]["type"] == "array"
        fields = tools["get_system_data"]["inputSchema"]["properties"]["fields"]
        assert fields["enum"] == ["summary", "full"] and fields["default"] == "summary"
        source = tools["get_invariant_reactions"]["inputSchema"]["properties"]["source"]
        assert source["enum"] == ["literature", "calculated", "both"] and source["default"] == "both"
        assert tools["get_system_data"]["inputSchema"]["properties"]["limit"]["default"] == 50
        assert tools["get_phase"]["inputSchema"]["required"] == ["system", "phase"]
        assert tools["search_phases"]["inputSchema"]["properties"]["limit"]["default"] == 50
        assert tools["search_papers"]["inputSchema"]["required"] == []
        assert tools["get_paper_relations"]["inputSchema"]["properties"]["direction"]["default"] == "both"
        assert tools["get_assessment_evidence"]["inputSchema"]["required"] == ["system"]
        assert tools["get_paper"]["inputSchema"]["properties"]["fields"]["default"] == "summary"


class TestFallbackProtocol(TestCase):
    """The built-in JSON-RPC fallback answers invalid input without dying."""

    def test_non_object_json_gets_invalid_request_and_server_lives(self):
        """A JSON number or array is answered with -32600, and the next valid request still gets a reply."""
        requests = '123\n[1]\n{"jsonrpc": "2.0", "id": 7, "method": "ping"}\n'
        stdin, stdout = io.StringIO(requests), io.StringIO()
        stdio.run_fallback_stdio(stdin, stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert [r["id"] for r in replies] == [None, None, 7]
        assert replies[0]["error"]["code"] == -32600
        assert replies[1]["error"]["code"] == -32600
        assert replies[2]["result"] == {}

    def test_console_streams_are_utf8_with_bare_lf(self):
        """The real process answers in UTF-8 with LF-only lines, whatever the OS console defaults are."""
        request = '{"jsonrpc": "2.0", "id": "éü", "method": "ping"}\n'.encode()
        out = subprocess.run(
            [sys.executable, "-m", "orchid_mcp", "--fallback"], input=request, capture_output=True, env=server_env(), check=True
        ).stdout
        assert b"\r" not in out and out.endswith(b"\n")
        assert json.loads(out.decode("utf-8"))["id"] == "éü"


class TestStdioSmoke(OrchidTestCase):
    """Tests that the stdio server answers the MCP handshake, tools, resources and prompts end to end."""

    def _run_smoke(self, fallback):
        """Start the server in a subprocess and walk it through initialize, tools, resources and prompts.

        Args:
            fallback (bool): If True, start the server with ``--fallback`` (the stdlib server, without the mcp SDK).
        """
        cmd = [sys.executable, "-m", "orchid_mcp"] + (["--fallback"] if fallback else [])
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=server_env(),
        )
        try:
            init = _rpc(
                proc,
                1,
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            )
            assert init["result"]["serverInfo"]["name"] == "orchid"
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            proc.stdin.flush()
            tools = _rpc(proc, 2, "tools/list")["result"]["tools"]
            assert {t["name"] for t in tools} == expected
            call = _rpc(
                proc,
                3,
                "tools/call",
                {"name": "get_phase_diagram", "arguments": {"system": "Al-Ni", "assessment": "AlNi-05Mie", "x": 0.5, "T": 1200}},
            )
            payload = json.loads(call["result"]["content"][0]["text"])
            assert payload["assessments"][0]["phase_at"]["stable_phases"] == ["BCC_A2"]
            bad = _rpc(proc, 4, "tools/call", {"name": "get_paper", "arguments": {"doi": "10.0000/none"}})
            assert bad["result"]["isError"] is True
            res = _rpc(proc, 5, "resources/list")["result"]["resources"]
            assert {r["uri"] for r in res} == {
                "orchid://schema",
                "orchid://dataset-card",
                "orchid://licence",
                "orchid://bibliography-schema",
            }
            schema = _rpc(proc, 6, "resources/read", {"uri": "orchid://schema"})["result"]["contents"][0]["text"]
            assert "Record fields" in schema
            prompts = _rpc(proc, 7, "prompts/list")["result"]["prompts"]
            assert prompts[0]["name"] == "cite_orchid"
            msg = _rpc(proc, 8, "prompts/get", {"name": "cite_orchid", "arguments": {"topic": "Al-Ni"}})
            assert "DOI" in msg["result"]["messages"][0]["content"]["text"]
        finally:
            proc.stdin.close()
            proc.wait(timeout=10)

    def test_stdio_smoke_with_fallback(self):
        """The server in ``--fallback`` mode completes the full handshake and answers every tool, resource and prompt call."""
        self._run_smoke(fallback=True)

    def test_stdio_smoke_without_fallback(self):
        """The server using the mcp SDK completes the same handshake, or is skipped when the SDK is not installed."""
        if not server.sdk_available():
            self.skipTest("mcp SDK not installed")
        self._run_smoke(fallback=False)
