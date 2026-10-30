# Contributing to orchid-mcp

Thank you for helping. This repository is the MCP server and the OpenAPI description of the ORCHID data.

- **A wrong value** is a data issue, not a server bug: use the **report** link of the value (`report_issue_url` in
  every tool result) or the form
  [Report an error in a value](https://github.com/orchid-calphad/orchid-data/issues/new?template=report-value-error.yml)
  in orchid-data. Data contributions also go there
  ([CONTRIBUTING.md](https://github.com/orchid-calphad/orchid-data/blob/main/CONTRIBUTING.md)).
- **A bug or a feature request** for the server: open an issue here with the bug form (tool name, arguments, what
  you got, what you expected, the orchid-mcp version).
- **Code**: fork, branch, and open a pull request. Keep the package free of runtime dependencies (the `mcp` SDK is
  optional), keep every value's provenance (citation, DOI, page, quote or table locator, evidence class) in tool
  results, never average across evidence classes, and never make the server post or send anything on the user's
  behalf. Add tests; run them with local data:

```bash
.venv/bin/pip install -e '.[mcp,test]'
ORCHID_TEST_DATA_DIR=~/projects/orchid-data ORCHID_TEST_SITE_DATA_DIR=~/projects/orchid-project.org/data .venv/bin/python -m pytest
```

  On Windows use `.venv\Scripts\python` and clone the data repositories with `git clone -c core.autocrlf=false`:
  the served `.tdb` files are checksummed byte for byte, and CRLF conversion changes the hash.

  Without local data the data-dependent tests are skipped (CI runs the rest); tests must never use the network.

## Licence

Code is under the MIT licence of this repository (`LICENSE`). Sign off every commit (`git commit -s`): the `Signed-off-by` line certifies the
[Developer Certificate of Origin](https://developercertificate.org/), that you wrote the change or have the right to
submit it under the licence of this repository. CI checks the sign-off on every pull request.

## Maintainers

R. Arróyave (Texas A&M University, lead), J. Saal (Citrine Informatics) and D. Shin (Oak Ridge National Laboratory)
maintain all ORCHID repositories; how decisions are made is in
[GOVERNANCE.md](https://github.com/orchid-calphad/orchid-data/blob/main/GOVERNANCE.md). Everyone follows the
[Code of Conduct](CODE_OF_CONDUCT.md) (Contributor Covenant 2.1; reports to conduct@orchid-project.org).
