"""The static-API description parses as YAML, its $refs resolve, and its paths exist in the local data."""

import re
from pathlib import Path

import pytest

from conftest import DATA_DIR, SITE_DIR

SPEC = Path(__file__).resolve().parents[1] / "openapi" / "openapi.yaml"


def _load():
    text = SPEC.read_text(encoding="utf-8")
    assert "\t" not in text, "YAML must not contain tabs"
    try:
        import yaml
    except ImportError:  # stdlib-only fallback: structural sanity check
        assert text.startswith("openapi: 3.1.0") and "\npaths:\n" in text and "\ncomponents:\n" in text
        pytest.skip("PyYAML not installed; only the stdlib sanity check ran")
    return yaml.safe_load(text)


def _refs(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref":
                yield v
            else:
                yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


def test_openapi_parses_and_refs_resolve():
    spec = _load()
    assert spec["openapi"].startswith("3.1")
    for ref in set(_refs(spec)):
        assert ref.startswith("#/")
        node = spec
        for part in ref[2:].split("/"):
            node = node[part]
    op_ids = [op["operationId"] for p in spec["paths"].values() for m, op in p.items() if m == "get"]
    assert len(op_ids) == len(set(op_ids))
    for path, item in spec["paths"].items():
        assert set(item) <= {"get", "servers"}, f"{path} must be read-only"
        names = set(re.findall(r"{(\w+)}", path))
        declared = set()
        for prm in item["get"].get("parameters", []):
            if "$ref" in prm:
                prm = spec["components"]["parameters"][prm["$ref"].rsplit("/", 1)[-1]]
            declared.add(prm["name"])
        assert names == declared, path


EXAMPLES = {
    "A": "AL", "B": "NI", "file": None, "layer": "dft-mp", "assessment": "AlNi-05Mie", "collection": "sgte-binary",
}


def _bib_example_file():
    import hashlib
    return "p" + hashlib.sha256(b"doi:10.1016/j.calphad.2005.02.002").hexdigest()[:16] + ".json"


def test_documented_paths_exist_locally():
    spec = _load()
    if not DATA_DIR.is_dir() or not SITE_DIR.is_dir():
        pytest.skip("local data not available")
    for path, item in spec["paths"].items():
        on_site = "servers" in item and "orchid-project.org/data" in item["servers"][0]["url"]
        concrete = path
        for name in re.findall(r"{(\w+)}", path):
            val = EXAMPLES[name]
            if name == "file":
                val = {"/literature/systems/{file}": "AL-NI.json", "/phase-diagrams/sgte/{file}": "AlNi-05Mie.json",
                       "/databases/{collection}/{file}": "AlNi-05Mie.tdb",
                       "/phases/systems/{file}": "AL-NI.json", "/bibliography/systems/{file}": "AL-NI.json",
                       "/bibliography/papers/{file}": _bib_example_file()}[path]
            concrete = concrete.replace("{%s}" % name, val)
        root = SITE_DIR if on_site else DATA_DIR
        assert (root / concrete.lstrip("/")).is_file(), f"{path} -> {concrete} missing in {root}"
