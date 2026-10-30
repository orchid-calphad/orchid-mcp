"""The static-API description parses as YAML, its $refs resolve, and its paths exist in the local data."""

import hashlib
import re
from pathlib import Path
from unittest import SkipTest, TestCase

from conftest import data_dir, site_dir

spec = Path(__file__).resolve().parents[1] / "openapi" / "openapi.yaml"


def _load():
    """Parse the OpenAPI document, running only a structural check when PyYAML is absent.

    Returns:
        The parsed OpenAPI document as a nested dict.

    Raises:
        SkipTest: If PyYAML is not installed, after the stdlib sanity check has passed.
    """
    text = spec.read_text(encoding="utf-8")
    assert "\t" not in text, "YAML must not contain tabs"
    try:
        import yaml  # noqa: PLC0415 - optional dependency, imported only when the document is parsed
    except ImportError:  # stdlib-only fallback: structural sanity check
        assert text.startswith("openapi: 3.1.0") and "\npaths:\n" in text and "\ncomponents:\n" in text
        raise SkipTest("PyYAML not installed; only the stdlib sanity check ran") from None
    return yaml.safe_load(text)


def _refs(node):
    """Yield every ``$ref`` string found anywhere inside a nested dict or list.

    Args:
        node (dict | list | object): The parsed YAML node to search.

    Yields:
        Each ``$ref`` value, in document order.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref":
                yield v
            else:
                yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


examples = {
    "A": "AL",
    "B": "NI",
    "file": None,
    "layer": "dft-mp",
    "assessment": "AlNi-05Mie",
    "collection": "sgte-binary",
}


def _bib_example_file():
    """Return the file name the static API uses for the example bibliography paper.

    Returns:
        ``p`` plus the first 16 hex digits of the sha256 of the example DOI, with a ``.json`` suffix.
    """
    return "p" + hashlib.sha256(b"doi:10.1016/j.calphad.2005.02.002").hexdigest()[:16] + ".json"


class TestOpenApiSpec(TestCase):
    """Tests for the structure and local-data consistency of the OpenAPI description."""

    def test_openapi_parses_and_refs_resolve(self):
        """The spec parses, every $ref resolves, operation ids are unique, and path parameters match declarations."""
        doc = _load()
        assert doc["openapi"].startswith("3.1")
        for ref in set(_refs(doc)):
            assert ref.startswith("#/")
            node = doc
            for part in ref[2:].split("/"):
                node = node[part]
        op_ids = [op["operationId"] for p in doc["paths"].values() for m, op in p.items() if m == "get"]
        assert len(op_ids) == len(set(op_ids))
        for path, item in doc["paths"].items():
            assert set(item) <= {"get", "servers"}, f"{path} must be read-only"
            names = set(re.findall(r"{(\w+)}", path))
            declared = set()
            for prm in item["get"].get("parameters", []):
                resolved = prm
                if "$ref" in prm:
                    resolved = doc["components"]["parameters"][prm["$ref"].rsplit("/", 1)[-1]]
                declared.add(resolved["name"])
            assert names == declared, path

    def test_documented_paths_exist_locally(self):
        """Every documented path, with its example parameters filled in, is a real file in the local data."""
        doc = _load()
        if not data_dir.is_dir() or not site_dir.is_dir():
            self.skipTest("local data not available")
        for path, item in doc["paths"].items():
            on_site = "servers" in item and "orchid-project.org/data" in item["servers"][0]["url"]
            concrete = path
            for name in re.findall(r"{(\w+)}", path):
                val = examples[name]
                if name == "file":
                    val = {
                        "/literature/systems/{file}": "AL-NI.json",
                        "/phase-diagrams/sgte/{file}": "AlNi-05Mie.json",
                        "/databases/{collection}/{file}": "AlNi-05Mie.tdb",
                        "/phases/systems/{file}": "AL-NI.json",
                        "/bibliography/systems/{file}": "AL-NI.json",
                        "/bibliography/papers/{file}": _bib_example_file(),
                    }[path]
                concrete = concrete.replace(f"{{{name}}}", val)
            base = site_dir if on_site else data_dir
            assert (base / concrete.lstrip("/")).is_file(), f"{path} -> {concrete} missing in {base}"
