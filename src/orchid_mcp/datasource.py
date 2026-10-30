"""Read-only access to ORCHID data files, from a local directory or over HTTPS.

The same relative paths are served locally (a checkout of ``orchid-data`` or of
the website's ``data/`` folder) and online (``https://data.orchid-project.org/``
and ``https://orchid-project.org/data/``). Remote reads use ``urllib`` and an
on-disk cache, so a server that once ran online keeps working offline.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_DATA_URL = "https://data.orchid-project.org/"
DEFAULT_SITE_DATA_URL = "https://orchid-project.org/data/"
USER_AGENT = "orchid-mcp/0.1 (+https://orchid-project.org)"


class DataSourceError(RuntimeError):
    """Raised when a file cannot be read from the configured source."""


class NotFound(DataSourceError):
    """Raised when a file does not exist in the source."""


def _clean_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise DataSourceError("empty path")
    norm = posixpath.normpath(path.lstrip("/"))
    if norm.startswith("..") or norm.startswith("/") or "\\" in norm or "\x00" in norm:
        raise DataSourceError(f"unsafe path: {path!r}")
    return norm


class DataSource:
    """Read files by relative path from ``local_dir`` or ``base_url``.

    If ``local_dir`` is given, it is used exclusively (no network). Otherwise
    files are fetched from ``base_url`` and cached under ``cache_dir`` for
    ``cache_ttl`` seconds (stale cache entries are still used when the network
    fails). Parsed JSON is memoised in memory.
    """

    def __init__(
        self,
        local_dir: str | os.PathLike[str] | None = None,
        base_url: str = DEFAULT_DATA_URL,
        cache_dir: str | os.PathLike[str] | None = None,
        cache_ttl: float = 86400.0,
        timeout: float = 30.0,
        offline: bool = False,
    ) -> None:
        self.local_dir = Path(local_dir).expanduser().resolve() if local_dir else None
        if self.local_dir is not None and not self.local_dir.is_dir():
            raise DataSourceError(f"local data directory not found: {self.local_dir}")
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        default_cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "orchid-mcp"
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir else default_cache
        self.cache_ttl = cache_ttl
        self.timeout = timeout
        self.offline = offline
        self._json: dict[str, Any] = {}

    # -- description -------------------------------------------------------
    @property
    def location(self) -> str:
        return str(self.local_dir) if self.local_dir else self.base_url

    def public_url(self, path: str, public_base: str | None = None) -> str:
        """Public URL of a file (``public_base`` overrides ``base_url``)."""
        base = public_base or self.base_url
        return (base if base.endswith("/") else base + "/") + _clean_path(path)

    # -- raw access --------------------------------------------------------
    def _cache_file(self, path: str) -> Path:
        key = hashlib.sha256(self.base_url.encode()).hexdigest()[:12]
        return self.cache_dir / key / path

    def exists(self, path: str) -> bool:
        path = _clean_path(path)
        if self.local_dir is not None:
            return (self.local_dir / path).is_file()
        try:
            self.read_bytes(path)
            return True
        except NotFound:
            return False

    def read_bytes(self, path: str) -> bytes:
        path = _clean_path(path)
        if self.local_dir is not None:
            f = self.local_dir / path
            try:
                f.resolve().relative_to(self.local_dir)
            except ValueError as exc:  # symlink escaping the data dir
                raise DataSourceError(f"path outside data dir: {path}") from exc
            if not f.is_file():
                raise NotFound(f"not found: {path} (in {self.local_dir})")
            return f.read_bytes()
        cached = self._cache_file(path)
        fresh = cached.is_file() and (time.time() - cached.stat().st_mtime) < self.cache_ttl
        if fresh or (self.offline and cached.is_file()):
            return cached.read_bytes()
        if self.offline:
            raise NotFound(f"offline and not cached: {path}")
        url = self.base_url + path
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (https base URL)
                data = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFound(f"not found: {url}") from exc
            if cached.is_file():
                return cached.read_bytes()
            raise DataSourceError(f"HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if cached.is_file():
                return cached.read_bytes()
            raise DataSourceError(f"cannot fetch {url}: {exc}") from exc
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(cached.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(cached)
        return data

    def read_text(self, path: str) -> str:
        return self.read_bytes(path).decode("utf-8", errors="replace")

    def read_json(self, path: str) -> Any:
        path = _clean_path(path)
        if path not in self._json:
            try:
                self._json[path] = json.loads(self.read_bytes(path))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise DataSourceError(f"invalid JSON in {path}: {exc}") from exc
            except RecursionError as exc:  # pathologically nested document
                raise DataSourceError(f"JSON nested too deeply in {path}") from exc
        return self._json[path]


def sources_from_env() -> tuple[DataSource, DataSource]:
    """Build the (data, site) sources from environment variables.

    ``ORCHID_DATA_DIR``       local copy of data.orchid-project.org (orchid-data checkout)
    ``ORCHID_DATA_URL``       default https://data.orchid-project.org/
    ``ORCHID_SITE_DATA_DIR``  local copy of orchid-project.org/data (catalog.json, databases/)
    ``ORCHID_SITE_DATA_URL``  default https://orchid-project.org/data/
    ``ORCHID_CACHE_DIR``      on-disk cache for remote reads (default ~/.cache/orchid-mcp)
    ``ORCHID_CACHE_TTL``      seconds (default 86400)
    ``ORCHID_OFFLINE``        "1": never touch the network (cache only)
    """
    env = os.environ
    common = dict(
        cache_dir=env.get("ORCHID_CACHE_DIR") or None,
        cache_ttl=float(env.get("ORCHID_CACHE_TTL", "86400")),
        offline=env.get("ORCHID_OFFLINE", "") in ("1", "true", "yes"),
    )
    data = DataSource(env.get("ORCHID_DATA_DIR") or None, env.get("ORCHID_DATA_URL", DEFAULT_DATA_URL), **common)
    site = DataSource(
        env.get("ORCHID_SITE_DATA_DIR") or None, env.get("ORCHID_SITE_DATA_URL", DEFAULT_SITE_DATA_URL), **common
    )
    return data, site
