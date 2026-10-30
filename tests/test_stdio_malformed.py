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


class TestUnencodableEcho(TestCase):
    """A reply that echoes a lone surrogate from the request must not end the server."""

    def test_lone_surrogate_in_reply_is_escaped_and_next_request_is_answered(self):
        """A prompt topic holding a lone surrogate is answered with the surrogate escaped; the next ping is still answered."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "prompts/get",
            "params": {"name": "cite_orchid", "arguments": {"topic": "\udc80"}},
        }
        stdin = io.StringIO(json.dumps(request) + "\n" + PING.decode("utf-8"))
        raw = io.BytesIO()
        stdout = io.TextIOWrapper(raw, encoding="utf-8", newline="\n")
        run_fallback_stdio(stdin=stdin, stdout=stdout)
        stdout.flush()
        replies = [json.loads(line) for line in raw.getvalue().decode("utf-8").splitlines()]
        assert len(replies) == 2
        assert replies[0]["id"] == 1
        assert "\udc80" in replies[0]["result"]["messages"][0]["content"]["text"]
        assert replies[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


class TestOversizedInteger(TestCase):
    """An integer literal over the interpreter's digit limit must not end the server."""

    def test_oversized_integer_gets_parse_error_and_next_request_is_answered(self):
        """A 5,000-digit integer gets a parse error (id null); the next ping is still answered."""
        stdin = io.StringIO('{"jsonrpc": "2.0", "id": 1, "params": {"n": ' + "9" * 5000 + "}}\n" + PING.decode("utf-8"))
        stdout = io.StringIO()
        run_fallback_stdio(stdin=stdin, stdout=stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert replies == [
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
            {"jsonrpc": "2.0", "id": 2, "result": {}},
        ]
