"""Vocabulary, public URLs and default limits shared by the ORCHID query modules."""

site_url = "https://orchid-project.org/"
public_data_url = "https://data.orchid-project.org/"
public_site_data_url = "https://orchid-project.org/data/"

kj_per_mol_per_ev = 96.485  # 1 eV/atom = 96.485 kJ/mol-atom (same factor as literature/dft_parity.json)

evidence_classes = ("experimental", "model", "dft", "unspecified", "assessed_phase_diagram")
dft_layers = {
    "mp": {"dir": "hulls/dft-mp", "label": "Materials Project"},
    "oqmd": {"dir": "hulls/dft-oqmd", "label": "OQMD"},
}
field_modes = ("summary", "full")

default_limit = 50  # rows per page unless a tool documents otherwise
quote_max = 240  # characters of a sentence quote kept in summaries

fields_note = (
    "fields='summary': id, property, value, unit, T, phases, evidence class, accepted, citation, DOI, page, a "
    "quote shortened to 240 characters (table values: the table locator) and a compact uncertainty budget "
    "(components and sigma_effective), and the machine-learning folds (chemical_system, paper_id, fold_lso = "
    "leave-chemical-system-out fold, fold_lpo = leave-paper-out fold; int(sha256(key), 16) mod 5). Use fields='full' (or the ids) for "
    "complete provenance: locator, origin, flags, uncertainty, conditions, cited sources and model links. "
    "Summaries preserve human_review_corrections, specification_gaps, duplicate_of, evidence_identity and merged ids. "
    "Accepted extraction does not imply fitness for fitting. Respect espei_ready and specification_gaps; "
    "human-review corrections requiring downstream revalidation remain blocked. Count duplicate/alternate-unit "
    "representations once using their canonical identity, and keep related observations in the same evaluation split. "
    "Source adjudication by Codex is not independent human re-review."
)

cite_text = (
    "Cite the ORCHID data release you used (see CITATION.cff / the Zenodo DOI of the release) AND the original "
    "source of every value: the paper in `source.citation` / `source.doi` (for values the paper cites from earlier "
    "work, also `cited_sources`), the TDB assessment (`model_citation` / `model_doi`) for calculated values, and "
    "Materials Project (Jain et al., APL Mater. 1, 011002 (2013), doi:10.1063/1.4812323) or OQMD for DFT values."
)
