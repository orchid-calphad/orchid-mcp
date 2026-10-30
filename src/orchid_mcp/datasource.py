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

default_data_url = "https://data.orchid-project.org/"
default_site_data_url = "https://orchid-project.org/data/"
user_agent = "orchid-mcp/0.1 (+https://orchid-project.org)"
default_cache_ttl = 86400.0  # seconds a remote file stays fresh in the cache (one day)
default_timeout = 30.0  # seconds for one HTTP request


class DataSourceError(RuntimeError):
    """Raised when a file cannot be read from the configured source."""


class NotFound(DataSourceError):  # noqa: N818 (public name kept; callers catch it)
    """Raised when a file does not exist in the source."""


def _clean_path(path: str) -> str:
    """Normalise a relative data path and reject anything that could leave the data root.

    Args:
        path (str): Relative path such as ``"literature/index.json"``.

    Returns:
        The normalised path without a leading slash.

    Raises:
        DataSourceError: When the path is empty, escapes the root, or contains a backslash or NUL byte.
    """
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

    Args:
        local_dir (str | os.PathLike[str] | None): Local data directory, used exclusively when given.
        base_url (str): Remote base URL; a trailing slash is added when missing.
        cache_dir (str | os.PathLike[str] | None): Cache directory for remote reads.
        cache_ttl (float): Seconds a cached remote file is considered fresh.
        timeout (float): Seconds to wait for one HTTP request.
        offline (bool): Never touch the network; only cached files are served.

    Raises:
        DataSourceError: When ``local_dir`` is given but is not a directory.
    """

    def __init__(  # noqa: PLR0917 (public signature kept)
        self,
        local_dir: str | os.PathLike[str] | None = None,
        base_url: str = default_data_url,
        cache_dir: str | os.PathLike[str] | None = None,
        cache_ttl: float = default_cache_ttl,
        timeout: float = default_timeout,
        offline: bool = False,
    ) -> None:
        """Validate the local directory and store the remote and cache settings."""
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
        """Where the files come from: the local directory, or the base URL."""
        return str(self.local_dir) if self.local_dir else self.base_url

    def public_url(self, path: str, public_base: str | None = None) -> str:
        """Public URL of a file.

        Args:
            path (str): Relative data path.
            public_base (str | None): Base URL to use instead of ``base_url``.

        Returns:
            The URL of the file.
        """
        base = public_base or self.base_url
        return (base if base.endswith("/") else base + "/") + _clean_path(path)

    # -- raw access --------------------------------------------------------
    def _cache_file(self, path: str) -> Path:
        """Cache location of a remote file, keyed by a hash of the base URL.

        Args:
            path (str): Normalised relative path.

        Returns:
            The cache file path.
        """
        key = hashlib.sha256(self.base_url.encode()).hexdigest()[:12]
        return self.cache_dir / key / path

    def exists(self, path: str) -> bool:
        """Whether a file can be read from the source.

        Args:
            path (str): Relative data path.

        Returns:
            True when the file exists.
        """
        path = _clean_path(path)
        if self.local_dir is not None:
            return (self.local_dir / path).is_file()
        try:
            self.read_bytes(path)
            return True
        except NotFound:
            return False

    def read_bytes(self, path: str) -> bytes:
        """Raw bytes of a file.

        Args:
            path (str): Relative data path.

        Returns:
            The file content.

        Raises:
            NotFound: When the file does not exist (or, offline, is not cached).
            DataSourceError: When the path is unsafe or the network fails without a cached copy.
        """
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
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
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
        """Text of a file, decoded as UTF-8 with replacement of invalid bytes.

        Args:
            path (str): Relative data path.

        Returns:
            The decoded text.
        """
        return self.read_bytes(path).decode("utf-8", errors="replace")

    def read_json(self, path: str) -> Any:
        """Parsed JSON of a file, memoised for the life of the instance.

        Args:
            path (str): Relative data path.

        Returns:
            The parsed value.

        Raises:
            DataSourceError: When the file is not valid JSON.
        """
        path = _clean_path(path)
        if path not in self._json:
            try:
                self._json[path] = json.loads(self.read_bytes(path))
            except json.JSONDecodeError as exc:
                raise DataSourceError(f"invalid JSON in {path}: {exc}") from exc
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

    Returns:
        The data source and the site source.
    """
    env = os.environ
    common = {
        "cache_dir": env.get("ORCHID_CACHE_DIR") or None,
        "cache_ttl": float(env.get("ORCHID_CACHE_TTL", "86400")),
        "offline": env.get("ORCHID_OFFLINE", "") in ("1", "true", "yes"),
    }
    data = DataSource(env.get("ORCHID_DATA_DIR") or None, env.get("ORCHID_DATA_URL", default_data_url), **common)
    site = DataSource(env.get("ORCHID_SITE_DATA_DIR") or None, env.get("ORCHID_SITE_DATA_URL", default_site_data_url), **common)
    return data, site
