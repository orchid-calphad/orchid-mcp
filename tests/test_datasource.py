"""DataSource: local reads with path-traversal refusal, and remote fetch with an on-disk cache."""

import io
import json
import tempfile
import urllib.request
from pathlib import Path
from unittest import TestCase, mock

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
