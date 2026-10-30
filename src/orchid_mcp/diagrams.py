"""Calculated binary phase diagrams (one per SGTE assessment) and their invariant reactions."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .constants import public_data_url, public_site_data_url, site_url
from .core import check_fields, norm_doi
from .errors import OrchidError
from .summaries import summarize_record, to_float

if TYPE_CHECKING:
    from .core import OrchidCore

diagram_file_re = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")
matching_rule = (
    "literature_matches: literature records whose mapped CALPHAD model phases equal the calculated "
    "invariant's phase set exactly (unmapped phases never match); dT = literature - calculated."
)
invariant_match_summary = 3  # literature matches kept per invariant in summary mode
invariant_match_max = 5  # literature matches kept per invariant in full mode
diagram_note = "Each assessment is a separate CALPHAD model; results are never averaged across assessments."
_compact_grid_drop = ("rows",)


def grid_phase(doc: dict[str, Any], x: float, T: float) -> dict[str, Any]:  # noqa: N803 (T: kelvin, public name)
    """Stable phases at the nearest node of a diagram's stored equilibrium grid.

    Args:
        doc (dict[str, Any]): Diagram document with a ``grid`` block (``x_step`` in mole fraction, ``T0`` and ``dT``
            in kelvin, ``rows`` run-length encoded per temperature step).
        x (float): Mole fraction of the diagram's ``x_component``, in [0, 1].
        T (float): Temperature in kelvin.

    Returns:
        The node's x and T, its stable phases and label, and a note on the grid resolution; or an ``error`` key.
    """
    g = doc.get("grid") or {}
    rows, labels = g.get("rows") or [], g.get("labels") or []
    if not rows:
        return {"error": "no stored equilibrium grid for this diagram"}
    xs, T0, dT = float(g["x_step"]), float(g["T0"]), float(g["dT"])
    npts = int(g.get("x_points") or round(1 / xs) + 1)
    if not (0.0 <= x <= 1.0):
        return {"error": "x must be a mole fraction in [0, 1]"}
    i = round((T - T0) / dT)
    if i < 0 or i >= len(rows):
        return {"error": f"T outside the stored grid ({T0}..{T0 + dT * (len(rows) - 1)} K)"}
    j = min(npts - 1, max(0, round(x / xs)))
    label = _grid_label(rows[i], j, labels)
    return {
        "x_requested": x,
        "T_requested_K": T,
        "grid_x": round(j * xs, 6),
        "grid_T_K": T0 + i * dT,
        "x_component": doc.get("x_component"),
        "stable_phases": label.split("+") if label else None,
        "label": label,
        "note": "nearest node of the stored pycalphad equilibrium grid (" + str(g.get("note", "")) + "); "
        f"resolution dx={xs}, dT={dT} K. Calculated (evidence class: model), not measured.",
    }


def _grid_label(row: list[list[int]], j: int, labels: list[str]) -> str | None:
    """Phase-set label of grid column ``j`` in one temperature row of run-length pairs ``[label index, count]``.

    Args:
        row (list[list[int]]): Run-length pairs of one temperature step.
        j (int): Grid column index.
        labels (list[str]): Phase-set labels referenced by the pairs.

    Returns:
        The label covering column ``j``, or None when no run covers it.
    """
    col = 0
    for li, count in row:
        if col <= j < col + count:
            return labels[li] if 0 <= li < len(labels) else None
        col += count
    return None


class DiagramsTools:
    """Phase diagrams of a system and the invariant reactions of its assessments.

    Args:
        core (OrchidCore): Shared data sources and response envelope.
    """

    def __init__(self, core: OrchidCore) -> None:
        """Keep a reference to the shared core."""
        self._core = core

    def _assessments(self, sid: str) -> list[dict[str, Any]]:
        """Diagram index rows of one system.

        Args:
            sid (str): Normalised system id.

        Returns:
            A copy of the rows (empty when the system has none).
        """
        return list(self._core.pd_index().get("systems", {}).get(sid, []))

    def _pick(self, sid: str, assessment: str | None) -> list[dict[str, Any]]:
        """Diagram rows of a system, optionally narrowed to one assessment by TDB name, file stem or DOI.

        Args:
            sid (str): Normalised system id.
            assessment (str | None): TDB file name (with or without extension) or DOI; None selects all.

        Returns:
            The matching rows.

        Raises:
            OrchidError: When a given assessment does not match any row.
        """
        rows = self._assessments(sid)
        if not assessment:
            return rows
        a = assessment.strip().lower()
        a_doi = norm_doi(assessment)
        hit = [r for r in rows if self._names_match(r, a) or (r.get("doi") and norm_doi(r["doi"]) == a_doi)]
        if not hit:
            raise OrchidError(f"assessment {assessment!r} not found for {sid}; available: {[r.get('tdb') for r in rows]}")
        return hit

    @staticmethod
    def _names_match(row: dict[str, Any], a: str) -> bool:
        """Whether a lower-case query equals the row's TDB or file name, with or without its extension.

        Args:
            row (dict[str, Any]): Diagram index row.
            a (str): Lower-case query.

        Returns:
            True on a match.
        """
        names = {
            (row.get("file") or "").lower(),
            (row.get("tdb") or "").lower(),
            (row.get("tdb") or "").lower().rsplit(".", 1)[0],
            (row.get("file") or "").lower().rsplit(".", 1)[0],
        }
        return a in names

    def _diagram(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Full diagram document of an index row.

        Args:
            entry (dict[str, Any]): Diagram index row with a ``file`` name.

        Returns:
            The parsed diagram document.

        Raises:
            OrchidError: When the file name is not a plain file name.
        """
        f = entry.get("file") or ""
        if not diagram_file_re.match(f):
            raise OrchidError(f"bad diagram file name {f!r}")
        return self._core.data.read_json(f"phase-diagrams/sgte/{f}")

    def _diagram_summary(self, entry: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
        """Assessment summary of one diagram: provenance, phases, invariants, region boxes and links.

        Args:
            entry (dict[str, Any]): Diagram index row (with ``system`` set by the caller).
            doc (dict[str, Any]): Full diagram document.

        Returns:
            The summary; ``regions_summary`` gives per region the boundary phases, point count and x and T ranges.
        """
        src = doc.get("original_source") or {}
        sid = "-".join(doc.get("components") or entry.get("system", "").split("-"))
        tdb = doc.get("tdb") or entry.get("tdb")
        pd_index = self._core.pd_index()
        return {
            "evidence_class": "model",
            "tdb": tdb,
            "tdb_sha256": doc.get("sha256") or entry.get("sha256"),
            "tdb_download_url": f"{public_site_data_url}databases/sgte-binary/{tdb}",
            "assessment_citation": src.get("citation") or entry.get("citation"),
            "assessment_doi": src.get("doi") or entry.get("doi"),
            "assessment_url": src.get("url"),
            "collection": pd_index.get("source"),
            "engine": doc.get("engine") or pd_index.get("engine"),
            "status": doc.get("status"),
            "components": doc.get("components"),
            "x_component": doc.get("x_component"),
            "T_range_K": doc.get("T_range"),
            "phases": doc.get("phases"),
            "invariants": doc.get("invariants", []),
            "regions_summary": _region_boxes(doc.get("regions", [])),
            "grid": {k: v for k, v in (doc.get("grid") or {}).items() if k not in _compact_grid_drop},
            "data_url": f"{public_data_url}phase-diagrams/sgte/{entry.get('file')}",
            "viewer_url": f"{site_url}phase-diagrams/#{sid}/{entry.get('file')}",
        }

    def get_phase_diagram(
        self, *, system: str, assessment: str | None, x: float | None, T: float | None, fields: str  # noqa: N803
    ) -> dict[str, Any]:
        """Calculated binary phase diagram(s) of a system; optionally the stable phases at (x, T).

        Args:
            system (str): System as written by the user.
            assessment (str | None): TDB file name or DOI; None returns every assessment.
            x (float | None): Mole fraction of the diagram's ``x_component``; needed with ``T``.
            T (float | None): Temperature in kelvin; needed with ``x``.
            fields (str): ``"summary"`` omits the region boxes and grid metadata; ``"full"`` keeps them.

        Returns:
            One summary per assessment (never averaged), with ``phase_at`` when x and T are given.
        """
        fields = check_fields(fields)
        sid = self._core.sys_id(system)
        entries = self._pick(sid, assessment)
        if not entries:
            raise OrchidError(f"no calculated phase diagram for {sid}")
        out = []
        for e in entries:
            doc = self._diagram(e)
            s = self._diagram_summary({**e, "system": sid}, doc)
            if x is not None and T is not None:
                s["phase_at"] = grid_phase(doc, float(x), float(T))
            if fields == "summary":
                for k in ("regions_summary", "grid", "collection", "assessment_url"):
                    s.pop(k, None)
            out.append(s)
        return self._core.wrap(
            {
                "system": sid,
                "fields": fields,
                "n_assessments": len(out),
                "assessments": out,
                "note": diagram_note,
            }
        )

    def get_invariant_reactions(
        self,
        *,
        system: str,
        source: str,
        assessment: str | None,
        accepted_only: bool,
        limit: int,
        offset: int,
        fields: str,
    ) -> dict[str, Any]:
        """Literature invariant reactions and calculated invariants (per assessment), side by side.

        Args:
            system (str): System as written by the user.
            source (str): ``"literature"``, ``"calculated"`` or ``"both"``.
            assessment (str | None): Restrict the calculated part to one assessment (TDB name or DOI).
            accepted_only (bool): Keep only accepted literature records.
            limit (int): Page size of the literature list.
            offset (int): Rows to skip in the literature list.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            Literature page with its contexts, and per assessment the calculated invariants with literature matches
            (phase-set equality, dT = literature - calculated).

        Raises:
            OrchidError: On an invalid ``source``, unknown assessment, or when no invariant data exist at all.
        """
        fields = check_fields(fields)
        if source not in ("literature", "calculated", "both"):
            raise OrchidError("source must be 'literature', 'calculated' or 'both'")
        sid = self._core.sys_id(system)
        result: dict[str, Any] = {"system": sid, "source": source}
        lit_recs: list[dict[str, Any]] = []
        if source in ("literature", "both"):
            lit_recs = self._literature_part(result, sid, accepted_only, limit, offset, fields)
        if source in ("calculated", "both"):
            result["calculated"] = self._calculated_part(sid, assessment, lit_recs, fields)
            result["matching_rule"] = matching_rule
        if source in ("literature", "both") and not lit_recs and not result.get("calculated"):
            raise OrchidError(f"no invariant data for {sid}")
        return self._core.wrap(result)

    def _literature_part(
        self, result: dict[str, Any], sid: str, accepted_only: bool, limit: int, offset: int, fields: str
    ) -> list[dict[str, Any]]:
        """Fill the literature block of an invariant-reaction result and return all matching records.

        Args:
            result (dict[str, Any]): Result dict, updated in place.
            sid (str): Normalised system id.
            accepted_only (bool): Keep only accepted records.
            limit (int): Page size.
            offset (int): Rows to skip.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            All invariant records of the system that passed the filter (expanded, sorted by value, descending).
        """
        lit_recs: list[dict[str, Any]] = []
        page: list[dict[str, Any]] = []
        doc = self._core.system_doc(sid)
        if doc is not None:
            for rec in doc["records"]:
                if accepted_only and not self._core.accepted(rec):
                    continue
                if rec.get("observation_type") == "invariant_reaction" or rec.get("property_group") == "invariant reaction":
                    lit_recs.append(self._core.expand(rec, doc))
            lit_recs.sort(key=lambda r: -(to_float(r.get("value")) or 0))
            page = lit_recs[max(0, offset) : max(0, offset) + max(1, limit)]
            result["contexts"] = self._core.contexts_for(page, doc)
        result["fields"] = fields
        result["literature_total"] = len(lit_recs)
        result["literature_offset"] = offset
        result["literature_truncated"] = offset + len(page) < len(lit_recs)
        result["literature"] = [summarize_record(r) for r in page] if fields == "summary" else page
        return lit_recs

    def _calculated_part(
        self, sid: str, assessment: str | None, lit_recs: list[dict[str, Any]], fields: str
    ) -> list[dict[str, Any]]:
        """Calculated invariants of each selected assessment, each matched against the literature records.

        Args:
            sid (str): Normalised system id.
            assessment (str | None): Restrict to one assessment, or None for all.
            lit_recs (list[dict[str, Any]]): All literature invariant records of the system.
            fields (str): ``"summary"`` keeps the three best literature matches per invariant.

        Returns:
            One block per assessment with its invariants.
        """
        calc = []
        for e in self._pick(sid, assessment):
            doc = self._diagram(e)
            s = self._diagram_summary({**e, "system": sid}, doc)
            invs = self._invariants(s["invariants"], lit_recs, fields)
            calc.append(
                {
                    k: s[k]
                    for k in ("tdb", "tdb_sha256", "assessment_citation", "assessment_doi", "engine", "viewer_url", "data_url")
                }
                | {"evidence_class": "model", "invariants": invs, "n_invariants_raw": len(s["invariants"])}
            )
        return calc

    def _invariants(self, raw: list[dict[str, Any]], lit_recs: list[dict[str, Any]], fields: str) -> list[dict[str, Any]]:
        """Calculated invariants with duplicates removed and, when literature exists, their literature matches.

        Args:
            raw (list[dict[str, Any]]): The diagram's invariant entries.
            lit_recs (list[dict[str, Any]]): Literature invariant records of the system.
            fields (str): Summary mode shortens each match.

        Returns:
            The unique invariants, each copied and extended with ``literature_matches`` when ``lit_recs`` is non-empty.
        """
        invs = []
        seen: set[tuple[Any, ...]] = set()
        for inv in raw:
            key = (round(float(inv.get("T", 0)), 1), tuple(sorted(p.get("phase") or "" for p in inv.get("points", []))))
            if key in seen:  # the engine reports one entry per node contact
                continue
            seen.add(key)
            item = dict(inv)
            if lit_recs:
                phases = {re.sub(r"#\d+$", "", p.get("phase") or "") for p in inv.get("points", [])}
                item["literature_matches"] = _literature_matches(inv, phases, lit_recs, fields)
            invs.append(item)
        return invs


def _literature_matches(
    inv: dict[str, Any], phases: set[str], lit_recs: list[dict[str, Any]], fields: str
) -> list[dict[str, Any]]:
    """Literature invariant records whose mapped phase set equals the calculated invariant's, closest in T first.

    Args:
        inv (dict[str, Any]): Calculated invariant (``T`` in kelvin).
        phases (set[str]): Calculated phase names with their ``#n`` suffixes removed.
        lit_recs (list[dict[str, Any]]): Literature invariant records (expanded).
        fields (str): Summary mode keeps only id, evidence class, T and dT of the best matches.

    Returns:
        At most ``invariant_match_max`` matches (``invariant_match_summary`` in summary mode).
    """
    best = []
    for r in lit_recs:
        mp = {p.get("model_phase") for p in r.get("phases") or []}
        if None in mp or {re.sub(r"#\d+$", "", m) for m in mp} != phases:
            continue
        v = to_float(r.get("value"))
        if v is None:
            continue
        best.append(
            {
                "id": r["id"],
                "evidence_class": r.get("evidence_class"),
                "T_K": v,
                "dT_K": round(v - inv["T"], 2),
                "doi": (r.get("source") or {}).get("doi"),
                "page": (r.get("source") or {}).get("page"),
            }
        )
    best.sort(key=lambda b: abs(b["dT_K"]))
    if fields == "summary":
        best = [{k: b[k] for k in ("id", "evidence_class", "T_K", "dT_K")} for b in best[:invariant_match_summary]]
    return best[:invariant_match_max]


def _region_boxes(regions: list[Any]) -> list[dict[str, Any]]:
    """Boundary phases, point count and x and T ranges of each region of a diagram.

    Args:
        regions (list[Any]): Region entries; each is a boundary segment or a list of segments.

    Returns:
        One dict per region.
    """
    out = []
    for reg in regions:
        segs = reg if isinstance(reg, list) else [reg]
        xs = [v for sg in segs for v in sg.get("x", []) if v is not None]
        ts = [v for sg in segs for v in sg.get("T", []) if v is not None]
        out.append(
            {
                "boundaries": [sg.get("phase") for sg in segs],
                "points": len(xs),
                "x_range": [min(xs), max(xs)] if xs else None,
                "T_range_K": [min(ts), max(ts)] if ts else None,
            }
        )
    return out
