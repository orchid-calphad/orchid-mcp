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


def _fake_remote(monkeypatch, payload=b'{"ok": true}'):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Resp(payload))


def test_concurrent_cache_writes_do_not_collide(tmp_path, monkeypatch):
    """BL-004: two writers of the same cache file each use their own temporary file.

    Both writers are held at the rename step until both have written, which is the interleaving that made the
    second rename fail when the temporary name was fixed."""
    import pathlib
    import threading

    _fake_remote(monkeypatch)
    barrier = threading.Barrier(2, timeout=5)
    real_replace = pathlib.Path.replace

    def held_replace(self, target):
        barrier.wait()
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", held_replace)
    errors, results = [], []

    def fetch():
        try:
            results.append(DataSource(base_url="https://example.invalid/d", cache_dir=tmp_path).read_json("a.json"))
        except Exception as exc:  # noqa: BLE001 (the test reports any failure)
            errors.append(exc)

    threads = [threading.Thread(target=fetch) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and results == [{"ok": True}, {"ok": True}]
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["a.json"]  # no temporary files left


def test_cache_write_retries_then_gives_up_quietly(tmp_path, monkeypatch):
    """BL-004: a rename blocked for a moment (Windows: the target is open in another process) is retried; a cache
    that cannot be written at all does not fail a read whose data was fetched."""
    import pathlib

    _fake_remote(monkeypatch)
    real_replace = pathlib.Path.replace
    failures = {"left": 2}

    def flaky_replace(self, target):
        if failures["left"]:
            failures["left"] -= 1
            raise PermissionError("target in use")
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", flaky_replace)
    ds = DataSource(base_url="https://example.invalid/d", cache_dir=tmp_path)
    assert ds.read_json("a.json") == {"ok": True}
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["a.json"]

    def blocked(self, target):
        raise PermissionError("read-only cache")

    monkeypatch.setattr(pathlib.Path, "replace", blocked)
    ds = DataSource(base_url="https://example.invalid/d", cache_dir=tmp_path / "ro")
    assert ds.read_bytes("b.json") == b'{"ok": true}'
    assert [p for p in (tmp_path / "ro").rglob("*") if p.is_file()] == []  # nothing cached, nothing left behind
