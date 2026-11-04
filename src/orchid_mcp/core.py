"""Shared state of the ORCHID query tools: data sources, the per-system literature cache and the response envelope."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .chem import ChemError, element, system_id
from .constants import cite_text, field_modes
from .datasource import DataSourceError, sources_from_env
from .errors import OrchidError
from .report import report_note, report_url_for

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .datasource import DataSource

resource_paths: dict[str, list[tuple[str, str]]] = {
    "schema": [("data", "literature/SCHEMA.md")],
    "dataset-card": [("data", "README.md"), ("data", "CITATION.cff")],
    "licence": [("data", "LICENSES.md"), ("data", "NOTICE.md"), ("data", "LICENSE")],
    "bibliography-schema": [("data", "bibliography/SCHEMA.md")],
}
_dataset_name = "ORCHID (Open Repository for CALPHAD Hypotheses, Intelligence, and Discovery)"


def check_fields(fields: str) -> str:
    """Validate the ``fields`` argument of a tool.

    Args:
        fields (str): ``"summary"`` or ``"full"`` (case and surrounding spaces are ignored; empty means summary).

    Returns:
        The normalised mode.

    Raises:
        OrchidError: When the mode is not one of the accepted modes.
    """
    f = (fields or "summary").strip().lower()
    if f not in field_modes:
        raise OrchidError(f"fields must be one of {field_modes}")
    return f


def wanted_elements(elements: list[str] | None) -> set[str]:
    """Element tokens of a user-supplied list.

    Args:
        elements (list[str] | None): Element symbols in any case.

    Returns:
        The upper-case tokens (empty for None or an empty list).

    Raises:
        OrchidError: When a symbol is not an element.
    """
    try:
        return {element(e) for e in elements or []}
    except ChemError as exc:
        raise OrchidError(str(exc)) from exc


def norm_doi(doi: str) -> str:
    """Lower-case DOI without the ``doi:`` or resolver prefix.

    Args:
        doi (str): A DOI, optionally written as a doi.org URL or with a ``doi:`` prefix.

    Returns:
        The bare, lower-case DOI.
    """
    d = doi.strip()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d, flags=re.IGNORECASE)
    d = re.sub(r"^doi:\s*", "", d, flags=re.IGNORECASE)
    return d.lower()


class OrchidCore:
    """Data sources, cached system documents and the response envelope shared by the domain tools.

    Args:
        data (DataSource | None): Source of the ORCHID data release (literature, phase diagrams, hulls, bibliography).
        site (DataSource | None): Source of the website data (TDB catalog and databases). Missing sources are read
            from the environment (see ``sources_from_env``).
    """

    def __init__(self, data: DataSource | None = None, site: DataSource | None = None) -> None:
        """Store the two sources, falling back to the environment for any that are missing."""
        if data is None or site is None:
            d, s = sources_from_env()
            data, site = data or d, site or s
        self.data = data
        self.site = site
        self._systems: dict[str, dict[str, Any]] = {}

    def lit_index(self) -> dict[str, Any]:
        """Literature index of the release (``literature/index.json``).

        Returns:
            The parsed index.
        """
        return self.data.read_json("literature/index.json")

    def pd_index(self) -> dict[str, Any]:
        """Phase-diagram index (``phase-diagrams/index.json``).

        Returns:
            The parsed index.
        """
        return self.data.read_json("phase-diagrams/index.json")

    def papers(self) -> dict[str, Any]:
        """Paper table of the literature (``literature/papers.json``), keyed by ORCHID paper id.

        Returns:
            The parsed table.
        """
        return self.data.read_json("literature/papers.json")

    def meta(self) -> dict[str, Any]:
        """Dataset description, release, licence, citation text and notes attached to every response.

        Returns:
            The ``orchid`` block of a response.
        """
        idx = self.lit_index()
        return {
            "dataset": _dataset_name,
            "release": idx.get("release"),
            "licence": idx.get("licence"),
            "data_source": self.data.location,
            "how_to_cite": cite_text,
            "notes": [
                "Evidence classes (experimental | model | dft | unspecified) are never mixed or averaged.",
                "uncertainty.basis 'stated_default' / 'reading_resolution' is an ORCHID placeholder, not a measurement.",
                "Every value carries uncertainty_budget: components kept separate (sigma_printed, sigma_reading, "
                "sigma_method = method-class floor calibrated from ORCHID duplicate measurements or a documented "
                "prior, sigma_composition, sigma_scatter, sigma_conversion) and sigma_effective = max(sqrt(max("
                "printed, reading, method)^2 + composition^2 + conversion^2), scatter), the recommended sigma; "
                "printed values and printed uncertainties are unchanged (literature/UNCERTAINTY_CALIBRATION.md).",
                report_note,
                "Quotes are short excerpts kept so that each value can be verified; they remain under the "
                "publishers' copyright and are not relicensed; source papers are not redistributed.",
            ],
        }

    def wrap(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach the ``orchid`` metadata block to a response payload.

        Args:
            payload (dict[str, Any]): The tool's result; modified in place.

        Returns:
            The same dict with an ``orchid`` key added last.
        """
        payload["orchid"] = self.meta()
        return payload

    def sys_id(self, system: Any) -> str:
        """Normalised system id of a user-supplied system (``"Ni-Al"`` and ``["ni", "al"]`` both give ``"AL-NI"``).

        Args:
            system (Any): A system string or a list of element symbols.

        Returns:
            Sorted, upper-case element tokens joined with ``-``.

        Raises:
            OrchidError: When the system cannot be parsed.
        """
        try:
            return system_id(system)
        except ChemError as exc:
            raise OrchidError(str(exc)) from exc

    def system_doc(self, sys_id: str) -> dict[str, Any] | None:
        """Merged literature document of one chemical system, cached for the life of the instance.

        Args:
            sys_id (str): Normalised system id, e.g. ``"AL-NI"``.

        Returns:
            Dict with ``records``, ``artifacts``, ``models``, ``papers``, ``contexts`` and the system metadata, or
            None when the release has no literature for the system.
        """
        if sys_id in self._systems:
            return self._systems[sys_id]
        entry = self.lit_index().get("systems", {}).get(sys_id)
        if entry is None:
            return None
        merged: dict[str, Any] = {"records": [], "artifacts": {}, "models": {}, "papers": {}, "contexts": {}}
        for path in entry.get("files") or [f"systems/{sys_id}.json"]:
            part = self.data.read_json(f"literature/{path}")
            merged["records"].extend(part.get("records", []))
            for key in ("artifacts", "models", "papers", "contexts"):
                merged[key].update(part.get(key) or {})
            for key in ("system", "elements", "licence", "release"):
                merged.setdefault(key, part.get(key))
        self._systems[sys_id] = merged
        return merged

    def paper_info(self, pid: str | None, doc: dict[str, Any] | None = None) -> dict[str, Any]:
        """Citation record of a paper: from the system document when present, else from the paper table.

        Args:
            pid (str | None): ORCHID paper id; falsy gives an empty record.
            doc (dict[str, Any] | None): System document whose ``papers`` block is searched first.

        Returns:
            The paper's record (possibly empty).
        """
        if not pid:
            return {}
        p = (doc or {}).get("papers", {}).get(pid)
        if p is None:
            p = self.papers().get(pid, {})
        return p

    def expand(self, rec: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
        """Expanded copy of a stored record: locator, model link, its source block and a pre-filled report link.

        Args:
            rec (dict[str, Any]): Compact record as stored in the system file (not modified).
            doc (dict[str, Any]): The system document the record belongs to.

        Returns:
            A new dict with ``source`` (paper, citation, DOI, page, quote and locator fields) and
            ``report_issue_url`` added.
        """
        r = dict(rec)
        r.setdefault("elements", doc.get("elements"))
        loc = rec.get("locator") or {}
        art = doc.get("artifacts", {}).get(loc.get("ref"), {}) if isinstance(loc, dict) else {}
        if art:
            r["locator"] = {**art, **loc}
        mkey = rec.get("model")
        if isinstance(mkey, str) and mkey in doc.get("models", {}):
            r["model"] = {"key": mkey, **doc["models"][mkey]}
        paper = self.paper_info(rec.get("paper"), doc)
        quote = rec.get("quote")
        page = rec.get("quote_page") or (r.get("locator") or {}).get("page")
        src = {
            "paper": rec.get("paper"),
            "citation": paper.get("citation"),
            "doi": paper.get("doi"),
            "doi_url": paper.get("doi_url"),
            "year": paper.get("year"),
            "page": page,
            "quote": quote,
            "artifact": (r.get("locator") or {}).get("label"),
            "caption": (r.get("locator") or {}).get("caption"),
            "row": (r.get("locator") or {}).get("row"),
        }
        if quote is None:
            src["quote_note"] = "table value: no sentence quote; verify with the table label/caption/page/row in `locator`"
        r["source"] = src
        r["report_issue_url"] = report_url_for(r)  # pre-filled issue form; never posted by the server
        return r

    def contexts_for(self, records: Iterable[dict[str, Any]], doc: dict[str, Any]) -> dict[str, Any]:
        """Experimental-context blocks of the papers behind a set of records.

        Args:
            records (Iterable[dict[str, Any]]): Records (expanded or stored) whose papers to look up.
            doc (dict[str, Any]): The system document holding the ``contexts`` block.

        Returns:
            Paper id to context block, for records whose condition is a paper context.
        """
        out = {}
        for r in records:
            cond = r.get("conditions") or {}
            pid = cond.get("paper") or r.get("paper")
            if cond.get("kind") == "paper_context" and pid in doc.get("contexts", {}):
                out[pid] = doc["contexts"][pid]
        return out

    @staticmethod
    def accepted(rec: dict[str, Any]) -> bool:
        """Whether a record passed extraction acceptance.

        Args:
            rec (dict[str, Any]): Stored record.

        Returns:
            True when ``flags.extraction_accepted`` is set.
        """
        return bool((rec.get("flags") or {}).get("extraction_accepted"))

    def resource_text(self, name: str) -> str:
        """Text of a documentation resource (schema, dataset card, licence) with a comment line per file.

        Args:
            name (str): One of the keys of ``resource_paths``.

        Returns:
            The concatenated files; a missing file is replaced by an "unavailable" comment.

        Raises:
            KeyError: When ``name`` is not a known resource.
        """
        parts = []
        for which, p in resource_paths[name]:
            src = self.data if which == "data" else self.site
            try:
                parts.append(f"<!-- {p} -->\n" + src.read_text(p))
            except DataSourceError as exc:
                parts.append(f"<!-- {p}: unavailable ({exc}) -->")
        return "\n\n".join(parts)
