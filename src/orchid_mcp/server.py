"""ORCHID MCP server.

Uses the official MCP Python SDK (``pip install mcp``; MCPServer in mcp 2.x, FastMCP in 1.x) when it is
installed. Without it, ``--transport stdio`` falls back to a small built-in
JSON-RPC 2.0 implementation of the MCP stdio protocol (tools, resources,
prompts), so the server still works in a bare Python environment.
"""

from __future__ import annotations

import argparse
import functools
import inspect
import json
import sys
import types
import typing
from typing import TYPE_CHECKING, Any, Literal

from . import __version__
from .datasource import DataSourceError
from .tools import Orchid, OrchidError, cite_orchid_prompt

if TYPE_CHECKING:
    from collections.abc import Callable

server_name = "orchid"
server_instructions = (
    "ORCHID: CALPHAD thermodynamic data with provenance. Literature values (experimental | model | dft | "
    "unspecified, never mixed), calculated binary phase diagrams tied to TDB files by SHA-256, Materials Project "
    "and OQMD formation energies, the TDB catalog, the phase registry (every name of each binary "
    "phase, its TDB models, stability and crystal structure when certain), and the bibliography (every paper ORCHID knows, "
    "its roles, evidence and typed relations: cites, used_in_assessment, reassesses, ...). Every value carries its citation, DOI, page and quote; "
    "report them, and cite ORCHID plus the original sources (see the cite_orchid prompt)."
)

Fields = Literal["summary", "full"]

_ORCHID: Orchid | None = None


def orchid() -> Orchid:
    """Shared Orchid instance, created on first use from the environment.

    Returns:
        The instance.
    """
    global _ORCHID  # noqa: PLW0603 (module-level singleton)
    if _ORCHID is None:
        _ORCHID = Orchid()
    return _ORCHID


def _call(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    try:
        return fn(**kwargs)
    except (OrchidError, DataSourceError) as exc:
        return {"error": str(exc)}


def search_systems(
    elements: list[str] | None = None,
    text: str | None = None,
    exact: bool = False,
    limit: int = 50,
    offset: int = 0,
    fields: Fields = "summary",
) -> dict:
    """Find chemical systems in ORCHID. elements: e.g. ["Al","Ni"] (systems containing all of them; exact=true for
    exactly them). text: matches system names and titles/citations of reporting papers. Returns per-system counts
    by evidence class and the calculated assessments; fields="full" adds property-group counts, assessment
    citations/DOIs and viewer URLs. Paginate with offset; `total` is the number of matching systems.
    """
    return _call(orchid().search_systems, elements=elements, text=text, exact=exact, limit=limit, offset=offset, fields=fields)


def get_system_data(
    system: str,
    property_group: str | None = None,
    evidence_class: str | None = None,
    accepted_only: bool = True,
    limit: int = 50,
    offset: int = 0,
    property: str | None = None,
    fields: Fields = "summary",
    compact: bool = False,
) -> dict:
    """Literature values for a system (e.g. "Al-Ni"). Filters: property_group (invariant reaction, phase transition,
    phase equilibrium, formation, mixing / partial, heat capacity / entropy / enthalpy, activity / EMF / Gibbs
    energy, crystal structure, model expression, other property), property code (HM_FORM, CPM, ...), evidence_class
    (experimental|model|dft|unspecified). Paginate with limit/offset (default 50); `total` counts all matches.
    fields: "summary" (default; id, property, value, unit, T, phases, evidence class, accepted, espei_ready, espei_certified, calphad_check, citation, DOI, page,
    quote <= 240 chars or table locator) or "full" (every field with expanded provenance: locator, origin, flags,
    uncertainty, conditions, cited sources, model link; compact=true then drops pipeline bookkeeping).
    """
    return _call(
        orchid().get_system_data,
        system=system,
        property_group=property_group,
        evidence_class=evidence_class,
        accepted_only=accepted_only,
        limit=limit,
        offset=offset,
        property=property,
        fields=fields,
        compact=compact,
    )


def get_invariant_reactions(
    system: str,
    source: str = "both",
    assessment: str | None = None,
    accepted_only: bool = True,
    limit: int = 50,
    offset: int = 0,
    fields: Fields = "summary",
) -> dict:
    """Invariant reactions of a system: literature records (source="literature"), invariants calculated from each
    SGTE assessment (source="calculated"), or both side by side with phase-set matches and dT. Calculated
    invariants are matched against all literature records; the literature list is paged with limit/offset
    (literature_total counts all).
    fields: "summary" (default; id, property, value, unit, T, phases, evidence class, accepted, espei_ready, espei_certified, calphad_check, citation, DOI, page,
    quote <= 240 chars or table locator) or "full" (complete provenance).
    """
    return _call(
        orchid().get_invariant_reactions,
        system=system,
        source=source,
        assessment=assessment,
        accepted_only=accepted_only,
        limit=limit,
        offset=offset,
        fields=fields,
    )


def get_formation_enthalpy(
    formula: str, accepted_only: bool = True, include_higher_order_systems: bool = True, fields: Fields = "summary"
) -> dict:
    """Formation enthalpy of a compound (e.g. "Al3Ni"): literature values in kJ/mol-atom with provenance,
    Materials Project and OQMD DFT entries at that composition, and per-evidence-class / per-source summaries.
    fields: "summary" (default; id, property, value, unit, T, phases, evidence class, accepted, espei_ready, espei_certified, calphad_check, citation, DOI, page,
    quote <= 240 chars or table locator) or "full" (complete provenance).
    """
    return _call(
        orchid().get_formation_enthalpy,
        formula=formula,
        accepted_only=accepted_only,
        include_higher_order_systems=include_higher_order_systems,
        fields=fields,
    )


def get_phase_diagram(
    system: str, assessment: str | None = None, x: float | None = None, T: float | None = None, fields: Fields = "summary"
) -> dict:
    """Calculated binary phase diagram(s): TDB file, SHA-256, assessment citation/DOI, phases, invariants, viewer
    URL. assessment: TDB name (e.g. "AlNi-05Mie") or DOI; omit for all. With x (mole fraction of the diagram's
    x_component) and T (K): the stable phases at the nearest stored grid node. fields="full" adds the per-region
    boundary summary and grid metadata.
    """
    return _call(orchid().get_phase_diagram, system=system, assessment=assessment, x=x, T=T, fields=fields)


def list_tdb_files(
    elements: list[str] | None = None,
    exact: bool = False,
    tier: str | None = None,
    limit: int = 50,
    offset: int = 0,
    fields: Fields = "summary",
) -> dict:
    """TDB catalog entries for systems containing all `elements` (exact=true: exactly them). tier: bundled |
    fetched | manual. Returns name, elements, tier, licence, licence status, source URL, SHA-256 and, for bundled
    files, the ORCHID download URL; fields="full" returns the whole catalog entry. Paginate with offset.
    """
    return _call(orchid().list_tdb_files, elements=elements, exact=exact, tier=tier, limit=limit, offset=offset, fields=fields)


def get_tdb(file: str, include_content: bool = True) -> dict:
    """One TDB catalog entry (e.g. "AlNi-05Mie.tdb") with licence, SHA-256 and download URL. The file content is
    returned only for bundled files with a confirmed redistributable licence; fetched/manual tiers are never served.
    """
    return _call(orchid().get_tdb, file=file, include_content=include_content)


def get_paper(doi: str, fields: Fields = "summary") -> dict:
    """Bibliographic record of a paper by DOI (or ORCHID id: "doi:...", "nodoi:...", literature "ref:..." ids, or a
    bibliography key "p..."): systems, number of values it reports, its experimental-context items (quotes, pages),
    and its ORCHID bibliography entry: roles (corpus_assessment_read, cited_original_source, tdb_source_assessment,
    handbook_diagram_source, discovery_lead, ...), evidence summary (values by evidence class / property group,
    tables, passages, ESPEI datasets, phase-registry phases), Crossref metadata, open-access licence from Crossref,
    relation counts. fields="full" adds the complete bibliography record and every relation (edges with provenance).
    """
    return _call(orchid().get_paper, doi=doi, fields=fields)


def search_papers(
    system: str | None = None,
    elements: list[str] | None = None,
    role: str | None = None,
    property: str | None = None,
    evidence_class: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    text: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Search the ORCHID bibliography (every paper ORCHID knows about). system: exact chemical system ("Al-Ni");
    elements: papers with a system containing all of them. role: corpus_assessment_read | cited_original_source |
    tdb_source_assessment | tdb_compilation | handbook_diagram_source | discovery_lead | context_source |
    espei_reference. property: property code of values reported in / citing the paper (HM_FORM,
    INVARIANT_TEMPERATURE, ...). evidence_class: experimental | model | dft | unspecified. year_from / year_to;
    text: substring of title, authors or DOI. Sorted by year, then id; paginate with offset (`total` counts all).
    """
    return _call(
        orchid().search_papers,
        system=system,
        elements=elements,
        role=role,
        property=property,
        evidence_class=evidence_class,
        year_from=year_from,
        year_to=year_to,
        text=text,
        limit=limit,
        offset=offset,
    )


def get_paper_relations(
    doi: str, relation: str | None = None, direction: str = "both", limit: int = 100, offset: int = 0
) -> dict:
    """Typed relations of one paper (DOI or bibliography id), each with provenance. relation: cites (paper -> works
    printed as the source of its values; count = values) | used_in_assessment (data paper -> assessments that used
    it) | models_system_in_tdb (paper -> TDB file) | reassesses (later -> earlier assessment of the same exact
    system that it cites) | handbook_reproduces (handbook diagram -> original evaluation) | cited_by_crossref
    (publisher-deposited Crossref reference lists). direction: out | in | both. Examples: which original papers did
    this assessment use -> relation="cites", direction="out"; which assessments used this paper ->
    relation="used_in_assessment", direction="out"; reassessment chain -> relation="reassesses".
    """
    return _call(orchid().get_paper_relations, doi=doi, relation=relation, direction=direction, limit=limit, offset=offset)


def get_assessment_evidence(system: str, max_data_papers: int = 25) -> dict:
    """Assessments of an exact chemical system (papers behind its TDB files or read by ORCHID) with the original
    data papers each used (works printed as the source of the extracted values, with value counts and evidence
    classes), the TDB files, reassessment links (citation+same-system) and paper counts by role. max_data_papers
    caps the data-paper list per assessment (flag data_papers_truncated).
    """
    return _call(orchid().get_assessment_evidence, system=system, max_data_papers=max_data_papers)


def compare_to_dft(system: str, default_subset_only: bool = False, fields: Fields = "summary") -> dict:
    """Literature formation enthalpies vs Materials Project vs OQMD for the compounds of a system, with residuals
    (DFT - literature), MAE over the default experimental subset, and the DFT hull vertices of both sources.
    fields: "summary" (default; per literature value: id, value, evidence class, accepted, phase, T, citation, DOI,
    page, quote <= 240 chars, table, residuals) or "full" (method, cited sources, flags, DFT point details).
    """
    return _call(orchid().compare_to_dft, system=system, default_subset_only=default_subset_only, fields=fields)


def get_phase(system: str, phase: str, fields: Fields = "summary") -> dict:
    """One phase of a binary system from the ORCHID phase registry. system: e.g. "Al-Ni". phase: a registry id
    ("phase:al-ni:fcc-l12"), any namespaced name ("tdb:AlNi-01Dup-LB.tdb:L12_FCC", "paper:<doi>:<label>",
    "corpus:<id>", "mp:<id>", "oqmd:<id>") or a plain label ("L12_FCC", "NI3TI", "gamma'"); several matches return
    {ambiguous: true, matches}. Summary: structure (tier A name / B DFT-supported / C assessor-stated, prototype,
    Strukturbericht, Pearson, space group), names by namespace, TDB models with their verbatim PHASE and CONSTITUENT
    statements, stability per calculated diagram, literature counts, first 5 reference DOIs, viewer URL.
    fields="full": the complete record (tdb_definition with TYPE_DEFINITIONs, parsed model and parameter counts,
    all example references with citations, candidates).
    """
    return _call(orchid().get_phase, system=system, phase=phase, fields=fields)


def search_phases(
    elements: list[str] | None = None,
    name: str | None = None,
    prototype: str | None = None,
    tier: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Search the ORCHID phase registry (binary systems). elements: phases of systems containing all of them.
    name: substring of the phase name, TDB names, paper or corpus labels. prototype: prototype, Strukturbericht,
    Pearson symbol or structure-type id ("AuCu3", "L1_2" = "L12", "cP4"). tier: A | B | C | liquid | gas | none.
    status: assigned | needs_review | unresolved_no_candidates. Paginate with offset; `total` counts all matches.
    """
    return _call(
        orchid().search_phases,
        elements=elements,
        name=name,
        prototype=prototype,
        tier=tier,
        status=status,
        limit=limit,
        offset=offset,
    )


def get_structure_type(query: str) -> dict:
    """Crystal-structure types of the phase registry by id, prototype, Strukturbericht, Pearson symbol or space
    group ("AuCu3", "L1_2", "cP4", "Pm-3m", "221"): prototype, Strukturbericht, Pearson, space group, sources
    (Hallstedt 2025 crystallographic table, standard tables), number of registry phases and example phase ids.
    """
    return _call(orchid().get_structure_type, query=query)


tool_functions: list[Callable[..., dict]] = [
    search_systems,
    get_system_data,
    get_invariant_reactions,
    get_formation_enthalpy,
    get_phase_diagram,
    list_tdb_files,
    get_tdb,
    get_paper,
    search_papers,
    get_paper_relations,
    get_assessment_evidence,
    compare_to_dft,
    get_phase,
    search_phases,
    get_structure_type,
]

resource_table = {
    "orchid://schema": ("schema", "ORCHID literature data schema (SCHEMA.md)", "text/markdown"),
    "orchid://dataset-card": ("dataset-card", "ORCHID dataset card (README + CITATION.cff)", "text/markdown"),
    "orchid://licence": ("licence", "ORCHID data licences and attributions (LICENSES.md, NOTICE.md)", "text/markdown"),
    "orchid://bibliography-schema": (
        "bibliography-schema",
        "ORCHID bibliography schema (bibliography/SCHEMA.md)",
        "text/markdown",
    ),
}


def _schema_for(tp: Any) -> dict[str, Any]:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        inner = _schema_for(non_none[0]) if len(non_none) == 1 else {}
        return {"anyOf": [inner, {"type": "null"}]} if type(None) in args else inner
    if origin is typing.Literal:
        return {"type": "string", "enum": list(args)}
    if origin is list:
        return {"type": "array", "items": _schema_for(args[0]) if args else {}}
    return {str: {"type": "string"}, int: {"type": "integer"}, float: {"type": "number"}, bool: {"type": "boolean"}}.get(tp, {})


def tool_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    """JSON schema of a tool's parameters: types, required names and defaults.

    Args:
        fn (Callable[..., Any]): The tool function.

    Returns:
        An object schema with ``properties`` and ``required``.
    """
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
    """Name, description and input schema of every tool, as advertised by the server.

    Returns:
        One dict per tool.
    """
    return [
        {"name": fn.__name__, "description": inspect.cleandoc(fn.__doc__ or ""), "inputSchema": tool_schema(fn)}
        for fn in tool_functions
    ]


def _sdk_server_class() -> tuple[Any, int]:
    """The official SDK's high-level server class and its major API generation.

    mcp >= 2 renamed ``FastMCP`` to ``MCPServer`` (host/port moved to ``run``);
    mcp 1.x is still accepted.
    """
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: PLC0415 (optional SDK, mcp >= 2)

        return MCPServer, 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP  # noqa: PLC0415 (optional SDK, mcp 1.x)

        return FastMCP, 1


def _raising(fn: Callable[..., dict], gen: int) -> Callable[..., str]:
    """Wrap a tool for the SDK: an ``{"error": ...}`` result becomes a tool error (``isError: true``), and a
    result is returned as one compact JSON text block (the SDK would otherwise indent it, ~25% larger).
    """
    if gen >= 2:
        from mcp.server.mcpserver.exceptions import ToolError  # noqa: PLC0415 (optional SDK)
    else:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415 (optional SDK)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        result = fn(*args, **kwargs)
        if isinstance(result, dict) and "error" in result:
            raise ToolError(result["error"])
        return json.dumps(result, ensure_ascii=False)

    wrapper.__annotations__ = {**typing.get_type_hints(fn), "return": str}
    return wrapper


def build_fastmcp(host: str = "127.0.0.1", port: int = 8000) -> Any:
    """Build the server on the official MCP SDK (MCPServer in mcp 2.x, FastMCP in 1.x)."""
    cls, gen = _sdk_server_class()
    if gen >= 2:
        server = cls(server_name, instructions=server_instructions, version=__version__)
    else:
        server = cls(server_name, instructions=server_instructions, host=host, port=port)
    for fn in tool_functions:
        server.tool(structured_output=False)(_raising(fn, gen))
    for uri, (key, desc, mime) in resource_table.items():

        def _make(k: str, d: str) -> Callable[[], str]:
            def read() -> str:
                return orchid().resource_text(k)

            read.__name__ = f"resource_{k.replace('-', '_')}"
            read.__doc__ = d
            return read

        server.resource(uri, name=key, description=desc, mime_type=mime)(_make(key, desc))

    @server.prompt(name="cite_orchid", description="How to cite ORCHID and the original sources of each value")
    def cite_orchid(topic: str = "") -> str:
        return cite_orchid_prompt(topic)

    return server


def run_sdk(server: Any, transport: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run an SDK server on stdio or streamable HTTP; host and port apply to HTTP only."""
    if transport == "stdio":
        server.run(transport="stdio")
        return
    _, gen = _sdk_server_class()
    if gen >= 2:
        server.run(transport="streamable-http", host=host, port=port)
    else:
        server.run(transport="streamable-http")


def sdk_available() -> bool:
    """Whether the official MCP SDK can be imported.

    Returns:
        True when a server class of the SDK is available.
    """
    try:
        _sdk_server_class()
    except ImportError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point: parse options, then run the server on stdio or HTTP.

    Args:
        argv (list[str] | None): Arguments without the program name (defaults to ``sys.argv``).

    Returns:
        Process exit status.
    """
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
        run_sdk(build_fastmcp(args.host, args.port), "http", args.host, args.port)
        return 0
    if sdk_available() and not args.fallback:
        run_sdk(build_fastmcp(), "stdio")
    else:
        from .stdio import run_fallback_stdio  # noqa: PLC0415 (stdio imports this module)

        run_fallback_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
