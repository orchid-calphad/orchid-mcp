"""Chemical-system search and the literature values of one system."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .chem import pretty_system
from .constants import evidence_classes, fields_note, site_url
from .core import check_fields, wanted_elements
from .errors import OrchidError
from .summaries import summarize_record

if TYPE_CHECKING:
    from .core import OrchidCore

compact_drop = {"readers", "reader_tier", "tool_release", "value_stored", "unit_stored", "link_rule", "pid", "elements"}
system_values_page_max = 2000  # largest page of get_system_data


class SystemsTools:
    """Search over chemical systems and filtered access to their literature records.

    Args:
        core (OrchidCore): Shared data sources and response envelope.
    """

    def __init__(self, core: OrchidCore) -> None:
        """Keep a reference to the shared core."""
        self._core = core

    def search_systems(
        self, *, elements: list[str] | None, text: str | None, exact: bool, limit: int, offset: int, fields: str
    ) -> dict[str, Any]:
        """Find chemical systems with literature data and/or calculated phase diagrams.

        Args:
            elements (list[str] | None): Systems containing all of them (``exact``: exactly them).
            text (str | None): Case-insensitive match on the system name or on titles and citations of papers
                reporting that system.
            exact (bool): Require the system's element set to equal ``elements``.
            limit (int): Page size (at least 1).
            offset (int): Rows to skip (negative values count as 0).
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            The page of systems with counts by evidence class and calculated assessments, plus paging fields.
        """
        fields = check_fields(fields)
        want = wanted_elements(elements)
        lit = self._core.lit_index().get("systems", {})
        pd = self._core.pd_index().get("systems", {})
        text_hits = self._text_hits(text, set(lit) | set(pd)) if text else None
        rows = self._rows(want, exact, lit, pd, text_hits)
        rows.sort(key=lambda r: (len(r["elements"]), -r["literature_accepted"], -r["calculated_phase_diagrams"], r["system"]))
        page = rows[max(0, offset) : max(0, offset) + max(1, limit)]
        if fields == "summary":
            page = [self._summary_row(r) for r in page]
        return self._core.wrap(
            {
                "query": {"elements": sorted(want), "text": text, "exact": exact},
                "fields": fields,
                "total": len(rows),
                "offset": offset,
                "returned": len(page),
                "truncated": offset + len(page) < len(rows),
                "systems": page,
            }
        )

    def _text_hits(self, text: str, sids: set[str]) -> set[str]:
        """Systems matching a free-text query by name, or by a title or citation of a paper that reports them.

        Args:
            text (str): Query text (case-insensitive, substring match).
            sids (set[str]): System ids to test by name.

        Returns:
            The matching system ids.
        """
        t = text.strip().lower()
        hits: set[str] = set()
        for sid in sids:
            if t in sid.lower() or t in pretty_system(sid).lower() or t.replace(" ", "") in sid.lower().replace("-", ""):
                hits.add(sid)
        for p in self._core.papers().values():
            blob = f"{p.get('title') or ''} {p.get('citation') or ''}".lower()
            if t in blob:
                hits.update(p.get("systems") or [])
        return hits

    @staticmethod
    def _rows(
        want: set[str],
        exact: bool,
        lit: dict[str, Any],
        pd: dict[str, Any],
        text_hits: set[str] | None,
    ) -> list[dict[str, Any]]:
        """One row per system that passes the element and text filters.

        Args:
            want (set[str]): Required element tokens (empty: no element filter).
            exact (bool): Compare element sets for equality instead of inclusion.
            lit (dict[str, Any]): Literature index of systems.
            pd (dict[str, Any]): Phase-diagram index of systems.
            text_hits (set[str] | None): Allowed system ids from the text search (None: no text filter).

        Returns:
            Unsorted system rows with their counts, assessments and links.
        """
        rows = []
        for sid in sorted(set(lit) | set(pd)):
            els = set(sid.split("-"))
            if want and (els != want if exact else not want <= els):
                continue
            if text_hits is not None and sid not in text_hits:
                continue
            li = lit.get(sid, {})
            calc = pd.get(sid, [])
            rows.append(
                {
                    "system": sid,
                    "elements": sorted(els),
                    "literature_values": li.get("n", 0),
                    "literature_accepted": li.get("accepted", 0),
                    "by_evidence_class": li.get("by_evidence_class", {}),
                    "by_property_group": li.get("by_property_group", {}),
                    "calculated_phase_diagrams": len(calc),
                    "assessments": [{"tdb": a.get("tdb"), "citation": a.get("citation"), "doi": a.get("doi")} for a in calc],
                    "dft_hulls": len(els) == 2,
                    "explorer_url": f"{site_url}data-explorer/#{sid}" if li else None,
                    "phase_diagram_url": f"{site_url}phase-diagrams/#{sid}" if calc else None,
                }
            )
        return rows

    @staticmethod
    def _summary_row(r: dict[str, Any]) -> dict[str, Any]:
        """Short form of a system row (no links, no assessment citations).

        Args:
            r (dict[str, Any]): Full system row from ``_rows``.

        Returns:
            System, accepted count, counts by evidence class, calculated count and assessment TDB names.
        """
        return {
            "system": r["system"],
            "literature_accepted": r["literature_accepted"],
            "by_evidence_class": r["by_evidence_class"],
            "calculated_phase_diagrams": r["calculated_phase_diagrams"],
            "assessments": [a["tdb"] for a in r["assessments"]],
        }

    def get_system_data(
        self,
        *,
        system: str,
        property_group: str | None,
        evidence_class: str | None,
        accepted_only: bool,
        limit: int,
        offset: int,
        property: str | None,  # noqa: A002 (public parameter name)
        compact: bool,
        fields: str,
    ) -> dict[str, Any]:
        """Literature values for one system, filtered and paged.

        Args:
            system (str): System as written by the user (e.g. ``"Al-Ni"``).
            property_group (str | None): Case-insensitive substring of the record's property group.
            evidence_class (str | None): One of ``evidence_classes``.
            accepted_only (bool): Keep only records that passed extraction acceptance.
            limit (int): Page size, capped at ``system_values_page_max``.
            offset (int): Rows to skip.
            property (str | None): Exact property code (case-insensitive), e.g. ``"HM_FORM"``.
            compact (bool): In full mode, drop the extraction-pipeline bookkeeping keys.
            fields (str): ``"summary"`` (short form) or ``"full"`` (expanded provenance).

        Returns:
            The page of values with contexts, counts by property group and the explorer link.

        Raises:
            OrchidError: When the system has no literature, the evidence class is unknown, or an argument is invalid.
        """
        fields = check_fields(fields)
        sid = self._core.sys_id(system)
        doc = self._core.system_doc(sid)
        if doc is None:
            raise OrchidError(f"no literature data for system {sid}; try search_systems")
        if evidence_class and evidence_class not in evidence_classes:
            raise OrchidError(f"evidence_class must be one of {evidence_classes}")
        pg = property_group.strip().lower() if property_group else None
        prop = property.strip().upper() if property else None
        sel = [r for r in doc["records"] if self._selected(r, accepted_only, evidence_class, pg, prop)]
        page = sel[max(0, offset) : max(0, offset) + max(1, min(limit, system_values_page_max))]
        values = [self._core.expand(r, doc) for r in page]
        contexts = self._core.contexts_for(values, doc)
        if fields == "summary":
            values = [summarize_record(r) for r in values]
        elif compact:
            values = [{k: v for k, v in r.items() if k not in compact_drop} for r in values]
        groups: dict[str, int] = {}
        for r in sel:
            groups[r.get("property_group") or "?"] = groups.get(r.get("property_group") or "?", 0) + 1
        return self._core.wrap(
            {
                "system": sid,
                "filters": {
                    "property_group": property_group,
                    "property": property,
                    "evidence_class": evidence_class,
                    "accepted_only": accepted_only,
                },
                "fields": fields,
                "total": len(sel),
                "total_matching": len(sel),
                "returned": len(values),
                "offset": offset,
                "truncated": offset + len(values) < len(sel),
                "next_offset": offset + len(values) if offset + len(values) < len(sel) else None,
                "matching_by_property_group": groups,
                "values": values,
                "contexts": contexts,
                "explorer_url": f"{site_url}data-explorer/#{sid}",
                **({"fields_note": fields_note} if fields == "summary" else {}),
            }
        )

    def _selected(
        self,
        rec: dict[str, Any],
        accepted_only: bool,
        evidence_class: str | None,
        pg: str | None,
        prop: str | None,
    ) -> bool:
        """Whether a stored record passes the filters of ``get_system_data``.

        Args:
            rec (dict[str, Any]): Stored record.
            accepted_only (bool): Require extraction acceptance.
            evidence_class (str | None): Required evidence class.
            pg (str | None): Lower-case substring of the property group.
            prop (str | None): Upper-case property code.

        Returns:
            True when the record is kept.
        """
        if accepted_only and not self._core.accepted(rec):
            return False
        if evidence_class and rec.get("evidence_class") != evidence_class:
            return False
        if pg and pg not in (rec.get("property_group") or "").lower():
            return False
        return not (prop and (rec.get("property") or "").upper() != prop)
