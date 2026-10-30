"""Malformed input on the built-in stdio server: each bad line gets an answer and the server keeps serving."""

import io
import json
import subprocess
import sys
from unittest import TestCase

from conftest import server_env

from orchid_mcp.stdio import run_fallback_stdio

PING = b'{"jsonrpc": "2.0", "id": 2, "method": "ping"}\n'


class TestInvalidUtf8OnConsole(TestCase):
    """Bytes that are not UTF-8 on the console stdin must not end the server."""

    def test_invalid_utf8_line_gets_parse_error_and_next_request_is_answered(self):
        """A line of invalid UTF-8 bytes gets a parse error (id null); the following ping is still answered."""
        proc = subprocess.run(
            [sys.executable, "-m", "orchid_mcp", "--fallback"],
            input=b"\xff\xfe\n" + PING,
            capture_output=True,
            env=server_env(),
            timeout=120,
            check=False,
        )
        replies = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines()]
        assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
        assert replies == [
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
            {"jsonrpc": "2.0", "id": 2, "result": {}},
        ]


class TestDeeplyNestedJson(TestCase):
    """A line of JSON nested past the interpreter's recursion limit must not end the server."""

    def test_deeply_nested_line_gets_parse_error_and_next_request_is_answered(self):
        """Nesting deeper than the recursion limit gets a parse error (id null); the next ping is still answered."""
        stdin = io.StringIO("[" * 10000 + "]" * 10000 + "\n" + PING.decode("utf-8"))
        stdout = io.StringIO()
        run_fallback_stdio(stdin=stdin, stdout=stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert replies == [
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
            {"jsonrpc": "2.0", "id": 2, "result": {}},
        ]
