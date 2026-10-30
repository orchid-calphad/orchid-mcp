"""Built-in MCP stdio server: a small JSON-RPC 2.0 implementation used when the official SDK is not installed.

It serves the same tools, resources and prompts as the SDK path in ``server``. Requests and responses are
line-delimited JSON on stdin and stdout.
"""

from __future__ import annotations

import inspect
import json
import sys
from typing import Any

from . import __version__
from .prompts import cite_orchid_prompt
from .server import (
    orchid,
    resource_table,
    server_instructions,
    server_name,
    tool_descriptions,
    tool_functions,
)

protocol_version = "2025-06-18"


def _ok(mid: Any, result: Any) -> dict[str, Any]:
    """JSON-RPC success response for one request.

    Args:
        mid (Any): Request id, echoed back.
        result (Any): Result payload.

    Returns:
        The response object.
    """
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: Any, code: int, message: str) -> dict[str, Any]:
    """JSON-RPC error response for one request.

    Args:
        mid (Any): Request id, echoed back.
        code (int): JSON-RPC error code.
        message (str): Human-readable message.

    Returns:
        The response object.
    """
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _rpc_initialize(mid: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Answer initialize with the protocol version, capabilities and instructions.

    Args:
        mid (Any): Request id.
        params (dict[str, Any]): Request parameters; the client's protocol version is echoed when given.

    Returns:
        The response object.
    """
    return _ok(
        mid,
        {
            "protocolVersion": params.get("protocolVersion") or protocol_version,
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": server_name, "version": __version__},
            "instructions": server_instructions,
        },
    )


def _rpc_ping(mid: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Answer ping with an empty result.

    Args:
        mid (Any): Request id.
        params (dict[str, Any]): Unused.

    Returns:
        The response object.
    """
    del params
    return _ok(mid, {})


def _rpc_tools_list(mid: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Answer tools/list with the tool descriptions.

    Args:
        mid (Any): Request id.
        params (dict[str, Any]): Unused.

    Returns:
        The response object.
    """
    del params
    return _ok(mid, {"tools": tool_descriptions()})


def _rpc_tools_call(mid: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Answer tools/call: validate the arguments, run the tool, return its JSON as text and as structured content.

    Args:
        mid (Any): Request id.
        params (dict[str, Any]): Request parameters with ``name`` and ``arguments``.

    Returns:
        The response object; tool errors and bad arguments come back with ``isError`` set.
    """
    by_name = {fn.__name__: fn for fn in tool_functions}
    fn = by_name.get(params.get("name"))
    if fn is None:
        return _err(mid, -32602, f"unknown tool {params.get('name')!r}")
    args = params.get("arguments") or {}
    bad = set(args) - set(inspect.signature(fn).parameters)
    if bad:
        return _ok(mid, {"content": [{"type": "text", "text": f"unknown arguments: {sorted(bad)}"}], "isError": True})
    try:
        result = fn(**args)
    except TypeError as exc:
        return _ok(mid, {"content": [{"type": "text", "text": f"bad arguments: {exc}"}], "isError": True})
    text = json.dumps(result, ensure_ascii=False)
    return _ok(
        mid,
        {"content": [{"type": "text", "text": text}], "structuredContent": result, "isError": "error" in result},
    )


def _rpc_resources_list(mid: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Answer resources/list with the documentation resources.

    Args:
        mid (Any): Request id.
        params (dict[str, Any]): Unused.

    Returns:
        The response object.
    """
    del params
    return _ok(
        mid,
        {"resources": [{"uri": u, "name": k, "description": d, "mimeType": m} for u, (k, d, m) in resource_table.items()]},
    )


def _rpc_resources_read(mid: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Answer resources/read with the text of one documentation resource.

    Args:
        mid (Any): Request id.
        params (dict[str, Any]): Request parameters with ``uri``.

    Returns:
        The response object, or an error for an unknown URI.
    """
    uri = params.get("uri")
    if uri not in resource_table:
        return _err(mid, -32602, f"unknown resource {uri!r}")
    key, _, mime = resource_table[uri]
    return _ok(mid, {"contents": [{"uri": uri, "mimeType": mime, "text": orchid().resource_text(key)}]})


def _rpc_prompts_list(mid: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Answer prompts/list with the cite_orchid prompt.

    Args:
        mid (Any): Request id.
        params (dict[str, Any]): Unused.

    Returns:
        The response object.
    """
    del params
    return _ok(
        mid,
        {
            "prompts": [
                {
                    "name": "cite_orchid",
                    "description": "How to cite ORCHID and the original sources of each value",
                    "arguments": [{"name": "topic", "description": "optional topic", "required": False}],
                }
            ]
        },
    )


def _rpc_prompts_get(mid: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Answer prompts/get with the rendered cite_orchid prompt.

    Args:
        mid (Any): Request id.
        params (dict[str, Any]): Request parameters with ``name`` and optional ``arguments``.

    Returns:
        The response object, or an error for an unknown prompt name.
    """
    if params.get("name") != "cite_orchid":
        return _err(mid, -32602, f"unknown prompt {params.get('name')!r}")
    topic = (params.get("arguments") or {}).get("topic", "")
    return _ok(
        mid,
        {
            "description": "How to cite ORCHID and the original sources",
            "messages": [{"role": "user", "content": {"type": "text", "text": cite_orchid_prompt(topic)}}],
        },
    )


_rpc_handlers: dict[str, Any] = {
    "initialize": _rpc_initialize,
    "ping": _rpc_ping,
    "tools/list": _rpc_tools_list,
    "tools/call": _rpc_tools_call,
    "resources/list": _rpc_resources_list,
    "resources/read": _rpc_resources_read,
    "prompts/list": _rpc_prompts_list,
    "prompts/get": _rpc_prompts_get,
}


def handle_request(msg: Any) -> dict[str, Any] | None:
    """Handle one JSON-RPC message of the MCP protocol (fallback server).

    Args:
        msg (Any): Decoded JSON value. A message without an ``id`` is a notification; a value that is not an
            object is an invalid request (-32600).

    Returns:
        The response object, or None for a notification.
    """
    if not isinstance(msg, dict):
        return _err(None, -32600, "invalid request")
    mid = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}
    if mid is None:  # notification
        return None
    handler = _rpc_handlers.get(method) if isinstance(method, str) else None
    if handler is None:
        return _err(mid, -32601, f"method not found: {method}")
    return handler(mid, params)


def run_fallback_stdio(stdin: Any = None, stdout: Any = None) -> None:
    """Serve the built-in JSON-RPC protocol over line-delimited stdin and stdout.

    Args:
        stdin (Any): Text stream to read requests from (defaults to ``sys.stdin``).
        stdout (Any): Text stream to write responses to (defaults to ``sys.stdout``).
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        text = line.strip()
        if not text:
            continue
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            resp: dict[str, Any] | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            try:
                resp = handle_request(msg)
            except Exception as exc:  # keep the server alive
                resp = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": str(exc)}}
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()
