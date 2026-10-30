"""Phase registry of binary systems: phases by name, structure types and their tiers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .constants import public_data_url, site_url
from .core import check_fields, wanted_elements
from .datasource import NotFound
from .errors import OrchidError
from .summaries import shorten

if TYPE_CHECKING:
    from .core import OrchidCore

phase_names_max = 30  # names listed per namespace in the summary
phase_models_max = 50  # TDB models listed per phase
phase_refs_summary = 5  # reference DOIs in the summary
phase_examples = 5  # example phases per structure type
tdb_statement_max = 4000  # characters of a verbatim TDB statement kept in the full form
phases_page_max = 500
structure_type_limit = 20  # structure types returned by default
phase_structure_keys = (
    "structure_type",
    "basis",
    "tier",
    "confidence",
    "prototype",
    "strukturbericht",
    "pearson",
    "space_group",
)
phase_tiers = {
    "A": "standard_name: the TDB phase name encodes one structure; model stoichiometry/sublattices and DFT agree",
    "B": "dft_unique_match ('DFT-supported'): one on-hull entry in both Materials Project and OQMD, same space group",
    "C": "assessment_declared ('assessor-stated'): one structure in the Hallstedt 2025 crystallographic table (mmc2)",
    "liquid": "liquid (no prototype)",
    "gas": "gas (no prototype)",
    "none": "no structure assigned (needs_review with candidates, or unresolved)",
}
phase_cite_text = (
    "Cite the ORCHID phase registry (binary systems) of the release you used (phases/CITATION.cff; Zenodo record "
    "phase-registry), the original CALPHAD assessment of every TDB model you use (references with role 'assessment "
    "that models this phase'), B. Hallstedt, Calphad 89 (2025) 102833, doi:10.1016/j.calphad.2025.102833 for the "
    "SGTE TDB files and tier-C structures, and Materials Project / OQMD for tier-B (DFT-supported) structures."
)
_tier_aliases = {"a": "A", "b": "B", "c": "C", "liquid": "liquid", "gas": "gas", "none": "none"}


def struct_norm(x: Any) -> str:
    """Case- and separator-insensitive key for structure labels (L1_2 == L12, D0_19 == D019, 'Pm-3m' == 'pm3m').

    Args:
        x (Any): A label; None gives an empty key.

    Returns:
        The lower-case key with whitespace, underscores, hyphens, parentheses, primes, commas and dots removed.
    """
    if x is None:
        return ""
    return re.sub(r"[\s_\-()'′,.]", "", str(x)).lower()


def _phase_tier(r: dict[str, Any]) -> str | None:
    """Tier letter of a registry row, with ``liquid``, ``gas`` or ``none`` for phases without a structure.

    Args:
        r (dict[str, Any]): Registry row.

    Returns:
        The tier key used by ``phase_tiers``.
    """
    s = r.get("structure") or {}
    if not s:
        return "none"
    if s.get("basis") in ("liquid", "gas"):
        return s["basis"]
    return s.get("tier")


def _phase_links(pid: str, sid: str) -> dict[str, str]:
    """Viewer, data and static-page links of one phase.

    Args:
        pid (str): Registry phase id.
        sid (str): Binary system id.

    Returns:
        ``viewer_url``, ``data_url`` and ``static_page_url``.
    """
    return {
        "viewer_url": f"{site_url}phases/#{pid}",
        "data_url": f"{public_data_url}phases/systems/{sid}.json",
        "static_page_url": f"{public_data_url}phases/pages/{sid}.html#" + re.sub(r"[^a-z0-9]+", "-", pid.lower()).strip("-"),
    }


def _labels_of(r: dict[str, Any]) -> set[str]:
    """All lower-case labels by which a phase can be named (preferred, TDB, namespaced and bare names, id tail).

    Args:
        r (dict[str, Any]): Registry row.

    Returns:
        The non-empty labels.
    """
    out = {str(r.get("preferred_name") or "").lower()}
    out |= {str(n).lower() for n in r.get("tdb_names") or []}
    for n in r.get("names") or []:
        out.add(str(n).lower())
        out.add(str(n).rsplit(":", 1)[-1].lower())
    out.add(str(r.get("id") or "").rsplit(":", 1)[-1].lower())
    out.discard("")
    return out


def _phase_row_summary(r: dict[str, Any]) -> dict[str, Any]:
    """Compact registry row used in lists and ambiguity responses.

    Args:
        r (dict[str, Any]): Registry row.

    Returns:
        Id, system, names, status, structure, TDB names, stoichiometry, name count, counts and viewer link.
    """
    names = r.get("names") or []
    return {
        "id": r.get("id"),
        "system": r.get("system"),
        "preferred_name": r.get("preferred_name"),
        "status": r.get("status"),
        "structure": r.get("structure"),
        "tdb_names": r.get("tdb_names") or [],
        "stoichiometry": r.get("stoichiometry"),
        "n_names": len(names),
        "counts": r.get("counts") or {},
        "viewer_url": f"{site_url}phases/#{r.get('id')}",
    }


def _phase_model_summary(m: dict[str, Any]) -> dict[str, Any]:
    """Short form of one TDB model of a phase: sublattices and shortened verbatim statements.

    Args:
        m (dict[str, Any]): TDB model entry.

    Returns:
        Model name, phase name, type code, sublattices, shortened PHASE and CONSTITUENT statements and website URL.
    """
    v = ((m.get("tdb_definition") or {}).get("verbatim") or {})
    return {
        "tdb": m.get("tdb"),
        "phase_name": m.get("phase_name"),
        "type_code": m.get("type_code"),
        "sublattices": " : ".join(
            f"{sl.get('site_ratio')} ({','.join(sl.get('constituents') or [])})" for sl in m.get("sublattices") or []
        ),
        "phase_statement": shorten((v.get("phase") or {}).get("text")),
        "constituent_statement": shorten((v.get("constituent") or {}).get("text")),
        "website_url": ((m.get("tdb_definition") or {}).get("provenance") or {}).get("website_url"),
    }


def _phase_summary(e: dict[str, Any]) -> dict[str, Any]:
    """Summary of a registry phase: structure tier, names by namespace, models, stability and literature counts.

    Args:
        e (dict[str, Any]): Full registry phase (from ``phases/systems/<sid>.json``).

    Returns:
        The summary with links; long lists are truncated and flagged.
    """
    by_ns: dict[str, list[str]] = {}
    for n in e.get("names") or []:
        by_ns.setdefault(n.get("namespace") or "?", []).append(n.get("name"))
    names = {
        ns: {"n": len(v), "names": v[:phase_names_max], **({"truncated": True} if len(v) > phase_names_max else {})}
        for ns, v in sorted(by_ns.items())
    }
    stab = [
        {k: s.get(k) for k in ("tdb", "stable", "T_min_K", "T_max_K", "x_element", "x_min", "x_max") if k in s}
        for s in e.get("stability") or []
        if s.get("status") == "ok"
    ]
    models = [_phase_model_summary(m) for m in (e.get("models") or [])[:phase_models_max]]
    refs = e.get("references") or []
    out = {
        "id": e.get("id"),
        "system": e.get("system"),
        "preferred_name": e.get("preferred_name"),
        "status": e.get("status"),
        "structure": {k: (e.get("structure") or {}).get(k) for k in phase_structure_keys} if e.get("structure") else None,
        "review_reasons": e.get("review_reasons") or [],
        "n_candidates": len(e.get("candidates") or []),
        "stoichiometry": e.get("stoichiometry"),
        "names": names,
        "models": models,
        "n_models": len(e.get("models") or []),
        "stability": stab,
        "literature": e.get("literature"),
        "references": [{"doi": r.get("doi"), "roles": r.get("roles")} for r in refs[:phase_refs_summary]],
        "total_papers": e.get("total_papers"),
        **_phase_links(e["id"], e["system"]),
    }
    if len(e.get("models") or []) > phase_models_max:
        out["models_truncated"] = True
    return out


def _phase_full(e: dict[str, Any]) -> dict[str, Any]:
    """Full registry phase: all fields, with models capped and long verbatim statements shortened.

    Args:
        e (dict[str, Any]): Full registry phase.

    Returns:
        A copy with ``models`` capped at ``phase_models_max`` and links added.
    """
    out = dict(e)
    out["models"] = [_full_model(m) for m in (e.get("models") or [])[:phase_models_max]]
    if len(e.get("models") or []) > phase_models_max:
        out["models_truncated"] = f"first {phase_models_max} of {len(e['models'])}; see data_url"
    out.update(_phase_links(e["id"], e["system"]))
    return out


def _full_model(m: dict[str, Any]) -> dict[str, Any]:
    """Copy of a TDB model with its verbatim statements and type definitions shortened to ``tdb_statement_max``.

    Args:
        m (dict[str, Any]): TDB model entry.

    Returns:
        The copy (the input is not modified).
    """
    m = dict(m)
    td = m.get("tdb_definition")
    if td:
        td = dict(td)
        vb = dict(td.get("verbatim") or {})
        for key in ("phase", "constituent"):
            if isinstance(vb.get(key), dict) and len(vb[key].get("text") or "") > tdb_statement_max:
                vb[key] = {**vb[key], "text": shorten(vb[key]["text"], tdb_statement_max), "text_truncated": True}
        vb["type_definitions"] = [
            {**t, "text": shorten(t.get("text"), tdb_statement_max)} for t in vb.get("type_definitions") or []
        ]
        td["verbatim"] = vb
        m["tdb_definition"] = td
    return m


def _find_rows(rows: list[dict[str, Any]], ql: str) -> list[dict[str, Any]]:
    """Registry rows matching a lower-case phase query, trying id, then names, then labels, then label substrings.

    Args:
        rows (list[dict[str, Any]]): Registry rows of one system.
        ql (str): Lower-case query.

    Returns:
        The rows of the first stage that matches anything (empty when none does).
    """
    stages = (
        lambda r: str(r.get("id")).lower() == ql,
        lambda r: ql in {str(n).lower() for n in r.get("names") or []},
        lambda r: ql in _labels_of(r),
        lambda r: any(ql in lab for lab in _labels_of(r)),
    )
    for stage in stages:
        hits = [r for r in rows if stage(r)]
        if hits:
            return hits
    return []


def _structural_match(r: dict[str, Any], want: set[str], t: str | None, status: str | None) -> bool:
    """Whether a registry row has the elements, tier and status asked for.

    Args:
        r (dict[str, Any]): Registry row.
        want (set[str]): Elements the system must contain (empty: no filter).
        t (str | None): Tier key (``A``, ``B``, ``C``, ``liquid``, ``gas``, ``none``) or None.
        status (str | None): Required status, or None.

    Returns:
        True when all filters match.
    """
    if want and not want <= set(r.get("elements") or str(r.get("system", "")).split("-")):
        return False
    if t and _phase_tier(r) != t:
        return False
    return not (status and r.get("status") != status)


def _text_match(r: dict[str, Any], nq: str, pq: str) -> bool:
    """Whether a registry row matches a name substring and a structure-type key.

    Args:
        r (dict[str, Any]): Registry row.
        nq (str): Lower-case name substring ("" for none).
        pq (str): Normalised prototype key from ``struct_norm`` ("" for none).

    Returns:
        True when both filters match.
    """
    if nq and not any(nq in lab for lab in _labels_of(r)):
        return False
    if not pq:
        return True
    s = r.get("structure") or {}
    keys = {struct_norm(s.get(k)) for k in ("prototype", "strukturbericht", "pearson", "structure_type")}
    keys.add(struct_norm(str(s.get("structure_type") or "").removeprefix("st:")))
    return pq in keys


class PhasesTools:
    """Phase-registry queries: one phase, search over phases, and structure types.

    Args:
        core (OrchidCore): Shared data sources and response envelope.
    """

    def __init__(self, core: OrchidCore) -> None:
        """Keep a reference to the shared core."""
        self._core = core

    def _phase_index(self) -> dict[str, Any]:
        """The registry index (``phases/registry.json``).

        Returns:
            The parsed index.

        Raises:
            OrchidError: When the data source has no phase registry.
        """
        try:
            return self._core.data.read_json("phases/registry.json")
        except NotFound as exc:
            raise OrchidError("the phase registry (phases/registry.json) is not in this ORCHID data source") from exc

    def _phase_system(self, sid: str) -> dict[str, Any]:
        """Full registry file of one binary system.

        Args:
            sid (str): Binary system id.

        Returns:
            The parsed file.

        Raises:
            OrchidError: When the system has no registry file.
        """
        try:
            return self._core.data.read_json(f"phases/systems/{sid}.json")
        except NotFound as exc:
            raise OrchidError(f"no phase-registry file for {sid} (binary systems only)") from exc

    def _phase_rows(self, sid: str | None = None) -> list[dict[str, Any]]:
        """Registry rows, optionally of one system.

        Args:
            sid (str | None): System id to filter by, or None for all rows.

        Returns:
            The rows.
        """
        rows = self._phase_index().get("phases", [])
        return [r for r in rows if r.get("system") == sid] if sid else rows

    def get_phase(self, *, system: str, phase: str, fields: str) -> dict[str, Any]:
        """One phase of a binary system from the ORCHID phase registry (by id, namespaced name or plain label).

        Args:
            system (str): System as written by the user; a ``phase:<sys>:...`` id may supply it.
            phase (str): Registry id, namespaced name, or label such as ``"L12_FCC"``.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            The phase, or an ``ambiguous`` block listing the matches.

        Raises:
            OrchidError: When the phase is empty, not found, or has no registry data for the system.
        """
        fields = check_fields(fields)
        q = (phase or "").strip()
        if not q:
            raise OrchidError("phase is empty: give a registry id, a namespaced name or a label such as 'L12_FCC'")
        if q.lower().startswith("phase:") and (not system or str(system).lower().startswith("phase:")):
            parts = q.split(":")
            system = parts[1].upper() if len(parts) > 2 else system
        sid = self._core.sys_id(system)
        rows = self._phase_rows(sid)
        if not rows:
            raise OrchidError(f"no phases for {sid} in the phase registry (binary systems only; use search_phases)")
        hits = _find_rows(rows, q.lower())
        if not hits:
            raise OrchidError(
                f"no phase {phase!r} in {sid}; phases: " + ", ".join(sorted(str(r.get("preferred_name")) for r in rows))
            )
        if len(hits) > 1:
            return self._core.wrap(
                {
                    "query": {"system": sid, "phase": q},
                    "ambiguous": True,
                    "matches": [_phase_row_summary(r) for r in hits],
                    "hint": "call get_phase again with one of the ids",
                    "how_to_cite_phase_registry": phase_cite_text,
                }
            )
        row = hits[0]
        doc = self._phase_system(sid)
        e = next((x for x in doc.get("phases", []) if x.get("id") == row["id"]), None)
        if e is None:
            raise OrchidError(f"{row['id']} is in registry.json but not in phases/systems/{sid}.json")
        return self._core.wrap(
            {
                "query": {"system": sid, "phase": q},
                "fields": fields,
                "phase": _phase_summary(e) if fields == "summary" else _phase_full(e),
                "how_to_cite_phase_registry": phase_cite_text,
            }
        )

    def search_phases(
        self,
        *,
        elements: list[str] | None,
        name: str | None,
        prototype: str | None,
        tier: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        """Phases of the registry filtered by elements, name, prototype / Strukturbericht / Pearson, tier, status.

        Args:
            elements (list[str] | None): Phases of systems containing all of them.
            name (str | None): Substring of a phase name, TDB name, paper or corpus label.
            prototype (str | None): Prototype, Strukturbericht, Pearson symbol or structure-type id.
            tier (str | None): ``A``, ``B``, ``C``, ``liquid``, ``gas`` or ``none``.
            status (str | None): ``assigned``, ``needs_review`` or ``unresolved_no_candidates``.
            limit (int): Page size, between 1 and ``phases_page_max``.
            offset (int): Rows to skip (at least 0).

        Returns:
            The page of phase summaries, sorted by system and id, with paging fields and the tier legend.

        Raises:
            OrchidError: On an unknown element or tier.
        """
        want = wanted_elements(elements)
        t = (tier or "").strip()
        if t and t.lower() not in _tier_aliases:
            raise OrchidError("tier must be one of A, B, C, liquid, gas, none")
        t = _tier_aliases.get(t.lower()) if t else None
        nq = (name or "").strip().lower()
        pq = struct_norm(prototype) if prototype else ""
        rows = [
            r
            for r in self._phase_rows()
            if _structural_match(r, want, t, status) and _text_match(r, nq, pq)
        ]
        rows.sort(key=lambda r: (r.get("system") or "", r.get("id") or ""))
        lim, off = max(1, min(int(limit), phases_page_max)), max(0, int(offset))
        page = rows[off : off + lim]
        return self._core.wrap(
            {
                "query": {"elements": sorted(want), "name": name, "prototype": prototype, "tier": t, "status": status},
                "total": len(rows),
                "offset": off,
                "returned": len(page),
                "truncated": off + lim < len(rows),
                "phases": [_phase_row_summary(r) for r in page],
                "tiers": phase_tiers,
                "how_to_cite_phase_registry": phase_cite_text,
            }
        )

    def get_structure_type(self, *, query: str, limit: int = structure_type_limit) -> dict[str, Any]:
        """Structure types by id, prototype, Strukturbericht, Pearson symbol or space group.

        Args:
            query (str): Id, prototype, Strukturbericht, Pearson symbol or space group (symbol or number).
            limit (int): Maximum number of structure types returned (at least 1).

        Returns:
            The matching types, most registry phases first, each with example phase ids.

        Raises:
            OrchidError: When the query is empty, the file is missing, or nothing matches.
        """
        q = (query or "").strip()
        if not q:
            raise OrchidError("query is empty: give a structure-type id, prototype, Strukturbericht or Pearson symbol")
        try:
            st = self._core.data.read_json("phases/structure_types.json").get("structure_types", [])
        except NotFound as exc:
            raise OrchidError("phases/structure_types.json is not in this ORCHID data source") from exc
        hits = [t for t in st if struct_norm(q) in _structure_keys(t)]
        if not hits:
            raise OrchidError(f"no structure type matches {query!r} (try a prototype like 'AuCu3', 'L1_2' or 'cP4')")
        hits.sort(key=lambda t: (-(t.get("registry_phases") or 0), t.get("id") or ""))
        shown = hits[: max(1, limit)]
        ids = {t["id"] for t in shown}
        examples: dict[str, list[str]] = {}
        for r in self._phase_rows():
            sid = (r.get("structure") or {}).get("structure_type")
            if sid in ids and len(examples.setdefault(sid, [])) < phase_examples:
                examples[sid].append(r["id"])
        return self._core.wrap(
            {
                "query": q,
                "total": len(hits),
                "truncated": len(hits) > max(1, limit),
                "structure_types": [{**t, "example_phases": examples.get(t["id"], [])} for t in shown],
                "how_to_cite_phase_registry": phase_cite_text,
            }
        )


def _structure_keys(t: dict[str, Any]) -> set[str]:
    """All normalised keys under which a structure type can be looked up.

    Args:
        t (dict[str, Any]): Structure type entry.

    Returns:
        The non-empty keys.
    """
    sg = t.get("space_group") or {}
    keys = {struct_norm(t.get(k)) for k in ("id", "prototype", "strukturbericht", "pearson")}
    keys.add(struct_norm(str(t.get("id") or "").removeprefix("st:")))
    keys |= {struct_norm(x) for x in t.get("prototype_spellings") or []}
    keys |= {struct_norm(sg.get("symbol")), struct_norm(str(sg.get("number") or ""))}
    keys.discard("")
    return keys
