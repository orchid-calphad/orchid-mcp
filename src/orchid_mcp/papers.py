"""Paper records: the values ORCHID reports from a paper, the papers that cite it, and its experimental context."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .bibliography import bib_cite_text
from .core import check_fields, norm_doi
from .datasource import NotFound
from .errors import OrchidError

if TYPE_CHECKING:
    from .bibliography import BibliographyTools
    from .core import OrchidCore


class PapersTools:
    """Paper lookup by DOI or ORCHID id, combining the literature and the bibliography.

    Args:
        core (OrchidCore): Shared data sources and response envelope.
        bibliography (BibliographyTools): Bibliography queries used for the bibliography block.
    """

    def __init__(self, core: OrchidCore, bibliography: BibliographyTools) -> None:
        """Keep references to the shared core and to the bibliography tools."""
        self._core = core
        self._bib = bibliography

    def get_paper(self, *, doi: str, fields: str) -> dict[str, Any]:
        """Bibliographic record of a paper (by DOI or ORCHID paper id), its systems, values and contexts, and its
        bibliography entry.

        Args:
            doi (str): DOI, or an ORCHID id such as ``doi:...``, ``nodoi:...``, ``ref:...`` or a bibliography key.
            fields (str): ``"summary"`` or ``"full"`` (full adds relations and the complete bibliography record).

        Returns:
            The paper record with the values it reports, or a bibliography-only block when ORCHID has no literature
            values for it.

        Raises:
            OrchidError: When the paper is in neither the literature nor the bibliography.
        """
        fields = check_fields(fields)
        try:
            papers = self._core.papers()
        except NotFound:  # a data source with the bibliography only
            papers = {}
        q = doi.strip()
        pid = q if q.split(":", 1)[0] in ("doi", "sha256", "ref", "unresolved") and q in papers else f"doi:{norm_doi(q)}"
        bib = self._bib.bib_paper(q, required=False)
        if pid not in papers:
            if bib is None:
                raise OrchidError(f"paper {doi!r} not in ORCHID (id tried: {pid})")
            return self._core.wrap(
                {
                    "paper": None,
                    "bibliography": self._bib.bib_view(bib, fields),
                    "note": "Known to the ORCHID bibliography only (no literature values read from it).",
                    "how_to_cite_bibliography": bib_cite_text,
                }
            )
        rec = papers[pid]
        reported, cited = self._reported(rec, pid)
        ctx = self._core.data.read_json("literature/contexts.json").get(pid)
        out: dict[str, Any] = {
            "paper": rec,
            "systems": rec.get("systems") or [],
            "n_values_reported": sum(v["values"] for v in reported.values()),
            "values_by_system": reported,
            "n_values_citing_this_as_source_in_its_systems": cited,
            "experimental_context": ctx,
            "n_context_items": len((ctx or {}).get("items", [])),
            "note": "Bibliographic record only; ORCHID does not redistribute the paper. Context items carry their quote and page.",
        }
        if bib is None:
            bib = self._bib.bib_paper(pid, required=False)
        if bib is not None:
            out["bibliography"] = self._bib.bib_view(bib, fields)
            out["how_to_cite_bibliography"] = bib_cite_text
        return self._core.wrap(out)

    def _reported(self, rec: dict[str, Any], pid: str) -> tuple[dict[str, dict[str, int]], int]:
        """Values this paper reports, per system, and how many values elsewhere cite it as their source.

        Args:
            rec (dict[str, Any]): The paper's entry in the paper table.
            pid (str): ORCHID paper id.

        Returns:
            (per-system counts of values, accepted values and values by evidence class; number of citing values).
        """
        reported: dict[str, dict[str, int]] = {}
        cited = 0
        for sid in rec.get("systems") or []:
            doc = self._core.system_doc(sid)
            if doc is None:
                continue
            for r in doc["records"]:
                if r.get("paper") == pid:
                    d = reported.setdefault(sid, {"values": 0, "accepted": 0})
                    d["values"] += 1
                    d["accepted"] += int(self._core.accepted(r))
                    ev = r.get("evidence_class") or "?"
                    d[f"evidence_class:{ev}"] = d.get(f"evidence_class:{ev}", 0) + 1
                elif any((c or {}).get("paper") == pid for c in r.get("cited_sources") or []):
                    cited += 1
        return reported, cited
