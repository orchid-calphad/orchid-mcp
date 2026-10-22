"""ORCHID MCP server.

Uses the official MCP Python SDK (``pip install mcp``; FastMCP) when it is
installed. Without it, ``--transport stdio`` falls back to a small built-in
JSON-RPC 2.0 implementation of the MCP stdio protocol (tools, resources,
prompts), so the server still works in a bare Python environment.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import types
import typing
from typing import Any, Callable

from . import __version__
from .datasource import DataSourceError
from .tools import Orchid, OrchidError, cite_orchid_prompt

SERVER_NAME = "orchid"
INSTRUCTIONS = (
    "ORCHID: CALPHAD thermodynamic data with provenance. Literature values (experimental | model | dft | "
    "unspecified, never mixed), calculated binary phase diagrams tied to TDB files by SHA-256, Materials Project "
    "and OQMD formation energies, and the TDB catalog. Every value carries its citation, DOI, page and quote; "
    "report them, and cite ORCHID plus the original sources (see the cite_orchid prompt)."
)

_ORCHID: Orchid | None = None


def orchid() -> Orchid:
    global _ORCHID
    if _ORCHID is None:
        _ORCHID = Orchid()
    return _ORCHID


def _call(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    try:
        return fn(**kwargs)
    except (OrchidError, DataSourceError) as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------- tools
def search_systems(elements: list[str] | None = None, text: str | None = None, exact: bool = False, limit: int = 50) -> dict:
    """Find chemical systems in ORCHID. elements: e.g. ["Al","Ni"] (systems containing all of them; exact=true for
    exactly them). text: matches system names and titles/citations of reporting papers. Returns per-system counts
    by evidence class and property group, calculated assessments, and viewer URLs."""
    return _call(orchid().search_systems, elements=elements, text=text, exact=exact, limit=limit)


def get_system_data(
    system: str,
    property_group: str | None = None,
    evidence_class: str | None = None,
    accepted_only: bool = True,
    limit: int = 200,
    offset: int = 0,
    property: str | None = None,
    compact: bool = False,
) -> dict:
    """Literature values for a system (e.g. "Al-Ni"). Filters: property_group (invariant reaction, phase transition,
    phase equilibrium, formation, mixing / partial, heat capacity / entropy / enthalpy, activity / EMF / Gibbs
    energy, crystal structure, model expression, other property), property code (HM_FORM, CPM, ...), evidence_class
    (experimental|model|dft|unspecified). Each value carries source citation, DOI, page, quote or table locator,
    evidence class, origin, flags and uncertainty basis. Paginate with offset."""
    return _call(
        orchid().get_system_data,
        system=system,
        property_group=property_group,
        evidence_class=evidence_class,
        accepted_only=accepted_only,
        limit=limit,
        offset=offset,
        property=property,
        compact=compact,
    )


def get_invariant_reactions(system: str, source: str = "both", assessment: str | None = None, accepted_only: bool = True) -> dict:
    """Invariant reactions of a system: literature records (source="literature"), invariants calculated from each
    SGTE assessment (source="calculated"), or both side by side with phase-set matches and dT."""
    return _call(orchid().get_invariant_reactions, system=system, source=source, assessment=assessment, accepted_only=accepted_only)


def get_formation_enthalpy(formula: str, accepted_only: bool = True, include_higher_order_systems: bool = True) -> dict:
    """Formation enthalpy of a compound (e.g. "Al3Ni"): literature values in kJ/mol-atom with provenance,
    Materials Project and OQMD DFT entries at that composition, and per-evidence-class / per-source summaries."""
    return _call(
        orchid().get_formation_enthalpy,
        formula=formula,
        accepted_only=accepted_only,
        include_higher_order_systems=include_higher_order_systems,
    )


def get_phase_diagram(system: str, assessment: str | None = None, x: float | None = None, T: float | None = None) -> dict:
    """Calculated binary phase diagram(s): TDB file, SHA-256, assessment citation/DOI, invariants, region summary,
    viewer URL. assessment: TDB name (e.g. "AlNi-05Mie") or DOI; omit for all. With x (mole fraction of the
    diagram's x_component) and T (K): the stable phases at the nearest stored grid node."""
    return _call(orchid().get_phase_diagram, system=system, assessment=assessment, x=x, T=T)


def list_tdb_files(elements: list[str] | None = None, exact: bool = False, tier: str | None = None, limit: int = 200) -> dict:
    """TDB catalog entries for systems containing all `elements` (exact=true: exactly them). tier: bundled |
    fetched | manual. Returns licence, licence status, source URL and, for bundled files, the ORCHID download URL."""
    return _call(orchid().list_tdb_files, elements=elements, exact=exact, tier=tier, limit=limit)


def get_tdb(file: str, include_content: bool = True) -> dict:
    """One TDB catalog entry (e.g. "AlNi-05Mie.tdb") with licence, SHA-256 and download URL. The file content is
    returned only for bundled files with a confirmed redistributable licence; fetched/manual tiers are never served."""
    return _call(orchid().get_tdb, file=file, include_content=include_content)


def get_paper(doi: str) -> dict:
    """Bibliographic record of a paper by DOI (or ORCHID paper id): systems, number of values it reports,
    and its experimental-context items (samples, treatments, measurements) with quotes and pages."""
    return _call(orchid().get_paper, doi=doi)


def compare_to_dft(system: str, default_subset_only: bool = False) -> dict:
    """Literature formation enthalpies vs Materials Project vs OQMD for the compounds of a system, with residuals
    (DFT - literature), MAE over the default experimental subset, and the DFT hull vertices of both sources."""
    return _call(orchid().compare_to_dft, system=system, default_subset_only=default_subset_only)


TOOLS: list[Callable[..., dict]] = [
    search_systems,
    get_system_data,
    get_invariant_reactions,
    get_formation_enthalpy,
    get_phase_diagram,
    list_tdb_files,
    get_tdb,
    get_paper,
    compare_to_dft,
]

RESOURCES = {
    "orchid://schema": ("schema", "ORCHID literature data schema (SCHEMA.md)", "text/markdown"),
    "orchid://dataset-card": ("dataset-card", "ORCHID dataset card (README + CITATION.cff)", "text/markdown"),
    "orchid://licence": ("licence", "ORCHID data licences and attributions (LICENSES.md, NOTICE.md)", "text/markdown"),
}


# ------------------------------------------------------------ JSON schema
def _schema_for(tp: Any) -> dict[str, Any]:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        inner = _schema_for(non_none[0]) if len(non_none) == 1 else {}
        return {"anyOf": [inner, {"type": "null"}]} if type(None) in args else inner
    if origin is list:
        return {"type": "array", "items": _schema_for(args[0]) if args else {}}
    return {str: {"type": "string"}, int: {"type": "integer"}, float: {"type": "number"}, bool: {"type": "boolean"}}.get(tp, {})


def tool_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    hints = typing.get_type_hints(fn)
    props: dict[str, Any] = {}
    required = []
    for name, p in inspect.signature(fn).parameters.items():
        sch = dict(_schema_for(hints.get(name, str)))
        if p.default is inspect.Parameter.empty:
            required.append(name)
        else:
            sch["default"] = p.default
        props[name] = sch
    return {"type": "object", "properties": props, "required": required}


def tool_descriptions() -> list[dict[str, Any]]:
    return [
        {"name": fn.__name__, "description": inspect.cleandoc(fn.__doc__ or ""), "inputSchema": tool_schema(fn)}
        for fn in TOOLS
    ]


# ------------------------------------------------------- official SDK path
def build_fastmcp(host: str = "127.0.0.1", port: int = 8000) -> Any:
    from mcp.server.fastmcp import FastMCP  # lazy: optional dependency

    server = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS, host=host, port=port)
    for fn in TOOLS:
        server.tool()(fn)
    for uri, (key, desc, mime) in RESOURCES.items():

        def _make(k: str) -> Callable[[], str]:
            def read() -> str:
                return orchid().resource_text(k)

            read.__name__ = f"resource_{k.replace('-', '_')}"
            read.__doc__ = desc
            return read

        server.resource(uri, name=key, description=desc, mime_type=mime)(_make(key))

    @server.prompt(name="cite_orchid", description="How to cite ORCHID and the original sources of each value")
    def cite_orchid(topic: str = "") -> str:
        return cite_orchid_prompt(topic)

    return server


# --------------------------------------------------- built-in stdio fallback
PROTOCOL_VERSION = "2025-06-18"


def handle_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message of the MCP protocol (fallback server)."""
    mid = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}
    if mid is None:  # notification
        return None

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok(
            {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        )
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_descriptions()})
    if method == "tools/call":
        by_name = {fn.__name__: fn for fn in TOOLS}
        fn = by_name.get(params.get("name"))
        if fn is None:
            return err(-32602, f"unknown tool {params.get('name')!r}")
        args = params.get("arguments") or {}
        allowed = set(inspect.signature(fn).parameters)
        bad = set(args) - allowed
        if bad:
            return ok({"content": [{"type": "text", "text": f"unknown arguments: {sorted(bad)}"}], "isError": True})
        try:
            result = fn(**args)
        except TypeError as exc:
            return ok({"content": [{"type": "text", "text": f"bad arguments: {exc}"}], "isError": True})
        text = json.dumps(result, ensure_ascii=False)
        return ok({"content": [{"type": "text", "text": text}], "structuredContent": result, "isError": "error" in result})
    if method == "resources/list":
        return ok({"resources": [{"uri": u, "name": k, "description": d, "mimeType": m} for u, (k, d, m) in RESOURCES.items()]})
    if method == "resources/read":
        uri = params.get("uri")
        if uri not in RESOURCES:
            return err(-32602, f"unknown resource {uri!r}")
        key, _, mime = RESOURCES[uri]
        return ok({"contents": [{"uri": uri, "mimeType": mime, "text": orchid().resource_text(key)}]})
    if method == "prompts/list":
        return ok(
            {
                "prompts": [
                    {
                        "name": "cite_orchid",
                        "description": "How to cite ORCHID and the original sources of each value",
                        "arguments": [{"name": "topic", "description": "optional topic", "required": False}],
                    }
                ]
            }
        )
    if method == "prompts/get":
        if params.get("name") != "cite_orchid":
            return err(-32602, f"unknown prompt {params.get('name')!r}")
        topic = (params.get("arguments") or {}).get("topic", "")
        return ok(
            {
                "description": "How to cite ORCHID and the original sources",
                "messages": [{"role": "user", "content": {"type": "text", "text": cite_orchid_prompt(topic)}}],
            }
        )
    return err(-32601, f"method not found: {method}")


def run_fallback_stdio(stdin: Any = None, stdout: Any = None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
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


def sdk_available() -> bool:
    try:
        import mcp.server.fastmcp  # noqa: F401
    except ImportError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orchid-mcp", description="ORCHID MCP server (CALPHAD data with provenance)")
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio", help="stdio (default) or streamable HTTP")
    ap.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    ap.add_argument("--fallback", action="store_true", help="use the built-in stdio server even if `mcp` is installed")
    ap.add_argument("--version", action="version", version=f"orchid-mcp {__version__}")
    args = ap.parse_args(argv)
    if args.transport == "http":
        if not sdk_available():
            print("streamable HTTP needs the MCP SDK: pip install mcp", file=sys.stderr)
            return 2
        build_fastmcp(args.host, args.port).run(transport="streamable-http")
        return 0
    if sdk_available() and not args.fallback:
        build_fastmcp().run(transport="stdio")
    else:
        run_fallback_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
