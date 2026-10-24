# orchid-mcp

An [MCP](https://modelcontextprotocol.io) server for **ORCHID** (Open Repository for CALPHAD Hypotheses,
Intelligence, and Discovery): literature thermodynamic and phase-equilibrium values, calculated binary phase
diagrams, Materials Project / OQMD formation energies and the TDB catalog — every value returned with its
citation, DOI, page, quote (or table locator), evidence class and flags. Also: an OpenAPI description of the static
JSON files at `data.orchid-project.org` (`openapi/openapi.yaml`).

## Install

```bash
python3.13 -m venv .venv                 # a dedicated environment (git-ignored)
.venv/bin/pip install -e '.[mcp]'        # with the official MCP Python SDK, pinned to mcp==2.3.0 (recommended)
.venv/bin/pip install -e .               # stdlib only: built-in stdio fallback server, no HTTP mode
```

The package itself has no runtime dependencies. If the `mcp` SDK is installed, the server runs on it (`MCPServer` in
mcp 2.x, tested with 2.3.0; `FastMCP` in mcp 1.x is also supported); otherwise
`orchid-mcp` serves the MCP stdio protocol (tools, resources, prompts) with a small built-in JSON-RPC loop.
Streamable HTTP (`--transport http`) needs `pip install mcp`.

## Data source

| variable | default | meaning |
|---|---|---|
| `ORCHID_DATA_DIR` | — | local copy of `data.orchid-project.org` (an `orchid-data` checkout). If set, no network is used. |
| `ORCHID_DATA_URL` | `https://data.orchid-project.org/` | online data |
| `ORCHID_SITE_DATA_DIR` | — | local copy of `orchid-project.org/data` (`catalog.json`, `databases/`) |
| `ORCHID_SITE_DATA_URL` | `https://orchid-project.org/data/` | online catalog and bundled TDB files |
| `ORCHID_CACHE_DIR` | `~/.cache/orchid-mcp` | on-disk cache of remote files |
| `ORCHID_CACHE_TTL` | `86400` | seconds before a cached file is refetched (stale copies are used if offline) |
| `ORCHID_OFFLINE` | — | `1`: never touch the network, serve from the cache |

## Run

```bash
orchid-mcp                                   # stdio (default)
orchid-mcp --transport http --port 8000      # streamable HTTP at http://127.0.0.1:8000/mcp (needs `mcp`)
```

### Claude Code

```bash
# as installed on the Mac Studio: user scope, offline, from local checkouts, using the dedicated venv
claude mcp add --scope user orchid \
  -e ORCHID_DATA_DIR=$HOME/projects/orchid-data \
  -e ORCHID_SITE_DATA_DIR=$HOME/projects/orchid-project.org/data \
  -- $HOME/projects/orchid-mcp/.venv/bin/orchid-mcp

# online (data fetched from data.orchid-project.org and cached)
claude mcp add orchid -- orchid-mcp
# offline, from local checkouts
claude mcp add orchid \
  -e ORCHID_DATA_DIR=$HOME/projects/orchid-data \
  -e ORCHID_SITE_DATA_DIR=$HOME/projects/orchid-project.org/data \
  -- orchid-mcp
# over HTTP (server started separately)
claude mcp add --transport http orchid http://127.0.0.1:8000/mcp
```

Use the absolute path of `orchid-mcp` (e.g. `$(which orchid-mcp)`) if it is not on the PATH Claude Code sees.

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "orchid": {
      "command": "/absolute/path/to/orchid-mcp",
      "env": {
        "ORCHID_DATA_DIR": "/Users/you/projects/orchid-data",
        "ORCHID_SITE_DATA_DIR": "/Users/you/projects/orchid-project.org/data"
      }
    }
  }
}
```

Omit `env` to use the online data.

## Tools

| tool | returns |
|---|---|
| `search_systems(elements, text, exact, limit=50, offset, fields)` | systems with value counts by evidence class and calculated assessments (`full`: property groups, citations, viewer URLs) |
| `get_system_data(system, property_group, evidence_class, accepted_only=True, limit=50, offset, property, fields, compact)` | literature values; `full`: complete records with an expanded `source` (citation, DOI, page, quote or table locator) |
| `get_invariant_reactions(system, source="literature"\|"calculated"\|"both", assessment, limit=50, offset, fields)` | literature invariants (paged) and calculated invariants per SGTE assessment, with exact phase-set matches and dT |
| `get_formation_enthalpy(formula, fields)` | literature HM_FORM (kJ/mol-atom) + Materials Project and OQMD entries at that composition + per-class summaries |
| `get_phase_diagram(system, assessment, x, T, fields)` | TDB file, SHA-256, assessment citation/DOI, phases, invariants, viewer URL (`full`: region summary, grid); stable phases at (x, T) from the stored grid |
| `list_tdb_files(elements, exact, tier, limit=50, offset, fields)` / `get_tdb(file)` | licence, tier, SHA-256, download URL (`full`: whole catalog entry); file content **only** for bundled, confirmed-licence files |
| `get_paper(doi, fields)` | bibliographic record, systems, number of values, experimental-context items with quotes, and the bibliography entry: roles, evidence summary, Crossref metadata, open-access licence (Crossref `license` only), relation counts (`full`: whole record and every relation with provenance). Accepts a DOI, `doi:`/`nodoi:` id, literature id or bibliography key |
| `search_papers(system, elements, role, property, evidence_class, year_from, year_to, text, limit=50, offset)` | papers of the bibliography (every paper ORCHID knows) by exact system, elements, role (`corpus_assessment_read`, `cited_original_source`, `tdb_source_assessment`, `handbook_diagram_source`, `discovery_lead`, ...), property code, evidence class, years, title/author text |
| `get_paper_relations(doi, relation, direction="both", limit=100, offset)` | typed edges with provenance: `cites`, `used_in_assessment`, `models_system_in_tdb`, `reassesses` (citation+same-system), `handbook_reproduces`, `cited_by_crossref` — e.g. which original papers an assessment used, which assessments used a paper, a system's reassessment chain |
| `get_assessment_evidence(system, max_data_papers=25)` | assessments of an exact system with the data papers each used (value counts, evidence classes), TDB files and reassessment links |
| `compare_to_dft(system, fields)` | literature vs MP vs OQMD formation enthalpies with residuals and the DFT hull vertices |
| `get_phase(system, phase, fields)` | one phase of the phase registry by id, namespaced name (`tdb:`, `paper:`, `corpus:`, `mp:`, `oqmd:`) or label (`L12_FCC`, `NI3TI`): structure (tier A/B/C), names, TDB models with verbatim PHASE/CONSTITUENT lines, stability, first 5 reference DOIs (`full`: `tdb_definition`, all references, candidates) |
| `search_phases(elements, name, prototype, tier, status, limit=50, offset)` | phases filtered by elements, name/label, prototype / Strukturbericht / Pearson (`L1_2` = `L12`), tier (A, B, C, liquid, gas, none) |
| `get_structure_type(query)` | structure types by id, prototype, Strukturbericht, Pearson or space group, with sources, number of registry phases and example phase ids |

### Response size: `fields` and paging

List-returning tools default to `fields="summary"`. A summary literature value carries `id`, `property`, `value`,
`unit`, `T_K`, `phases`, `reaction` (invariants), `evidence_class`, `accepted`, `espei_ready` (usable in ESPEI: complete state, phase mapped to a model, physical phase rule), `espei_certified` (ESPEI-ready and agreeing with a pycalphad calculation from the linked TDB), `calphad_check` (`agrees` / `disagrees` / `not_available`), `gate_failures` (codes of the literature-level gates that demoted or flagged the value, when any) and `corrected_by` (rules of gate corrections, when any; the stored value is in the full record), `citation`, `doi`, `page` and a
`quote` shortened to 240 characters, or `table` (label and row) for table values. Each summary also carries `uncertainty_budget`: per
quantity (`value`, `composition`, `temperature_K`) the components in the quantity's unit (`sigma_printed`,
`sigma_reading`, `sigma_method` {value, class, basis}, `sigma_composition`, `sigma_scatter`, `sigma_conversion`) and
`sigma_effective`, plus `sigma_rule` = max(sqrt(max(printed, reading, method)^2 + composition^2 + conversion^2),
scatter) and the calibration version. `sigma_effective` is the recommended sigma; method floors are calibrated from
ORCHID duplicate measurements or documented priors (`literature/UNCERTAINTY_CALIBRATION.md`). `fields="full"` returns the
complete record with all provenance (locator, origin, flags, uncertainty, conditions, cited sources, model link).
Paged tools report `total`, `offset`, `returned`/`truncated` and (for `get_system_data`) `next_offset`. With the
SDK, results are sent as one compact JSON text block.

CU-ZR, bytes of JSON per call (0.1.0 defaults → current defaults):

| call | before | after |
|---|---:|---:|
| `get_system_data("CU-ZR")` (200 full → 50 summary, of 792) | 521,405 | 23,234 |
| `get_invariant_reactions("CU-ZR")` (all 420 literature + 11 assessments) | 1,294,212 | 83,218 |
| `get_phase_diagram("CU-ZR")` (11 assessments) | 108,883 | 38,958 |
| `compare_to_dft("CU-ZR")` | 86,015 | 37,597 |
| `get_formation_enthalpy("CuZr2")` | 51,940 | 15,140 |
| `search_systems(["Cu"])` | 47,947 | 12,135 |
| `list_tdb_files(["Cu","Zr"])` | 20,874 | 10,294 |

Resources: `orchid://schema` (SCHEMA.md), `orchid://bibliography-schema` (bibliography/SCHEMA.md), `orchid://dataset-card` (README + CITATION.cff), `orchid://licence`
(LICENSES.md + NOTICE.md). Prompt: `cite_orchid` (how to cite ORCHID and the original sources).

Rules the tools keep: evidence classes (experimental, model, dft, unspecified) are never mixed or averaged; MP and
OQMD use different energy references and are never averaged; each calculated diagram is one assessment;
`uncertainty.basis: stated_default` is a placeholder, not a measurement (use `uncertainty_budget.sigma_effective`); `fetched` and `manual` TDB files are never
served (only their original-source links).

## Example questions

- "Which Al–Ni invariant reactions have been measured, and how do the SGTE assessments reproduce them?"
- "What is the formation enthalpy of Al3Ni? Compare calorimetry with Materials Project and OQMD."
- "Which phases are stable in Ni–Ti at x(Ti)=0.5 and 1200 K according to NiTi-04Tok?"
- "List the Cu–Zr TDB files I can download and their licences."
- "What experimental conditions did doi:10.1016/j.tca.2010.10.005 use?"
- "Which binary phases have the L1_2 (AuCu3) structure, and how is NI3TI modelled in the SGTE TDB files?"

## Static JSON API

`openapi/openapi.yaml` (OpenAPI 3.1) documents the read-only file layout: `GET /literature/index.json`,
`/literature/systems/{SYS}.json`, `/literature/papers.json`, `/phase-diagrams/index.json`,
`/phase-diagrams/sgte/{file}`, `/hulls/dft-mp/{A}/{A}-{B}.json`, `/hulls/dft-oqmd/...`, `/hulls/calphad-sgte/...`
`/phases/registry.json` (compact index), `/phases/systems/{SYS}.json` (full phase records),
`/phases/structure_types.json` and, on `https://orchid-project.org/data`, `/catalog.json` and `/databases/{collection}/{file}`.

## Tests

```bash
.venv/bin/pip install -e '.[mcp,test]'
ORCHID_TEST_DATA_DIR=~/projects/orchid-data ORCHID_TEST_SITE_DATA_DIR=~/projects/orchid-project.org/data .venv/bin/python -m pytest
```

Tests read local data only; network access fails the run. The stdio smoke test runs against both the built-in
fallback and the SDK server (skipped if `mcp` is not installed).

## Licence

Code: MIT (`LICENSE`). Data: derived data CC BY 4.0 — cite ORCHID (release / Zenodo DOI, `CITATION.cff`) **and** the
original sources of each value (paper, CALPHAD assessment, Materials Project, OQMD). Third-party data and TDB files
keep their own licences (`orchid://licence`). The tools return one-sentence quotes from source papers under fair
use, solely so each value can be verified; the papers themselves are not redistributed.
