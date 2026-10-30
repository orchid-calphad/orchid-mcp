"""ORCHID bibliography: every paper the release knows, its roles and evidence, and typed relations between papers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .constants import evidence_classes, public_data_url
from .core import check_fields, norm_doi, wanted_elements
from .datasource import NotFound
from .errors import OrchidError

if TYPE_CHECKING:
    from .core import OrchidCore

bib_roles = (
    "corpus_assessment_read",
    "cited_original_source",
    "tdb_source_assessment",
    "tdb_compilation",
    "handbook_diagram_source",
    "discovery_lead",
    "context_source",
    "espei_reference",
)
bib_relations = ("cites", "used_in_assessment", "models_system_in_tdb", "reassesses", "handbook_reproduces", "cited_by_crossref")
bib_directions = ("out", "in", "both")
bib_used_default = 25  # data papers listed per assessment unless asked otherwise
papers_page_max = 500
relations_page_max = 1000
data_papers_max = 500
bib_cite_text = (
    "Cite the papers themselves when you use their data (doi / citation of each record), and the ORCHID "
    "bibliography (bibliography/CITATION.cff; Zenodo record 'bibliography'). Crossref metadata is CC0; ORCHID-derived "
    "fields (roles, evidence counts, relations) are CC BY 4.0."
)
relation_types = {
    "cites": "paper ORCHID read -> work printed as the source of its values (count = values)",
    "used_in_assessment": "original data paper -> paper/assessment that used it (inverse of cites)",
    "models_system_in_tdb": "paper -> TDB file whose header cites it",
    "reassesses": "later assessment -> earlier assessment of the same exact system it cites",
    "handbook_reproduces": "handbook diagram -> original evaluation",
    "cited_by_crossref": "src's publisher-deposited Crossref reference list contains dst",
}
_summary_evidence_keys = (
    "n_values",
    "n_values_accepted",
    "by_evidence_class",
    "by_property_group",
    "n_tables",
    "n_text_passages",
    "n_citing_papers",
)


def bib_summary_record(r: dict[str, Any]) -> dict[str, Any]:
    """Short form of a bibliography record: identity, roles, evidence counts and availability.

    Args:
        r (dict[str, Any]): Full bibliography record (``id``, ``key``, ``role_details``, ``evidence_summary``...).

    Returns:
        The summary; the first ten authors only, TDB file lists reduced to file, system, digest and bundling.
    """
    es = r.get("evidence_summary") or {}
    ev: dict[str, Any] = {}
    for k in ("reported", "cited_as_source"):
        if k in es:
            ev[k] = {x: es[k][x] for x in _summary_evidence_keys if x in es[k]}
    for k in ("espei_datasets", "phase_registry_phases"):
        if k in es:
            ev["n_" + k] = len(es[k])
    if "assessed_assemblage_records" in es:
        ev["assessed_assemblage_records"] = es["assessed_assemblage_records"]
    rd = {}
    for role, d in (r.get("role_details") or {}).items():
        d = dict(d)
        if "tdb_files" in d:
            d["tdb_files"] = [{k: t.get(k) for k in ("file", "system", "sha256", "bundled")} for t in d["tdb_files"]]
        rd[role] = d
    return {
        "id": r["id"],
        "key": r["key"],
        "doi": r.get("doi"),
        "citation": r.get("citation"),
        "title": r.get("title"),
        "authors": [a.get("family") or a.get("name") for a in r.get("authors") or []][:10],
        "year": r.get("year"),
        "container_title": r.get("container_title"),
        "type": r.get("type"),
        "systems": r.get("systems") or [],
        "roles": r.get("roles") or [],
        "role_details": rd,
        "evidence_summary": ev,
        "availability": r.get("availability"),
        "is_referenced_by_count": r.get("is_referenced_by_count"),
        "is_referenced_by_count_retrieved": r.get("is_referenced_by_count_retrieved"),
        "doi_match": r.get("doi_match"),
        "doi_check": r.get("doi_check"),
        "relation_counts": r.get("relation_counts"),
        "urls": r.get("urls"),
    }


def bib_row(r: dict[str, Any]) -> dict[str, Any]:
    """One row of a paper search: key fields of the bibliography index row and the record's data URL.

    Args:
        r (dict[str, Any]): Index row (with ``key``).

    Returns:
        The row fields plus ``data_url``.
    """
    fields = ("id", "key", "doi", "title", "year", "container", "authors", "roles", "systems", "values_reported", "values_cited")
    return {k: r.get(k) for k in fields} | {"data_url": f"{public_data_url}bibliography/papers/{r['key']}.json"}


def _structural_match(
    r: dict[str, Any],
    sid: str | None,
    want: set[str],
    role: str | None,
    prop: str,
    evidence_class: str | None,
) -> bool:
    """Whether an index row has the system, element, role, property and evidence class asked for.

    Args:
        r (dict[str, Any]): Index row.
        sid (str | None): Exact system id, or None.
        want (set[str]): Elements that a system of the paper must contain.
        role (str | None): Required role.
        prop (str): Upper-case property code ("" for none).
        evidence_class (str | None): Required evidence class.

    Returns:
        True when every given filter matches.
    """
    if sid and sid not in r["systems"]:
        return False
    if want and not any(want <= set(s.split("-")) for s in r["systems"]):
        return False
    if role and role not in r["roles"]:
        return False
    if prop and prop not in r["properties"]:
        return False
    return not (evidence_class and evidence_class not in r["evidence_classes"])


def _year_text_match(r: dict[str, Any], year_from: int | None, year_to: int | None, tq: str) -> bool:
    """Whether an index row lies in the year range and contains the text in title, authors or DOI.

    Args:
        r (dict[str, Any]): Index row.
        year_from (int | None): Earliest year, inclusive.
        year_to (int | None): Latest year, inclusive.
        tq (str): Normalised lower-case text ("" for none).

    Returns:
        True when the row passes both filters.
    """
    y = r.get("year")
    if year_from is not None and (y is None or y < int(year_from)):
        return False
    if year_to is not None and (y is None or y > int(year_to)):
        return False
    if not tq:
        return True
    hay = " ".join([str(r.get("title") or ""), " ".join(r.get("authors") or []), str(r.get("doi") or "")]).lower()
    return tq in re.sub(r"\s+", " ", hay)


class BibliographyTools:
    """Bibliography index, paper search, paper relations and assessment evidence.

    Args:
        core (OrchidCore): Shared data sources and response envelope.
    """

    def __init__(self, core: OrchidCore) -> None:
        """Keep a reference to the shared core."""
        self._core = core

    def bib_index(self) -> dict[str, Any]:
        """Bibliography index with its rows and an id-to-key lookup, built once from the columnar file.

        Returns:
            The index, extended in place with ``_rows`` (list of dicts) and ``_by`` (id or ``doi:`` key to paper key).

        Raises:
            OrchidError: When the release has no bibliography.
        """
        try:
            idx = self._core.data.read_json("bibliography/index.json")
        except NotFound as exc:
            raise OrchidError("the bibliography (bibliography/index.json) is not in this ORCHID data source") from exc
        if "_by" not in idx:
            cols = idx["columns"]
            rows = [dict(zip(cols, r, strict=False)) for r in idx["rows"]]
            by: dict[str, str] = {}
            for r in rows:
                by[r["id"]] = r["key"]
                if r.get("doi"):
                    by["doi:" + r["doi"]] = r["key"]
            idx["_rows"] = rows
            idx["_by"] = by
        return idx

    def bib_key(self, q: str) -> str | None:
        """Paper key for a DOI, a ``doi:`` / ``nodoi:`` id, or a ``p<16 hex>`` bibliography key.

        Args:
            q (str): The query.

        Returns:
            The key, or None when the bibliography has no such paper.

        Raises:
            OrchidError: When the query is empty.
        """
        idx = self.bib_index()
        q = (q or "").strip()
        if not q:
            raise OrchidError("give a DOI or a bibliography id")
        if re.fullmatch(r"p[0-9a-f]{16}", q):
            return q if any(r["key"] == q for r in idx["_rows"]) else None
        if q.startswith(("nodoi:", "doi:")) and q in idx["_by"]:
            return idx["_by"][q]
        return idx["_by"].get("doi:" + norm_doi(q.removeprefix("doi:")))

    def bib_paper(self, q: str, required: bool = True) -> dict[str, Any] | None:  # noqa: FBT001, FBT002 (public flag)
        """Full bibliography record of a paper.

        Args:
            q (str): DOI or bibliography id.
            required (bool): Raise when the paper is unknown; otherwise return None.

        Returns:
            The record (``paper``, ``relations``), or None when not found and not required.

        Raises:
            OrchidError: When not found and ``required``, or when the query is empty.
        """
        try:
            key = self.bib_key(q)
        except OrchidError:
            if required:
                raise
            return None
        if key is None:
            if required:
                raise OrchidError(f"paper {q!r} not in the ORCHID bibliography")
            return None
        return self._core.data.read_json(f"bibliography/papers/{key}.json")

    def bib_view(self, doc: dict[str, Any], fields: str) -> dict[str, Any]:
        """Bibliography block of a paper response: the full record with relations, or its summary.

        Args:
            doc (dict[str, Any]): Full bibliography record.
            fields (str): ``"full"`` for the record and its relations; anything else for the summary.

        Returns:
            The block.
        """
        r = doc["paper"]
        if fields == "full":
            return {"record": r, "relations": doc.get("relations") or []}
        return bib_summary_record(r)

    def search_papers(
        self,
        *,
        system: str | None,
        elements: list[str] | None,
        role: str | None,
        property: str | None,  # noqa: A002 (public parameter name)
        evidence_class: str | None,
        year_from: int | None,
        year_to: int | None,
        text: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        """Papers of the bibliography filtered by exact system, elements, role, property code, evidence class,
        year range and title/author/DOI text.

        Args:
            system (str | None): Exact system id as written by the user.
            elements (list[str] | None): Papers with a system containing all of them.
            role (str | None): One of ``bib_roles``.
            property (str | None): Property code of values reported in or citing the paper.
            evidence_class (str | None): One of the first four ``evidence_classes``.
            year_from (int | None): Earliest year, inclusive.
            year_to (int | None): Latest year, inclusive.
            text (str | None): Case-insensitive substring of title, authors or DOI (whitespace collapsed).
            limit (int): Page size, between 1 and ``papers_page_max``.
            offset (int): Rows to skip (at least 0).

        Returns:
            The page of papers sorted by year then id, with paging fields and the role list.

        Raises:
            OrchidError: On an unknown role or evidence class, or an invalid system or element.
        """
        sid = self._core.sys_id(system) if system else None
        want = wanted_elements(elements)
        if role and role not in bib_roles:
            raise OrchidError(f"role must be one of {bib_roles}")
        if evidence_class and evidence_class not in evidence_classes[:4]:
            raise OrchidError(f"evidence_class must be one of {evidence_classes[:4]}")
        prop = (property or "").strip().upper()
        tq = re.sub(r"\s+", " ", (text or "").strip().lower())
        rows = [
            r
            for r in self.bib_index()["_rows"]
            if _structural_match(r, sid, want, role, prop, evidence_class)
            and _year_text_match(r, year_from, year_to, tq)
        ]
        rows.sort(key=lambda r: (r.get("year") or 0, r["id"]))
        lim, off = max(1, min(int(limit), papers_page_max)), max(0, int(offset))
        page = rows[off : off + lim]
        return self._core.wrap(
            {
                "query": {
                    "system": sid,
                    "elements": sorted(want),
                    "role": role,
                    "property": prop or None,
                    "evidence_class": evidence_class,
                    "year_from": year_from,
                    "year_to": year_to,
                    "text": text,
                },
                "total": len(rows),
                "offset": off,
                "returned": len(page),
                "truncated": off + lim < len(rows),
                "papers": [bib_row(r) for r in page],
                "roles": list(bib_roles),
                "how_to_cite_bibliography": bib_cite_text,
            }
        )

    def get_paper_relations(
        self, *, doi: str, relation: str | None, direction: str, limit: int, offset: int
    ) -> dict[str, Any]:
        """Typed relations of one paper (edges with provenance).

        Args:
            doi (str): DOI or bibliography id of the paper.
            relation (str | None): One of ``bib_relations``; None keeps all types.
            direction (str): ``"out"``, ``"in"`` or ``"both"``.
            limit (int): Page size, between 1 and ``relations_page_max``.
            offset (int): Rows to skip (at least 0).

        Returns:
            The page of relations sorted by type, direction, count and other paper, with counts by type.

        Raises:
            OrchidError: On an unknown relation or direction, or when the paper is not in the bibliography.
        """
        if relation and relation not in bib_relations:
            raise OrchidError(f"relation must be one of {bib_relations}")
        d = (direction or "both").strip().lower()
        if d not in bib_directions:
            raise OrchidError(f"direction must be one of {bib_directions}")
        doc = self.bib_paper(doi)
        rels = [
            x
            for x in doc.get("relations") or []
            if (not relation or x["type"] == relation) and (d == "both" or x["direction"] == d)
        ]
        rels.sort(key=lambda x: (x["type"], x["direction"], -(x.get("count") or 0), x["other"]["id"]))
        lim, off = max(1, min(int(limit), relations_page_max)), max(0, int(offset))
        page = rels[off : off + lim]
        by_type: dict[str, dict[str, int]] = {}
        for x in rels:
            by_type.setdefault(x["type"], {"out": 0, "in": 0})[x["direction"]] += 1
        return self._core.wrap(
            {
                "paper": bib_summary_record(doc["paper"]) | {"evidence_summary": None, "role_details": None},
                "query": {"relation": relation, "direction": d},
                "total": len(rels),
                "offset": off,
                "returned": len(page),
                "truncated": off + lim < len(rels),
                "counts_by_type": by_type,
                "relations": page,
                "relation_types": relation_types,
                "how_to_cite_bibliography": bib_cite_text,
            }
        )

    def get_assessment_evidence(self, *, system: str, max_data_papers: int) -> dict[str, Any]:
        """Assessments of an exact system with the original data papers they used, counts, and reassessment links.

        Args:
            system (str): System as written by the user.
            max_data_papers (int): Cap on the data-paper list per assessment (between 0 and ``data_papers_max``).

        Returns:
            The assessments with their data papers (and a truncation flag), the reassessment chain and role counts.

        Raises:
            OrchidError: When the system has no papers in the bibliography.
        """
        sid = self._core.sys_id(system)
        self.bib_index()
        try:
            doc = self._core.data.read_json(f"bibliography/systems/{sid}.json")
        except NotFound as exc:
            raise OrchidError(f"no papers for {sid} in the ORCHID bibliography") from exc
        mx = max(0, min(int(max_data_papers), data_papers_max))
        assessments = []
        chain = []
        for a in doc.get("assessments") or []:
            used = a.get("data_papers_used") or []
            assessments.append(
                {
                    **{k: v for k, v in a.items() if k != "data_papers_used"},
                    "data_papers_used": used[:mx],
                    "data_papers_truncated": len(used) > mx,
                    "data_url": f"{public_data_url}bibliography/papers/{a['key']}.json",
                }
            )
            for earlier in a.get("reassesses") or []:
                chain.append({"later": a["id"], "earlier": earlier, "method": "citation+same-system"})
        return self._core.wrap(
            {
                "system": sid,
                "n_assessments": len(assessments),
                "assessments": assessments,
                "reassessment_links": chain,
                "papers_by_role": {k: len(v) for k, v in (doc.get("papers_by_role") or {}).items()},
                "data_url": f"{public_data_url}bibliography/systems/{sid}.json",
                "static_page_url": f"{public_data_url}bibliography/pages/systems/{sid}.html",
                "note": "Assessments = papers behind a TDB file of this system or read by ORCHID; data papers used = works "
                "printed as the source of the values ORCHID extracted from the assessment (cites edges).",
                "how_to_cite_bibliography": bib_cite_text,
            }
        )
