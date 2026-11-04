"""MCP prompt template: how to cite ORCHID and the original sources of each value."""


def cite_orchid_prompt(topic: str = "") -> str:
    """Prompt template telling the model how to cite ORCHID and the original sources.

    Args:
        topic (str): Optional topic the answer is about; added to the first sentence.

    Returns:
        The prompt text.
    """
    about = f" about {topic}" if topic else ""
    return (
        f"You are answering a question{about} with data from ORCHID (Open Repository for CALPHAD Hypotheses, "
        "Intelligence, and Discovery; https://orchid-project.org, data at https://data.orchid-project.org).\n\n"
        "Rules for citing:\n"
        "1. For every number you report, give its evidence class (experimental, model, dft, unspecified) and its "
        "original source: the paper citation and DOI from `source.citation` / `source.doi`, the page, and the quote "
        "or table locator. If the value is cited from earlier work (`origin.own_work_of_paper` false), name the "
        "`cited_sources` as the primary source.\n"
        "2. Calculated (model) values: cite the CALPHAD assessment (`model_citation`, `model_doi`) and name the TDB "
        "file and its SHA-256. DFT values: cite Materials Project (A. Jain et al., APL Mater. 1, 011002 (2013), "
        "doi:10.1063/1.4812323, release in the response) or OQMD (Saal et al., JOM 65, 1501 (2013), "
        "doi:10.1007/s11837-013-0755-4; Kirklin et al., npj Comput. Mater. 1, 15010 (2015)).\n"
        "3. Never average values across evidence classes, assessments or DFT sources. Report "
        "`uncertainty.basis: stated_default` as 'no printed uncertainty' rather than as a measurement. As the "
        "value's uncertainty report `uncertainty_budget.quantities.value.sigma_effective` (the recommended sigma) and "
        "say what dominates it (printed sigma or the method floor, with its class and basis).\n"
        "4. Cite the ORCHID data release you used (release string in `orchid.release`; Zenodo DOI per CITATION.cff): "
        "R. Arróyave, J. Saal, D. Shin, ORCHID data: literature thermodynamic data, calculated phase diagrams and "
        "DFT hulls, https://data.orchid-project.org (CC BY 4.0).\n"
        "5. Quotes are short excerpts kept for verification and remain under the publishers' copyright (not "
        "relicensed); do not reproduce more of the source paper than the quote."
    )
