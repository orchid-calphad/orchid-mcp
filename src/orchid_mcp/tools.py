"""ORCHID query tools: a thin facade over the per-domain modules, with the public API of earlier releases.

Every tool returns a JSON-serialisable ``dict``. Literature values keep their
full ORCHID record (evidence class, origin, flags, uncertainty basis, readers,
cited sources, ...); the compact per-system storage is expanded so that each
value carries its own citation, DOI, page and quote (``source``), its table or
passage locator, and its model link. Nothing is averaged across evidence
classes or across DFT sources.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .bibliography import BibliographyTools, bib_used_default
from .constants import default_limit
from .core import OrchidCore
from .dft import DftTools
from .diagrams import DiagramsTools
from .errors import OrchidError
from .papers import PapersTools
from .phases import PhasesTools
from .prompts import cite_orchid_prompt
from .summaries import ml_fold, ml_folds, summarize_budget, summarize_record
from .systems import SystemsTools
from .tdb import TdbTools, tdb_max_bytes

if TYPE_CHECKING:
    from .datasource import DataSource

__all__ = [
    "Orchid",
    "OrchidError",
    "cite_orchid_prompt",
    "ml_fold",
    "ml_folds",
    "summarize_budget",
    "summarize_record",
]


class Orchid:
    """Facade over the ORCHID data (literature, phase diagrams, hulls, TDB catalog, bibliography, phase registry).

    Args:
        data (DataSource | None): Source of the data release; defaults to the environment (see ``sources_from_env``).
        site (DataSource | None): Source of the website data (TDB catalog); defaults to the environment.
    """

    def __init__(self, data: DataSource | None = None, site: DataSource | None = None) -> None:
        """Build the shared core and one tool object per domain."""
        self._core = OrchidCore(data, site)
        self._systems = SystemsTools(self._core)
        self._diagrams = DiagramsTools(self._core)
        self._dft = DftTools(self._core)
        self._tdb = TdbTools(self._core)
        self._bibliography = BibliographyTools(self._core)
        self._papers = PapersTools(self._core, self._bibliography)
        self._phases = PhasesTools(self._core)

    @property
    def data(self) -> DataSource:
        """Source of the ORCHID data release."""
        return self._core.data

    @property
    def site(self) -> DataSource:
        """Source of the website data (TDB catalog and databases)."""
        return self._core.site

    def meta(self) -> dict[str, Any]:
        """Dataset description, release, licence and citation text.

        Returns:
            The ``orchid`` metadata block.
        """
        return self._core.meta()

    def resource_text(self, name: str) -> str:
        """Text of a documentation resource (schema, dataset card, licence, bibliography schema).

        Args:
            name (str): Resource key.

        Returns:
            The text of the resource's files.
        """
        return self._core.resource_text(name)

    def search_systems(  # noqa: PLR0917 (public signature kept)
        self,
        elements: list[str] | None = None,
        text: str | None = None,
        exact: bool = False,
        limit: int = default_limit,
        offset: int = 0,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """Find chemical systems with literature data and/or calculated phase diagrams.

        Args:
            elements (list[str] | None): Systems containing all of them (``exact=True``: exactly them).
            text (str | None): Match on the system name or on titles/citations of papers reporting that system.
            exact (bool): Require an exact element set.
            limit (int): Page size.
            offset (int): Rows to skip.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            A page of systems with counts and links.
        """
        return self._systems.search_systems(elements=elements, text=text, exact=exact, limit=limit, offset=offset, fields=fields)

    def get_system_data(  # noqa: PLR0917 (public signature kept)
        self,
        system: str,
        property_group: str | None = None,
        evidence_class: str | None = None,
        accepted_only: bool = True,
        limit: int = default_limit,
        offset: int = 0,
        property: str | None = None,  # noqa: A002 (public parameter name)
        compact: bool = False,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """Literature values for one system, filtered and paged (see ``SystemsTools.get_system_data``).

        Args:
            system (str): System, e.g. ``"Al-Ni"``.
            property_group (str | None): Substring of the property group.
            evidence_class (str | None): One of experimental, model, dft, unspecified, assessed_phase_diagram.
            accepted_only (bool): Keep only accepted records.
            limit (int): Page size.
            offset (int): Rows to skip.
            property (str | None): Property code, e.g. ``"HM_FORM"``.
            compact (bool): In full mode, drop pipeline bookkeeping.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            A page of values with contexts and counts.
        """
        return self._systems.get_system_data(
            system=system,
            property_group=property_group,
            evidence_class=evidence_class,
            accepted_only=accepted_only,
            limit=limit,
            offset=offset,
            property=property,
            compact=compact,
            fields=fields,
        )

    def get_phase_diagram(  # noqa: PLR0917, N803 (public parameter names kept)
        self,
        system: str,
        assessment: str | None = None,
        x: float | None = None,
        T: float | None = None,  # noqa: N803 (kelvin, public name)
        fields: str = "summary",
    ) -> dict[str, Any]:
        """Calculated binary phase diagram(s) of a system; optionally the stable phases at (x, T).

        Args:
            system (str): System, e.g. ``"Al-Ni"``.
            assessment (str | None): TDB file name or DOI; None returns every assessment.
            x (float | None): Mole fraction of the diagram's x component.
            T (float | None): Temperature in kelvin.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            One summary per assessment, with ``phase_at`` when x and T are given.
        """
        return self._diagrams.get_phase_diagram(system=system, assessment=assessment, x=x, T=T, fields=fields)

    def get_invariant_reactions(  # noqa: PLR0917 (public signature kept)
        self,
        system: str,
        source: str = "both",
        assessment: str | None = None,
        accepted_only: bool = True,
        limit: int = default_limit,
        offset: int = 0,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """Literature and calculated invariant reactions of a system, side by side.

        Args:
            system (str): System, e.g. ``"Al-Ni"``.
            source (str): ``"literature"``, ``"calculated"`` or ``"both"``.
            assessment (str | None): TDB file name or DOI for the calculated part.
            accepted_only (bool): Keep only accepted literature records.
            limit (int): Page size of the literature list.
            offset (int): Rows to skip in the literature list.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            Literature page, calculated invariants per assessment with literature matches, and the matching rule.
        """
        return self._diagrams.get_invariant_reactions(
            system=system,
            source=source,
            assessment=assessment,
            accepted_only=accepted_only,
            limit=limit,
            offset=offset,
            fields=fields,
        )

    def get_formation_enthalpy(  # noqa: PLR0917 (public signature kept)
        self,
        formula: str,
        accepted_only: bool = True,
        include_higher_order_systems: bool = True,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """Formation enthalpy of a compound: literature values (kJ/mol-atom) and MP / OQMD DFT values.

        Args:
            formula (str): Single multi-element formula, e.g. ``"Al3Ni"``.
            accepted_only (bool): Keep only accepted literature records.
            include_higher_order_systems (bool): Also search higher-order systems containing the elements.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            Literature values, DFT entries per layer, statistics and matching rules.
        """
        return self._dft.get_formation_enthalpy(
            formula=formula,
            accepted_only=accepted_only,
            include_higher_order_systems=include_higher_order_systems,
            fields=fields,
        )

    def compare_to_dft(self, system: str, default_subset_only: bool = False, fields: str = "summary") -> dict[str, Any]:
        """Literature formation enthalpies vs Materials Project vs OQMD for one system.

        Args:
            system (str): System, e.g. ``"Al-Ni"``.
            default_subset_only (bool): Keep only the default experimental subset.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            Compounds with literature values and DFT points, residual statistics and hull vertices.
        """
        return self._dft.compare_to_dft(system=system, default_subset_only=default_subset_only, fields=fields)

    def list_tdb_files(  # noqa: PLR0917 (public signature kept)
        self,
        elements: list[str] | None = None,
        exact: bool = False,
        tier: str | None = None,
        limit: int = default_limit,
        offset: int = 0,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """TDB catalog entries whose system contains all ``elements`` (``exact``: exactly them).

        Args:
            elements (list[str] | None): Element symbols.
            exact (bool): Require an exact element set.
            tier (str | None): ``bundled``, ``fetched`` or ``manual``.
            limit (int): Page size.
            offset (int): Rows to skip.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            A page of catalog entries and the tier legend.
        """
        return self._tdb.list_tdb_files(elements=elements, exact=exact, tier=tier, limit=limit, offset=offset, fields=fields)

    def get_tdb(
        self,
        file: str,
        include_content: bool = True,
        max_bytes: int = tdb_max_bytes,  # noqa: A002 (public name)
    ) -> dict[str, Any]:
        """One TDB catalog entry; the file content only for bundled, confirmed-licence files.

        Args:
            file (str): File name, e.g. ``"AlNi-05Mie.tdb"``.
            include_content (bool): Return the file text when served.
            max_bytes (int): Larger files are withheld from the content.

        Returns:
            The entry, the SHA-256 of the served bytes, and the content or why it is withheld.
        """
        return self._tdb.get_tdb(file=file, include_content=include_content, max_bytes=max_bytes)

    def get_paper(self, doi: str, fields: str = "summary") -> dict[str, Any]:
        """Bibliographic record of a paper (by DOI or ORCHID id), its systems, values and contexts.

        Args:
            doi (str): DOI or ORCHID paper id.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            The paper record and its bibliography entry.
        """
        return self._papers.get_paper(doi=doi, fields=fields)

    def search_papers(  # noqa: PLR0917 (public signature kept)
        self,
        system: str | None = None,
        elements: list[str] | None = None,
        role: str | None = None,
        property: str | None = None,  # noqa: A002 (public parameter name)
        evidence_class: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        text: str | None = None,
        limit: int = default_limit,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the ORCHID bibliography by system, elements, role, property, evidence class, year and text.

        Args:
            system (str | None): Exact system, e.g. ``"Al-Ni"``.
            elements (list[str] | None): Papers with a system containing all of them.
            role (str | None): Paper role.
            property (str | None): Property code.
            evidence_class (str | None): Evidence class.
            year_from (int | None): Earliest year.
            year_to (int | None): Latest year.
            text (str | None): Substring of title, authors or DOI.
            limit (int): Page size.
            offset (int): Rows to skip.

        Returns:
            A page of papers with paging fields.
        """
        return self._bibliography.search_papers(
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

    def get_paper_relations(  # noqa: PLR0917 (public signature kept)
        self, doi: str, relation: str | None = None, direction: str = "both", limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """Typed relations of one paper (edges with provenance).

        Args:
            doi (str): DOI or bibliography id.
            relation (str | None): Relation type; None for all.
            direction (str): ``"out"``, ``"in"`` or ``"both"``.
            limit (int): Page size.
            offset (int): Rows to skip.

        Returns:
            A page of relations with counts by type.
        """
        return self._bibliography.get_paper_relations(doi=doi, relation=relation, direction=direction, limit=limit, offset=offset)

    def get_assessment_evidence(self, system: str, max_data_papers: int = bib_used_default) -> dict[str, Any]:
        """Assessments of an exact system with the original data papers each used, and reassessment links.

        Args:
            system (str): Exact system, e.g. ``"Al-Ni"``.
            max_data_papers (int): Cap on data papers listed per assessment.

        Returns:
            The assessments, reassessment chain and role counts.
        """
        return self._bibliography.get_assessment_evidence(system=system, max_data_papers=max_data_papers)

    def get_phase(self, system: str, phase: str, fields: str = "summary") -> dict[str, Any]:
        """One phase of a binary system from the ORCHID phase registry.

        Args:
            system (str): Binary system, e.g. ``"Al-Ni"``.
            phase (str): Registry id, namespaced name, or label such as ``"L12_FCC"``.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            The phase, or an ambiguity block listing the matches.
        """
        return self._phases.get_phase(system=system, phase=phase, fields=fields)

    def search_phases(  # noqa: PLR0917 (public signature kept)
        self,
        elements: list[str] | None = None,
        name: str | None = None,
        prototype: str | None = None,
        tier: str | None = None,
        status: str | None = None,
        limit: int = default_limit,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the phase registry by elements, name, prototype, tier and status.

        Args:
            elements (list[str] | None): Element symbols.
            name (str | None): Name substring.
            prototype (str | None): Prototype, Strukturbericht, Pearson symbol or structure-type id.
            tier (str | None): Tier key.
            status (str | None): Registry status.
            limit (int): Page size.
            offset (int): Rows to skip.

        Returns:
            A page of phase summaries with paging fields.
        """
        return self._phases.search_phases(
            elements=elements, name=name, prototype=prototype, tier=tier, status=status, limit=limit, offset=offset
        )

    def get_structure_type(self, query: str, limit: int = 20) -> dict[str, Any]:
        """Structure types by id, prototype, Strukturbericht, Pearson symbol or space group.

        Args:
            query (str): Structure-type query, e.g. ``"AuCu3"`` or ``"221"``.
            limit (int): Maximum number of types returned.

        Returns:
            The matching structure types with example phase ids.
        """
        return self._phases.get_structure_type(query=query, limit=limit)
