import json
import subprocess
import sys

import pytest

from orchid_mcp import server

EXPECTED = {
    "search_systems", "get_system_data", "get_invariant_reactions", "get_formation_enthalpy",
    "get_phase_diagram", "list_tdb_files", "get_tdb", "get_paper", "compare_to_dft",
}


def test_tool_schemas():
    tools = {t["name"]: t for t in server.tool_descriptions()}
    assert set(tools) == EXPECTED
    assert tools["get_system_data"]["inputSchema"]["required"] == ["system"]
    assert tools["search_systems"]["inputSchema"]["properties"]["elements"]["anyOf"][0]["type"] == "array"
    fields = tools["get_system_data"]["inputSchema"]["properties"]["fields"]
    assert fields["enum"] == ["summary", "full"] and fields["default"] == "summary"
    assert tools["get_system_data"]["inputSchema"]["properties"]["limit"]["default"] == 50


def _rpc(proc, mid, method, params=None):
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


@pytest.mark.parametrize("fallback", [True, False])
def test_stdio_smoke(server_env, fallback, orchid):
    if not fallback and not server.sdk_available():
        pytest.skip("mcp SDK not installed")
    cmd = [sys.executable, "-m", "orchid_mcp"] + (["--fallback"] if fallback else [])
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=server_env)
    try:
        init = _rpc(proc, 1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        assert init["result"]["serverInfo"]["name"] == "orchid"
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        tools = _rpc(proc, 2, "tools/list")["result"]["tools"]
        assert {t["name"] for t in tools} == EXPECTED
        call = _rpc(proc, 3, "tools/call", {"name": "get_phase_diagram", "arguments": {"system": "Al-Ni", "assessment": "AlNi-05Mie", "x": 0.5, "T": 1200}})
        payload = json.loads(call["result"]["content"][0]["text"])
        assert payload["assessments"][0]["phase_at"]["stable_phases"] == ["BCC_A2"]
        bad = _rpc(proc, 4, "tools/call", {"name": "get_paper", "arguments": {"doi": "10.0000/none"}})
        assert bad["result"]["isError"] is True
        res = _rpc(proc, 5, "resources/list")["result"]["resources"]
        assert {r["uri"] for r in res} == {"orchid://schema", "orchid://dataset-card", "orchid://licence"}
        schema = _rpc(proc, 6, "resources/read", {"uri": "orchid://schema"})["result"]["contents"][0]["text"]
        assert "Record fields" in schema
        prompts = _rpc(proc, 7, "prompts/list")["result"]["prompts"]
        assert prompts[0]["name"] == "cite_orchid"
        msg = _rpc(proc, 8, "prompts/get", {"name": "cite_orchid", "arguments": {"topic": "Al-Ni"}})
        assert "DOI" in msg["result"]["messages"][0]["content"]["text"]
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
