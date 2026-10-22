# orchid-mcp

An [MCP](https://modelcontextprotocol.io) server for **ORCHID** (Open Repository for CALPHAD Hypotheses,
Intelligence, and Discovery): literature thermodynamic and phase-equilibrium values, calculated binary phase
diagrams, Materials Project / OQMD formation energies and the TDB catalog — every value returned with its
citation, DOI, page, quote (or table locator), evidence class and flags. Also: an OpenAPI description of the static
JSON files at `data.orchid-project.org` (`openapi/openapi.yaml`).

## Install

```bash
pip install -e '.[mcp]'        # with the official MCP Python SDK (recommended)
pip install -e .               # stdlib only: built-in stdio fallback server, no HTTP mode
```

The package itself has no runtime dependencies. If the `mcp` SDK is installed, the server runs on FastMCP; otherwise
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
| `search_systems(elements, text, exact, limit)` | systems with value counts by evidence class / property group, calculated assessments, viewer URLs |
| `get_system_data(system, property_group, evidence_class, accepted_only=True, limit=200, offset, property, compact)` | literature values with full records and an expanded `source` (citation, DOI, page, quote or table locator) |
| `get_invariant_reactions(system, source="literature"\|"calculated"\|"both", assessment)` | literature invariants and calculated invariants per SGTE assessment, with exact phase-set matches and dT |
| `get_formation_enthalpy(formula)` | literature HM_FORM (kJ/mol-atom) + Materials Project and OQMD entries at that composition + per-class summaries |
| `get_phase_diagram(system, assessment, x, T)` | TDB file, SHA-256, assessment citation/DOI, invariants, region summary, viewer URL; stable phases at (x, T) from the stored grid |
| `list_tdb_files(elements, exact, tier)` / `get_tdb(file)` | catalog entry, licence, tier, SHA-256, download URL; file content **only** for bundled, confirmed-licence files |
| `get_paper(doi)` | bibliographic record, systems, number of values, experimental-context items with quotes |
| `compare_to_dft(system)` | literature vs MP vs OQMD formation enthalpies with residuals and the DFT hull vertices |

Resources: `orchid://schema` (SCHEMA.md), `orchid://dataset-card` (README + CITATION.cff), `orchid://licence`
(LICENSES.md + NOTICE.md). Prompt: `cite_orchid` (how to cite ORCHID and the original sources).

Rules the tools keep: evidence classes (experimental, model, dft, unspecified) are never mixed or averaged; MP and
OQMD use different energy references and are never averaged; each calculated diagram is one assessment;
`uncertainty.basis: stated_default` is a placeholder, not a measurement; `fetched` and `manual` TDB files are never
served (only their original-source links).

## Example questions

- "Which Al–Ni invariant reactions have been measured, and how do the SGTE assessments reproduce them?"
- "What is the formation enthalpy of Al3Ni? Compare calorimetry with Materials Project and OQMD."
- "Which phases are stable in Ni–Ti at x(Ti)=0.5 and 1200 K according to NiTi-04Tok?"
- "List the Cu–Zr TDB files I can download and their licences."
- "What experimental conditions did doi:10.1016/j.tca.2010.10.005 use?"

## Static JSON API

`openapi/openapi.yaml` (OpenAPI 3.1) documents the read-only file layout: `GET /literature/index.json`,
`/literature/systems/{SYS}.json`, `/literature/papers.json`, `/phase-diagrams/index.json`,
`/phase-diagrams/sgte/{file}`, `/hulls/dft-mp/{A}/{A}-{B}.json`, `/hulls/dft-oqmd/...`, `/hulls/calphad-sgte/...`
and, on `https://orchid-project.org/data`, `/catalog.json` and `/databases/{collection}/{file}`.

## Tests

```bash
pip install pytest pyyaml
ORCHID_TEST_DATA_DIR=~/projects/orchid-data ORCHID_TEST_SITE_DATA_DIR=~/projects/orchid-project.org/data pytest
```

Tests read local data only; network access fails the run.

## Licence

Code: MIT (`LICENSE`). Data: derived data CC BY 4.0 — cite ORCHID (release / Zenodo DOI, `CITATION.cff`) **and** the
original sources of each value (paper, CALPHAD assessment, Materials Project, OQMD). Third-party data and TDB files
keep their own licences (`orchid://licence`). The tools return one-sentence quotes from source papers under fair
use, solely so each value can be verified; the papers themselves are not redistributed.
