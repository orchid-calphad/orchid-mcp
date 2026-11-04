"""DataSource: local reads with path-traversal refusal, and remote fetch with an on-disk cache."""

import io
import json
import tempfile
import threading
import urllib.request
from pathlib import Path
from unittest import TestCase, mock

import orchid_mcp.datasource as datasource_module
from orchid_mcp.datasource import DataSource, DataSourceError, NotFound


class _FakeResponse(io.BytesIO):
    """In-memory stand-in for the response object returned by ``urlopen``."""

    def __enter__(self):
        """Return the response itself so it can be used as a context manager."""
        return self

    def __exit__(self, *a):
        """Do not suppress exceptions raised inside the ``with`` block."""
        return False


class TestDataSource(TestCase):
    """Tests for DataSource reads from a local directory and from a remote base URL."""

    def setUp(self) -> None:
        """Create a temporary data root that is removed after each test."""
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        """Delete the temporary data root created in setUp."""
        self._tmp.cleanup()

    def test_local_and_traversal(self):
        """Local JSON files are read and existence-checked, while traversal outside the root is refused."""
        (self.tmp_path / "a").mkdir()
        (self.tmp_path / "a" / "x.json").write_text('{"k": 1}', encoding="utf-8")
        ds = DataSource(self.tmp_path)
        assert ds.read_json("a/x.json") == {"k": 1}
        assert ds.exists("a/x.json") and not ds.exists("a/y.json")
        with self.assertRaises(DataSourceError):
            ds.read_bytes("../etc/passwd")
        with self.assertRaises(NotFound):
            ds.read_bytes("a/y.json")

    def test_remote_fetch_and_cache(self):
        """A remote file is fetched once, then served from the on-disk cache when offline."""
        calls = []

        def fake_urlopen(req, timeout=None):
            """Record the requested URL and return a canned JSON body."""
            calls.append(req.full_url)
            return _FakeResponse(json.dumps({"ok": True}).encode())

        with mock.patch.object(urllib.request, "urlopen", new=fake_urlopen):
            ds = DataSource(base_url="https://example.invalid/data", cache_dir=self.tmp_path)
            assert ds.read_json("literature/index.json") == {"ok": True}
            assert calls == ["https://example.invalid/data/literature/index.json"]
            ds2 = DataSource(base_url="https://example.invalid/data", cache_dir=self.tmp_path, offline=True)
            assert ds2.read_json("literature/index.json") == {"ok": True}  # served from the on-disk cache
            assert len(calls) == 1
            with self.assertRaises(NotFound):
                ds2.read_bytes("literature/other.json")

    def test_read_json_wraps_undecodable_bytes(self):
        """BL-016: a file that is not valid UTF-8 raises DataSourceError, not UnicodeDecodeError."""
        (self.tmp_path / "bad.json").write_bytes(b"\xff\xfe\x00not utf-8 \x80")
        with self.assertRaises(DataSourceError):
            DataSource(self.tmp_path).read_json("bad.json")

    def test_read_json_wraps_recursion_error(self):
        """BL-016: a document nested past the parser's limit raises DataSourceError, not RecursionError.

        Whether real deep nesting overflows depends on the Python version, so the parser error is simulated.
        """

        def too_deep(*_a, **_k):
            """Stand in for json.loads on a pathologically nested document."""
            raise RecursionError("maximum recursion depth exceeded")

        (self.tmp_path / "deep.json").write_text("[[]]", encoding="utf-8")
        with mock.patch.object(datasource_module.json, "loads", too_deep), self.assertRaises(DataSourceError):
            DataSource(self.tmp_path).read_json("deep.json")

    def _fake_remote(self, payload=b'{"ok": true}'):
        """Patch urlopen to return ``payload`` for every URL, for the duration of the test."""
        patcher = mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(payload))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_concurrent_cache_writes_do_not_collide(self):
        """BL-004: two writers of the same cache file each use their own temporary file.

        Both writers are held at the rename step until both have written, which is the interleaving that made the
        second rename fail when the temporary name was fixed.
        """
        self._fake_remote()
        barrier = threading.Barrier(2, timeout=5)
        real_replace = Path.replace
        waited = threading.local()

        def held_replace(path, target):
            """Wait for the other writer before its first rename attempt (later attempts are retries)."""
            if not getattr(waited, "done", False):
                waited.done = True
                barrier.wait()
            return real_replace(path, target)

        errors, results = [], []

        def fetch():
            """Fetch a.json through its own DataSource and record the result or the error."""
            try:
                results.append(DataSource(base_url="https://example.invalid/d", cache_dir=self.tmp_path).read_json("a.json"))
            except Exception as exc:  # noqa: BLE001 - the test reports any failure
                errors.append(exc)

        with mock.patch.object(Path, "replace", held_replace):
            threads = [threading.Thread(target=fetch) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        assert errors == [] and results == [{"ok": True}, {"ok": True}]
        assert [p.name for p in self.tmp_path.rglob("*") if p.is_file()] == ["a.json"]  # no temporary files left

    def test_cache_write_retries_then_gives_up_quietly(self):
        """BL-004: a rename blocked for a moment is retried, and a cache that cannot be written does not fail a read.

        On Windows the rename fails while another process has the target open.
        """
        self._fake_remote()
        real_replace = Path.replace
        failures = {"left": 2}

        def flaky_replace(path, target):
            """Fail twice with PermissionError, then rename for real."""
            if failures["left"]:
                failures["left"] -= 1
                raise PermissionError("target in use")
            return real_replace(path, target)

        with mock.patch.object(Path, "replace", flaky_replace):
            ds = DataSource(base_url="https://example.invalid/d", cache_dir=self.tmp_path)
            assert ds.read_json("a.json") == {"ok": True}
        assert [p.name for p in self.tmp_path.rglob("*") if p.is_file()] == ["a.json"]

        def blocked(path, target):
            """Always fail, like a read-only cache directory."""
            raise PermissionError("read-only cache")

        with mock.patch.object(Path, "replace", blocked):
            ds = DataSource(base_url="https://example.invalid/d", cache_dir=self.tmp_path / "ro")
            assert ds.read_bytes("b.json") == b'{"ok": true}'
        assert [p for p in (self.tmp_path / "ro").rglob("*") if p.is_file()] == []  # nothing cached, nothing left behind
