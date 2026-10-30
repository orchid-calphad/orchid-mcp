import io
import json
import urllib.request

import pytest

from orchid_mcp.datasource import DataSource, DataSourceError, NotFound


def test_local_and_traversal(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.json").write_text('{"k": 1}')
    ds = DataSource(tmp_path)
    assert ds.read_json("a/x.json") == {"k": 1}
    assert ds.exists("a/x.json") and not ds.exists("a/y.json")
    with pytest.raises(DataSourceError):
        ds.read_bytes("../etc/passwd")
    with pytest.raises(NotFound):
        ds.read_bytes("a/y.json")


def test_remote_fetch_and_cache(tmp_path, monkeypatch):
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return Resp(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ds = DataSource(base_url="https://example.invalid/data", cache_dir=tmp_path)
    assert ds.read_json("literature/index.json") == {"ok": True}
    assert calls == ["https://example.invalid/data/literature/index.json"]
    ds2 = DataSource(base_url="https://example.invalid/data", cache_dir=tmp_path, offline=True)
    assert ds2.read_json("literature/index.json") == {"ok": True}  # served from the on-disk cache
    assert len(calls) == 1
    with pytest.raises(NotFound):
        ds2.read_bytes("literature/other.json")



def test_read_json_wraps_undecodable_bytes(tmp_path):
    """BL-016: a file that is not valid UTF-8 raises DataSourceError, not UnicodeDecodeError."""
    (tmp_path / "bad.json").write_bytes(b"\xff\xfe\x00not utf-8 \x80")
    with pytest.raises(DataSourceError):
        DataSource(tmp_path).read_json("bad.json")


def test_read_json_wraps_recursion_error(tmp_path, monkeypatch):
    """BL-016: a document nested past the parser's limit raises DataSourceError, not RecursionError.

    Whether real deep nesting overflows depends on the Python version, so the parser error is simulated."""
    import orchid_mcp.datasource as dsmod

    def too_deep(*_a, **_k):
        raise RecursionError("maximum recursion depth exceeded")

    (tmp_path / "deep.json").write_text("[[]]")
    monkeypatch.setattr(dsmod.json, "loads", too_deep)
    with pytest.raises(DataSourceError):
        DataSource(tmp_path).read_json("deep.json")
