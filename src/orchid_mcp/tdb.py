"""TDB catalog: which thermodynamic database files exist for a system, their licences, and bundled file contents."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from .constants import public_site_data_url
from .core import check_fields, wanted_elements
from .datasource import DataSourceError
from .errors import OrchidError

if TYPE_CHECKING:
    from .core import OrchidCore

tdb_max_bytes = 2_000_000  # larger bundled files are not returned inline
tdb_summary_keys = (
    "name",
    "elements",
    "category",
    "tier",
    "license",
    "license_status",
    "source_url",
    "orchid_download_url",
    "sha256",
)
tdb_access = {
    "bundled": "download from ORCHID (licence permits redistribution); cite the original assessment",
    "fetched": "not redistributed by ORCHID: download from the original source (download_url / source_url)",
    "manual": "not redistributed: obtain from the original publisher / landing page (source_url)",
}
tdb_tiers = {
    "bundled": "redistributable; served by ORCHID",
    "fetched": "public upstream download, not redistributed here",
    "manual": "restricted / landing page only",
}


class TdbTools:
    """TDB catalog queries.

    Args:
        core (OrchidCore): Shared data sources and response envelope.
    """

    def __init__(self, core: OrchidCore) -> None:
        """Keep a reference to the shared core."""
        self._core = core

    def _catalog(self) -> list[dict[str, Any]]:
        """Catalog entries from the website data (``catalog.json``, a list or a ``records`` object).

        Returns:
            The entries.
        """
        cat = self._core.site.read_json("catalog.json")
        return cat if isinstance(cat, list) else cat.get("records", [])

    def _known_sha(self) -> dict[str, str]:
        """SHA-256 of each TDB file as recorded in the phase-diagram index.

        Returns:
            File name to digest.
        """
        out = {}
        for rows in self._core.pd_index().get("systems", {}).values():
            for r in rows:
                if r.get("tdb") and r.get("sha256"):
                    out[r["tdb"]] = r["sha256"]
        return out

    @staticmethod
    def _catalog_entry(e: dict[str, Any], sha: dict[str, str]) -> dict[str, Any]:
        """Catalog entry with its redistribution flag, download URL, digest and access note.

        Args:
            e (dict[str, Any]): Raw catalog entry.
            sha (dict[str, str]): Known digests by file name.

        Returns:
            A copy of the entry with the ORCHID-derived fields added.
        """
        out = dict(e)
        bundled = e.get("tier") == "bundled" and e.get("license_status") == "confirmed" and e.get("local_path")
        out["redistributable_here"] = bool(bundled)
        out["orchid_download_url"] = f"{public_site_data_url}{e['local_path']}" if bundled else None
        out["sha256"] = sha.get(e.get("name", "")) if bundled else None
        out["access"] = tdb_access.get(e.get("tier"), "see source_url")
        return out

    def list_tdb_files(
        self, *, elements: list[str] | None, exact: bool, tier: str | None, limit: int, offset: int, fields: str
    ) -> dict[str, Any]:
        """TDB catalog entries whose system contains all ``elements`` (``exact``: exactly them).

        Args:
            elements (list[str] | None): Element symbols that the file's system must contain.
            exact (bool): Require the file's element set to equal ``elements``.
            tier (str | None): ``bundled``, ``fetched`` or ``manual``; None keeps all tiers.
            limit (int): Page size (at least 1).
            offset (int): Rows to skip.
            fields (str): ``"summary"`` keeps the summary keys; ``"full"`` returns whole entries.

        Returns:
            The page of entries with paging fields and the tier legend.
        """
        fields = check_fields(fields)
        want = wanted_elements(elements)
        sha = self._known_sha()
        rows = []
        for e in self._catalog():
            els = {t.strip().upper() for t in str(e.get("elements", "")).split("-") if t.strip()}
            if want and (els != want if exact else not want <= els):
                continue
            if tier and e.get("tier") != tier:
                continue
            rows.append(self._catalog_entry(e, sha))
        rows.sort(key=lambda r: (r.get("n_elements") or 0, r.get("name") or ""))
        page = rows[max(0, offset) : max(0, offset) + max(1, limit)]
        return self._core.wrap(
            {
                "query": {"elements": sorted(want), "exact": exact, "tier": tier},
                "fields": fields,
                "total": len(rows),
                "offset": offset,
                "truncated": offset + max(1, limit) < len(rows),
                "files": [{k: r.get(k) for k in tdb_summary_keys} if fields == "summary" else r for r in page],
                "tiers": tdb_tiers,
            }
        )

    def get_tdb(self, *, file: str, include_content: bool, max_bytes: int) -> dict[str, Any]:
        """One TDB catalog entry; the file content only for bundled files with a confirmed redistributable licence.

        Args:
            file (str): File name (with or without ``.tdb``), case-insensitive; a path prefix is ignored.
            include_content (bool): Return the file text (latin-1, bytes preserved) when it is served.
            max_bytes (int): Files larger than this many bytes are withheld from the content.

        Returns:
            The entry, its SHA-256 of the served bytes and a size, and the content or the reason it is withheld.

        Raises:
            OrchidError: When the file is not in the catalog or its local path is unexpected.
        """
        entry = self._catalog_entry(self._find(file), self._known_sha())
        out: dict[str, Any] = {"entry": entry, "content": None}
        if not entry["redistributable_here"]:
            out["content_withheld"] = (
                f"tier '{entry.get('tier')}' / licence status '{entry.get('license_status')}': ORCHID does not "
                "redistribute this file. Obtain it from the original source."
            )
            return self._core.wrap(out)
        rel = entry["local_path"]
        if not isinstance(rel, str) or not rel.startswith("databases/"):
            raise OrchidError("catalog entry has an unexpected local_path")
        try:
            data = self._core.site.read_bytes(rel)
        except DataSourceError as exc:
            out["content_error"] = str(exc)
            return self._core.wrap(out)
        digest = hashlib.sha256(data).hexdigest()
        out["sha256_of_served_bytes"] = digest
        if entry.get("sha256"):
            out["sha256_matches_phase_diagram_record"] = digest == entry["sha256"]
        entry["sha256"] = entry.get("sha256") or digest
        out["size_bytes"] = len(data)
        if include_content:
            self._add_content(out, data, max_bytes)
        out["licence_note"] = (
            f"{entry.get('license')}: cite the original assessment ({entry.get('source_url')}) and the collection"
        )
        return self._core.wrap(out)

    @staticmethod
    def _add_content(out: dict[str, Any], data: bytes, max_bytes: int) -> None:
        """Add the file text to a response, or the reason it is withheld.

        Args:
            out (dict[str, Any]): Response under construction, updated in place.
            data (bytes): The served file bytes.
            max_bytes (int): Size limit in bytes.
        """
        if len(data) > max_bytes:
            out["content_withheld"] = f"file larger than max_bytes={max_bytes}; download it from orchid_download_url"
            return
        out["content"] = data.decode("latin-1") if b"\x00" not in data else None
        out["content_encoding"] = "latin-1 (bytes preserved 1:1)"

    def _find(self, file: str) -> dict[str, Any]:
        """Catalog entry matching a file name: exact name first, then name without extension.

        Args:
            file (str): File name or path; only the last path segment is used.

        Returns:
            The first matching raw entry.

        Raises:
            OrchidError: When nothing matches.
        """
        name = file.strip().rsplit("/", 1)[-1].lower()
        catalog = self._catalog()
        hits = [e for e in catalog if (e.get("name") or "").lower() == name]
        if not hits:
            hits = [e for e in catalog if (e.get("name") or "").lower().rsplit(".", 1)[0] == name]
        if not hits:
            raise OrchidError(f"TDB {file!r} not in the ORCHID catalog; use list_tdb_files")
        return hits[0]
