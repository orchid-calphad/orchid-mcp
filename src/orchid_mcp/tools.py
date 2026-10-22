"""ORCHID query tools as plain functions over a :class:`DataSource`.

Every tool returns a JSON-serialisable ``dict``. Literature values keep their
full ORCHID record (evidence class, origin, flags, uncertainty basis, readers,
cited sources, ...); the compact per-system storage is expanded so that each
value carries its own citation, DOI, page and quote (``source``), its table or
passage locator, and its model link. Nothing is averaged across evidence
classes or across DFT sources.
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
from typing import Any, Iterable

from .chem import ChemError, composition_system, element, parse_formula, pretty_system, system_id
from .datasource import DataSource, DataSourceError, NotFound, sources_from_env

SITE_URL = "https://orchid-project.org/"
PUBLIC_DATA_URL = "https://data.orchid-project.org/"
PUBLIC_SITE_DATA_URL = "https://orchid-project.org/data/"
KJ_PER_MOL_PER_EV = 96.485  # same factor as literature/dft_parity.json
EVIDENCE_CLASSES = ("experimental", "model", "dft", "unspecified", "assessed_phase_diagram")
DFT_LAYERS = {
    "mp": {"dir": "hulls/dft-mp", "label": "Materials Project"},
    "oqmd": {"dir": "hulls/dft-oqmd", "label": "OQMD"},
}
_COMPACT_DROP = {"readers", "reader_tier", "tool_release", "value_stored", "unit_stored", "link_rule", "pid", "elements"}
FIELDS = ("summary", "full")
_TDB_SUMMARY = (
    "name", "elements", "category", "tier", "license", "license_status", "source_url", "orchid_download_url", "sha256",
)
QUOTE_MAX = 240
FIELDS_NOTE = (
    "fields='summary': id, property, value, unit, T, phases, evidence class, accepted, citation, DOI, page and a "
    "quote shortened to 240 characters (table values: the table locator). Use fields='full' (or the ids) for "
    "complete provenance: locator, origin, flags, uncertainty, conditions, cited sources and model links."
)


def _check_fields(fields: str) -> str:
    f = (fields or "summary").strip().lower()
    if f not in FIELDS:
        raise OrchidError(f"fields must be one of {FIELDS}")
    return f


def _short(text: Any, n: int = QUOTE_MAX) -> Any:
    if not isinstance(text, str) or len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "\u2026"


def _phase_labels(phases: Any) -> list[str]:
    out = []
    for ph in phases or []:
        if not isinstance(ph, dict):
            out.append(str(ph))
            continue
        lab, mp = ph.get("label"), ph.get("model_phase")
        out.append(f"{lab} ({mp})" if lab and mp and mp != lab else (lab or mp or "?"))
    return out


def summarize_record(r: dict[str, Any]) -> dict[str, Any]:
    """Short form of an expanded literature record (see FIELDS_NOTE)."""
    src = r.get("source") or {}
    loc = r.get("locator") or {}
    t = r.get("temperature_K")
    if t is None and r.get("unit") == "K":
        t = r.get("value")
    out: dict[str, Any] = {
        "id": r.get("id"),
        "property": r.get("property"),
        "value": r.get("value"),
        "unit": r.get("unit"),
        "T_K": t,
        "phases": _phase_labels(r.get("phases")),
    }
    rx = r.get("reaction")
    if isinstance(rx, dict) and (rx.get("type") or rx.get("printed")):
        out["reaction"] = ": ".join(str(x) for x in (rx.get("type"), rx.get("printed")) if x)
    out.update(
        {
            "evidence_class": r.get("evidence_class"),
            "accepted": bool((r.get("flags") or {}).get("extraction_accepted")),
            "citation": src.get("citation"),
            "doi": src.get("doi"),
            "page": src.get("page"),
            "quote": _short(src.get("quote")),
        }
    )
    if src.get("quote") is None and isinstance(loc, dict) and loc.get("label") is not None:
        kind = loc.get("artifact") or "table"
        out["table"] = f"{kind} {loc.get('label')}" + (f", row {loc['row']}" if loc.get("row") is not None else "")
    return out


_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")

CITE_TEXT = (
    "Cite the ORCHID data release you used (see CITATION.cff / the Zenodo DOI of the release) AND the original "
    "source of every value: the paper in `source.citation` / `source.doi` (for values the paper cites from earlier "
    "work, also `cited_sources`), the TDB assessment (`model_citation` / `model_doi`) for calculated values, and "
    "Materials Project (Jain et al., APL Mater. 1, 011002 (2013), doi:10.1063/1.4812323) or OQMD for DFT values."
)


class OrchidError(ValueError):
    """A tool-level error with a message meant for the caller."""


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _stats(values: Iterable[float]) -> dict[str, Any]:
    vals = [v for v in (_f(x) for x in values) if v is not None]
    if not vals:
        return {"n": 0}
    out: dict[str, Any] = {
        "n": len(vals),
        "mean": round(statistics.fmean(vals), 4),
        "median": round(statistics.median(vals), 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
    }
    if len(vals) > 1:
        out["stdev"] = round(statistics.stdev(vals), 4)
    return out


def _norm_doi(doi: str) -> str:
    d = doi.strip()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:\s*", "", d, flags=re.I)
    return d.lower()


class Orchid:
    """Facade over the ORCHID data (literature, phase diagrams, hulls, TDB catalog)."""

    def __init__(self, data: DataSource | None = None, site: DataSource | None = None) -> None:
        if data is None or site is None:
            d, s = sources_from_env()
            data, site = data or d, site or s
        self.data = data
        self.site = site
        self._systems: dict[str, dict[str, Any]] = {}
        self._parity_by_sys: dict[str, list[str]] | None = None

    # ------------------------------------------------------------------ meta
    def _lit_index(self) -> dict[str, Any]:
        return self.data.read_json("literature/index.json")

    def _pd_index(self) -> dict[str, Any]:
        return self.data.read_json("phase-diagrams/index.json")

    def _papers(self) -> dict[str, Any]:
        return self.data.read_json("literature/papers.json")

    def meta(self) -> dict[str, Any]:
        idx = self._lit_index()
        return {
            "dataset": "ORCHID (Open Repository for CALPHAD Hypotheses, Intelligence, and Discovery)",
            "release": idx.get("release"),
            "licence": idx.get("licence"),
            "data_source": self.data.location,
            "how_to_cite": CITE_TEXT,
            "notes": [
                "Evidence classes (experimental | model | dft | unspecified) are never mixed or averaged.",
                "uncertainty.basis 'stated_default' / 'reading_resolution' is an ORCHID placeholder, not a measurement.",
                "Quotes are single sentences reproduced under fair use so each value can be verified; "
                "source papers are not redistributed.",
            ],
        }

    def _wrap(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["orchid"] = self.meta()
        return payload

    # ------------------------------------------------------------- literature
    def _system_doc(self, sys_id: str) -> dict[str, Any] | None:
        if sys_id in self._systems:
            return self._systems[sys_id]
        entry = self._lit_index().get("systems", {}).get(sys_id)
        if entry is None:
            return None
        merged: dict[str, Any] = {"records": [], "artifacts": {}, "models": {}, "papers": {}, "contexts": {}}
        for path in entry.get("files") or [f"systems/{sys_id}.json"]:
            part = self.data.read_json(f"literature/{path}")
            merged["records"].extend(part.get("records", []))
            for key in ("artifacts", "models", "papers", "contexts"):
                merged[key].update(part.get(key) or {})
            for key in ("system", "elements", "licence", "release"):
                merged.setdefault(key, part.get(key))
        self._systems[sys_id] = merged
        return merged

    def _paper_info(self, pid: str | None, doc: dict[str, Any] | None = None) -> dict[str, Any]:
        if not pid:
            return {}
        p = (doc or {}).get("papers", {}).get(pid)
        if p is None:
            p = self._papers().get(pid, {})
        return p

    def _expand(self, rec: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
        r = dict(rec)
        r.setdefault("elements", doc.get("elements"))
        loc = rec.get("locator") or {}
        art = doc.get("artifacts", {}).get(loc.get("ref"), {}) if isinstance(loc, dict) else {}
        if art:
            r["locator"] = {**art, **loc}
        mkey = rec.get("model")
        if isinstance(mkey, str) and mkey in doc.get("models", {}):
            r["model"] = {"key": mkey, **doc["models"][mkey]}
        paper = self._paper_info(rec.get("paper"), doc)
        quote = rec.get("quote")
        page = rec.get("quote_page") or (r.get("locator") or {}).get("page")
        src = {
            "paper": rec.get("paper"),
            "citation": paper.get("citation"),
            "doi": paper.get("doi"),
            "doi_url": paper.get("doi_url"),
            "year": paper.get("year"),
            "page": page,
            "quote": quote,
            "artifact": (r.get("locator") or {}).get("label"),
            "caption": (r.get("locator") or {}).get("caption"),
            "row": (r.get("locator") or {}).get("row"),
        }
        if quote is None:
            src["quote_note"] = (
                "table value: no sentence quote; verify with the table label/caption/page/row in `locator`"
            )
        r["source"] = src
        return r

    def _contexts_for(self, records: list[dict[str, Any]], doc: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for r in records:
            cond = r.get("conditions") or {}
            pid = cond.get("paper") or r.get("paper")
            if cond.get("kind") == "paper_context" and pid in doc.get("contexts", {}):
                out[pid] = doc["contexts"][pid]
        return out

    @staticmethod
    def _accepted(rec: dict[str, Any]) -> bool:
        return bool((rec.get("flags") or {}).get("extraction_accepted"))

    def _sys(self, system: str) -> str:
        try:
            return system_id(system)
        except ChemError as exc:
            raise OrchidError(str(exc)) from exc

    # ------------------------------------------------------------------ tools
    def search_systems(
        self,
        elements: list[str] | None = None,
        text: str | None = None,
        exact: bool = False,
        limit: int = 50,
        offset: int = 0,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """Find chemical systems with literature data and/or calculated phase diagrams.

        ``elements``: systems containing all of them (``exact=True``: exactly them).
        ``text``: case-insensitive match on the system name or on titles/citations of
        papers reporting that system.
        """
        fields = _check_fields(fields)
        want: set[str] = set()
        try:
            want = {element(e) for e in elements or []}
        except ChemError as exc:
            raise OrchidError(str(exc)) from exc
        lit = self._lit_index().get("systems", {})
        pd = self._pd_index().get("systems", {})
        text_hits: set[str] | None = None
        if text:
            t = text.strip().lower()
            text_hits = set()
            for sid in set(lit) | set(pd):
                if t in sid.lower() or t in pretty_system(sid).lower() or t.replace(" ", "") in sid.lower().replace("-", ""):
                    text_hits.add(sid)
            for p in self._papers().values():
                blob = f"{p.get('title') or ''} {p.get('citation') or ''}".lower()
                if t in blob:
                    text_hits.update(p.get("systems") or [])
        rows = []
        for sid in sorted(set(lit) | set(pd)):
            els = set(sid.split("-"))
            if want and (els != want if exact else not want <= els):
                continue
            if text_hits is not None and sid not in text_hits:
                continue
            li = lit.get(sid, {})
            calc = pd.get(sid, [])
            rows.append(
                {
                    "system": sid,
                    "elements": sorted(els),
                    "literature_values": li.get("n", 0),
                    "literature_accepted": li.get("accepted", 0),
                    "by_evidence_class": li.get("by_evidence_class", {}),
                    "by_property_group": li.get("by_property_group", {}),
                    "calculated_phase_diagrams": len(calc),
                    "assessments": [{"tdb": a.get("tdb"), "citation": a.get("citation"), "doi": a.get("doi")} for a in calc],
                    "dft_hulls": len(els) == 2,
                    "explorer_url": f"{SITE_URL}data-explorer/#{sid}" if li else None,
                    "phase_diagram_url": f"{SITE_URL}phase-diagrams/#{sid}" if calc else None,
                }
            )
        rows.sort(key=lambda r: (len(r["elements"]), -r["literature_accepted"], -r["calculated_phase_diagrams"], r["system"]))
        page = rows[max(0, offset) : max(0, offset) + max(1, limit)]
        if fields == "summary":
            page = [
                {
                    "system": r["system"],
                    "literature_accepted": r["literature_accepted"],
                    "by_evidence_class": r["by_evidence_class"],
                    "calculated_phase_diagrams": r["calculated_phase_diagrams"],
                    "assessments": [a["tdb"] for a in r["assessments"]],
                }
                for r in page
            ]
        return self._wrap(
            {
                "query": {"elements": sorted(want), "text": text, "exact": exact},
                "fields": fields,
                "total": len(rows),
                "offset": offset,
                "returned": len(page),
                "truncated": offset + len(page) < len(rows),
                "systems": page,
            }
        )

    def get_system_data(
        self,
        system: str,
        property_group: str | None = None,
        evidence_class: str | None = None,
        accepted_only: bool = True,
        limit: int = 50,
        offset: int = 0,
        property: str | None = None,
        compact: bool = False,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """Literature values for one system.

        ``fields='summary'`` (default) returns the short form of ``summarize_record``;
        ``fields='full'`` the complete record with expanded provenance, where
        ``compact=True`` drops only extraction-pipeline bookkeeping (readers, tool release,
        pre-normalisation copies); citation, DOI, page, quote, locator, evidence class,
        origin, flags, uncertainty and model link are always kept.
        """
        fields = _check_fields(fields)
        sid = self._sys(system)
        doc = self._system_doc(sid)
        if doc is None:
            raise OrchidError(f"no literature data for system {sid}; try search_systems")
        if evidence_class and evidence_class not in EVIDENCE_CLASSES:
            raise OrchidError(f"evidence_class must be one of {EVIDENCE_CLASSES}")
        pg = property_group.strip().lower() if property_group else None
        prop = property.strip().upper() if property else None
        sel = []
        for rec in doc["records"]:
            if accepted_only and not self._accepted(rec):
                continue
            if evidence_class and rec.get("evidence_class") != evidence_class:
                continue
            if pg and pg not in (rec.get("property_group") or "").lower():
                continue
            if prop and (rec.get("property") or "").upper() != prop:
                continue
            sel.append(rec)
        page = sel[max(0, offset) : max(0, offset) + max(1, min(limit, 2000))]
        values = [self._expand(r, doc) for r in page]
        contexts = self._contexts_for(values, doc)
        if fields == "summary":
            values = [summarize_record(r) for r in values]
        elif compact:
            values = [{k: v for k, v in r.items() if k not in _COMPACT_DROP} for r in values]
        groups: dict[str, int] = {}
        for r in sel:
            groups[r.get("property_group") or "?"] = groups.get(r.get("property_group") or "?", 0) + 1
        return self._wrap(
            {
                "system": sid,
                "filters": {
                    "property_group": property_group,
                    "property": property,
                    "evidence_class": evidence_class,
                    "accepted_only": accepted_only,
                },
                "fields": fields,
                "total": len(sel),
                "total_matching": len(sel),
                "returned": len(values),
                "offset": offset,
                "truncated": offset + len(values) < len(sel),
                "next_offset": offset + len(values) if offset + len(values) < len(sel) else None,
                "matching_by_property_group": groups,
                "values": values,
                "contexts": contexts,
                "explorer_url": f"{SITE_URL}data-explorer/#{sid}",
                **({"fields_note": FIELDS_NOTE} if fields == "summary" else {}),
            }
        )

    # -------------------------------------------------------- phase diagrams
    def _assessments(self, sid: str) -> list[dict[str, Any]]:
        return list(self._pd_index().get("systems", {}).get(sid, []))

    def _pick(self, sid: str, assessment: str | None) -> list[dict[str, Any]]:
        rows = self._assessments(sid)
        if not assessment:
            return rows
        a = assessment.strip().lower()
        a_doi = _norm_doi(assessment)
        hit = [
            r
            for r in rows
            if a in {
                (r.get("file") or "").lower(),
                (r.get("tdb") or "").lower(),
                (r.get("tdb") or "").lower().rsplit(".", 1)[0],
                (r.get("file") or "").lower().rsplit(".", 1)[0],
            }
            or (r.get("doi") and _norm_doi(r["doi"]) == a_doi)
        ]
        if not hit:
            raise OrchidError(
                f"assessment {assessment!r} not found for {sid}; available: {[r.get('tdb') for r in rows]}"
            )
        return hit

    def _diagram(self, entry: dict[str, Any]) -> dict[str, Any]:
        f = entry.get("file") or ""
        if not _FILE_RE.match(f):
            raise OrchidError(f"bad diagram file name {f!r}")
        return self.data.read_json(f"phase-diagrams/sgte/{f}")

    @staticmethod
    def _grid_phase(doc: dict[str, Any], x: float, T: float) -> dict[str, Any]:
        g = doc.get("grid") or {}
        rows, labels = g.get("rows") or [], g.get("labels") or []
        if not rows:
            return {"error": "no stored equilibrium grid for this diagram"}
        xs, T0, dT = float(g["x_step"]), float(g["T0"]), float(g["dT"])
        npts = int(g.get("x_points") or round(1 / xs) + 1)
        if not (0.0 <= x <= 1.0):
            return {"error": "x must be a mole fraction in [0, 1]"}
        i = round((T - T0) / dT)
        if i < 0 or i >= len(rows):
            return {"error": f"T outside the stored grid ({T0}..{T0 + dT * (len(rows) - 1)} K)"}
        j = min(npts - 1, max(0, round(x / xs)))
        col = 0
        label = None
        for li, count in rows[i]:
            if col <= j < col + count:
                label = labels[li] if 0 <= li < len(labels) else None
                break
            col += count
        return {
            "x_requested": x,
            "T_requested_K": T,
            "grid_x": round(j * xs, 6),
            "grid_T_K": T0 + i * dT,
            "x_component": doc.get("x_component"),
            "stable_phases": label.split("+") if label else None,
            "label": label,
            "note": "nearest node of the stored pycalphad equilibrium grid (" + str(g.get("note", "")) + "); "
            f"resolution dx={xs}, dT={dT} K. Calculated (evidence class: model), not measured.",
        }

    def _diagram_summary(self, entry: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
        regions = []
        for reg in doc.get("regions", []):
            segs = reg if isinstance(reg, list) else [reg]
            xs = [v for sg in segs for v in sg.get("x", []) if v is not None]
            Ts = [v for sg in segs for v in sg.get("T", []) if v is not None]
            regions.append(
                {
                    "boundaries": [sg.get("phase") for sg in segs],
                    "points": len(xs),
                    "x_range": [min(xs), max(xs)] if xs else None,
                    "T_range_K": [min(Ts), max(Ts)] if Ts else None,
                }
            )
        src = doc.get("original_source") or {}
        sid = "-".join(doc.get("components") or entry.get("system", "").split("-"))
        return {
            "evidence_class": "model",
            "tdb": doc.get("tdb") or entry.get("tdb"),
            "tdb_sha256": doc.get("sha256") or entry.get("sha256"),
            "tdb_download_url": f"{PUBLIC_SITE_DATA_URL}databases/sgte-binary/{doc.get('tdb') or entry.get('tdb')}",
            "assessment_citation": src.get("citation") or entry.get("citation"),
            "assessment_doi": src.get("doi") or entry.get("doi"),
            "assessment_url": src.get("url"),
            "collection": self._pd_index().get("source"),
            "engine": doc.get("engine") or self._pd_index().get("engine"),
            "status": doc.get("status"),
            "components": doc.get("components"),
            "x_component": doc.get("x_component"),
            "T_range_K": doc.get("T_range"),
            "phases": doc.get("phases"),
            "invariants": doc.get("invariants", []),
            "regions_summary": regions,
            "grid": {k: v for k, v in (doc.get("grid") or {}).items() if k not in ("rows",)},
            "data_url": f"{PUBLIC_DATA_URL}phase-diagrams/sgte/{entry.get('file')}",
            "viewer_url": f"{SITE_URL}phase-diagrams/#{sid}/{entry.get('file')}",
        }

    def get_phase_diagram(
        self,
        system: str,
        assessment: str | None = None,
        x: float | None = None,
        T: float | None = None,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """Calculated binary phase diagram(s) of a system; optionally the stable phases at (x, T).

        ``fields='summary'`` omits the per-region boundary summary and grid metadata (``fields='full'``).
        """
        fields = _check_fields(fields)
        sid = self._sys(system)
        entries = self._pick(sid, assessment)
        if not entries:
            raise OrchidError(f"no calculated phase diagram for {sid}")
        out = []
        for e in entries:
            doc = self._diagram(e)
            s = self._diagram_summary({**e, "system": sid}, doc)
            if x is not None and T is not None:
                s["phase_at"] = self._grid_phase(doc, float(x), float(T))
            if fields == "summary":
                for k in ("regions_summary", "grid", "collection", "assessment_url"):
                    s.pop(k, None)
            out.append(s)
        return self._wrap(
            {
                "system": sid,
                "fields": fields,
                "n_assessments": len(out),
                "assessments": out,
                "note": "Each assessment is a separate CALPHAD model; results are never averaged across assessments.",
            }
        )

    def get_invariant_reactions(
        self,
        system: str,
        source: str = "both",
        assessment: str | None = None,
        accepted_only: bool = True,
        limit: int = 50,
        offset: int = 0,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """Literature invariant reactions and calculated invariants (per assessment), side by side.

        Calculated invariants are matched against all literature invariants; the literature list
        itself is paged (``limit``/``offset``) and shortened with ``fields='summary'``.
        """
        fields = _check_fields(fields)
        if source not in ("literature", "calculated", "both"):
            raise OrchidError("source must be 'literature', 'calculated' or 'both'")
        sid = self._sys(system)
        result: dict[str, Any] = {"system": sid, "source": source}
        lit_recs: list[dict[str, Any]] = []
        if source in ("literature", "both"):
            doc = self._system_doc(sid)
            if doc is not None:
                for rec in doc["records"]:
                    if accepted_only and not self._accepted(rec):
                        continue
                    if rec.get("observation_type") == "invariant_reaction" or rec.get("property_group") == "invariant reaction":
                        lit_recs.append(self._expand(rec, doc))
                lit_recs.sort(key=lambda r: -(_f(r.get("value")) or 0))
                page = lit_recs[max(0, offset) : max(0, offset) + max(1, limit)]
                result["contexts"] = self._contexts_for(page, doc)
            else:
                page = []
            result["fields"] = fields
            result["literature_total"] = len(lit_recs)
            result["literature_offset"] = offset
            result["literature_truncated"] = offset + len(page) < len(lit_recs)
            result["literature"] = [summarize_record(r) for r in page] if fields == "summary" else page
        if source in ("calculated", "both"):
            calc = []
            for e in self._pick(sid, assessment):
                doc = self._diagram(e)
                s = self._diagram_summary({**e, "system": sid}, doc)
                invs = []
                seen: set[tuple[Any, ...]] = set()
                for inv in s["invariants"]:
                    key = (round(float(inv.get("T", 0)), 1), tuple(sorted(p.get("phase") or "" for p in inv.get("points", []))))
                    if key in seen:  # the engine reports one entry per node contact
                        continue
                    seen.add(key)
                    item = dict(inv)
                    phases = {re.sub(r"#\d+$", "", p.get("phase") or "") for p in inv.get("points", [])}
                    if lit_recs:
                        best = []
                        for r in lit_recs:
                            mp = {p.get("model_phase") for p in r.get("phases") or []}
                            if None in mp or {re.sub(r"#\d+$", "", m) for m in mp} != phases:
                                continue
                            v = _f(r.get("value"))
                            if v is None:
                                continue
                            best.append(
                                {
                                    "id": r["id"],
                                    "evidence_class": r.get("evidence_class"),
                                    "T_K": v,
                                    "dT_K": round(v - inv["T"], 2),
                                    "doi": (r.get("source") or {}).get("doi"),
                                    "page": (r.get("source") or {}).get("page"),
                                }
                            )
                        best.sort(key=lambda b: abs(b["dT_K"]))
                        if fields == "summary":
                            best = [{k: b[k] for k in ("id", "evidence_class", "T_K", "dT_K")} for b in best[:3]]
                        item["literature_matches"] = best[:5]
                    invs.append(item)
                calc.append(
                    {
                        k: s[k]
                        for k in ("tdb", "tdb_sha256", "assessment_citation", "assessment_doi", "engine", "viewer_url", "data_url")
                    }
                    | {"evidence_class": "model", "invariants": invs, "n_invariants_raw": len(s["invariants"])}
                )
            result["calculated"] = calc
            result["matching_rule"] = (
                "literature_matches: literature records whose mapped CALPHAD model phases equal the calculated "
                "invariant's phase set exactly (unmapped phases never match); dT = literature - calculated."
            )
        if source in ("literature", "both") and not lit_recs and not result.get("calculated"):
            raise OrchidError(f"no invariant data for {sid}")
        return self._wrap(result)

    # ------------------------------------------------------- formation / DFT
    def _hull(self, layer: str, sid: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        a, b = sid.split("-")
        base = DFT_LAYERS[layer]["dir"]
        try:
            hull = self.data.read_json(f"{base}/{a}/{a}-{b}.json")
        except NotFound:
            return None, None
        try:
            entries = self.data.read_json(f"{base}/entries/{a}/{a}-{b}.json")
        except NotFound:
            entries = None
        return hull, entries

    def _dft_at(self, sid: str, comp: dict[str, float]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for layer, info in DFT_LAYERS.items():
            hull, entries = self._hull(layer, sid)
            if hull is None:
                out[layer] = {"source": info["label"], "available": False}
                continue
            xel = hull.get("x_element")
            x0 = comp.get(xel, 0.0)
            hits = []
            for iso in hull.get("isotherms", []):
                for v in iso.get("vertices", []):
                    if abs(float(v["x"]) - x0) < 2e-3:
                        hits.append({**v, "on_hull": True, "energy_above_hull_eV": 0.0})
            for v in (entries or {}).get("above_hull", []):
                if abs(float(v["x"]) - x0) < 2e-3:
                    hits.append({**v, "on_hull": False, "energy_above_hull_eV": v.get("energy_above_hull_recomputed")})
            for h in hits:
                h["formation_energy_eV_per_atom"] = h.get("energy")
                h["formation_energy_kJ_per_mol_atom"] = round(float(h["energy"]) * KJ_PER_MOL_PER_EV, 3)
            hits.sort(key=lambda h: h["formation_energy_eV_per_atom"])
            out[layer] = {
                "source": info["label"],
                "available": True,
                "evidence_class": "dft",
                "reference": hull.get("reference"),
                "release": (hull.get("provenance") or {}).get("mp_release")
                or (hull.get("provenance") or {}).get("oqmd_release")
                or (hull.get("provenance") or {}).get("release"),
                "provenance": hull.get("provenance"),
                "x_element": xel,
                "x": x0,
                "entries": hits,
                "lowest_kJ_per_mol_atom": hits[0]["formation_energy_kJ_per_mol_atom"] if hits else None,
                "window_note": "entries above the hull are kept only within the layer's above_hull_window_eV",
                "data_url": f"{PUBLIC_DATA_URL}{info['dir']}/{sid.split('-')[0]}/{sid}.json",
            }
        return out

    def get_formation_enthalpy(
        self,
        formula: str,
        accepted_only: bool = True,
        include_higher_order_systems: bool = True,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """Formation enthalpy of a compound: literature values (kJ/mol-atom) and MP / OQMD DFT values."""
        fields = _check_fields(fields)
        comp = parse_formula(formula)
        if comp is None:
            raise OrchidError(f"cannot parse {formula!r} as a single multi-element formula (e.g. 'Al3Ni')")
        sid = composition_system(comp)
        lit: list[dict[str, Any]] = []
        other_units: list[dict[str, Any]] = []
        want = set(sid.split("-"))
        sys_ids = [sid] if not include_higher_order_systems else sorted(
            k for k in self._lit_index().get("systems", {}) if want <= set(k.split("-"))
        )
        for one in sys_ids:
            doc = self._system_doc(one)
            if doc is None:
                continue
            for rec in doc["records"]:
                if rec.get("property") != "HM_FORM":
                    continue
                if accepted_only and not self._accepted(rec):
                    continue
                if not self._comp_match(rec, comp):
                    continue
                r = self._expand(rec, doc)
                (lit if rec.get("unit") == "kJ/mol-atom" else other_units).append(r)
        summary: dict[str, Any] = {}
        for ev in EVIDENCE_CLASSES:
            vals = [
                r["value"]
                for r in lit
                if r.get("evidence_class") == ev
                and not (r.get("flags") or {}).get("unsigned_in_print")
                and not (r.get("flags") or {}).get("magnitude_suspect")
            ]
            if vals:
                summary[f"literature_{ev}"] = _stats(vals)
        dft = self._dft_at(sid, comp) if len(comp) == 2 else {"note": "DFT hull layers hold unary/binary entries only"}
        for layer in DFT_LAYERS:
            ents = (dft.get(layer) or {}).get("entries") or []
            if ents:
                summary[f"dft_{layer}_lowest"] = ents[0]["formation_energy_kJ_per_mol_atom"]
        return self._wrap(
            {
                "formula": formula,
                "composition": comp,
                "system": sid,
                "unit": "kJ/mol-atom",
                "fields": fields,
                "literature": [summarize_record(r) for r in lit] if fields == "summary" else lit,
                "literature_other_units": [summarize_record(r) for r in other_units] if fields == "summary" else other_units,
                "dft": dft,
                "summary": summary,
                "summary_rule": (
                    "Statistics per evidence class over values in kJ/mol-atom (records with basis-unstated kJ/mol are "
                    "listed in literature_other_units and excluded), excluding flags unsigned_in_print and "
                    "magnitude_suspect. DFT: lowest-energy entry at the composition per source; MP and OQMD use "
                    "different references and are never averaged. 1 eV/atom = 96.485 kJ/mol-atom. Values for different "
                    "structures or sublattice end-members of the same composition are not separated here: check each "
                    "record's phases[].label before comparing."
                ),
                "literature_match_rule": (
                    "record property HM_FORM in the compound's system file (and, by default, in files of higher-order "
                    "systems containing it; the record's `system` says where it was reported), with one formula parsed from its phase label(s) "
                    "(or a single printed mole-fraction phase composition) equal to the requested composition (±0.002)"
                ),
            }
        )

    @staticmethod
    def _comp_match(rec: dict[str, Any], comp: dict[str, float], tol: float = 2e-3) -> bool:
        def same(c: dict[str, float] | None) -> bool:
            if not c or set(c) != set(comp):
                return False
            return all(abs(float(c[k]) - comp[k]) <= tol for k in comp)

        parsed = {
            tuple(sorted(c.items()))
            for c in (parse_formula(p.get("label") or "") for p in rec.get("phases") or [])
            if c is not None
        }
        if len(parsed) == 1:
            return same(dict(next(iter(parsed))))
        if len(parsed) > 1:
            return False
        pcs = [
            pc.get("composition")
            for pc in rec.get("phase_compositions") or []
            if pc.get("basis") == "mole_fraction"
        ]
        if len(pcs) == 1 and pcs[0]:
            c = dict(pcs[0])
            if len(c) == len(comp) - 1:  # binary stored as one element's fraction
                missing = (set(comp) - set(c)).pop() if set(c) < set(comp) else None
                if missing:
                    c[missing] = round(1 - sum(float(v) for v in c.values()), 4)
            return same(c)
        return False

    def _parity(self) -> dict[str, Any]:
        return self.data.read_json("literature/dft_parity.json")

    def compare_to_dft(self, system: str, default_subset_only: bool = False, fields: str = "summary") -> dict[str, Any]:
        """Literature formation enthalpies vs Materials Project vs OQMD for one system."""
        fields = _check_fields(fields)
        sid = self._sys(system)
        par = self._parity()
        if self._parity_by_sys is None:
            by: dict[str, list[str]] = {}
            for rid, rec in par.get("records", {}).items():
                comp_sys = "-".join(sorted(k.upper() for k in (rec.get("comp") or {})))
                by.setdefault(comp_sys, []).append(rid)
            self._parity_by_sys = by
        points: dict[str, list[dict[str, Any]]] = {}
        rids = set(self._parity_by_sys.get(sid, []))
        for p in par.get("points", []):
            if p.get("rid") in rids:
                points.setdefault(p["rid"], []).append(p)
        papers = par.get("papers", {})
        compounds: dict[str, dict[str, Any]] = {}
        resid: dict[str, list[float]] = {"mp": [], "oqmd": []}
        for rid in sorted(rids):
            rec = par["records"][rid]
            in_default = (
                rec.get("ev") == "experimental"
                and rec.get("acc")
                and not rec.get("unsigned")
                and not rec.get("sign_lost")
                and not rec.get("suspect")
            )
            if default_subset_only and not in_default:
                continue
            key = rec.get("formula") or "?"
            c = compounds.setdefault(key, {"formula": key, "composition": rec.get("comp"), "literature": [], "dft": {}})
            pap = papers.get(rec.get("paper"), {}) or self._paper_info(rec.get("paper"))
            c["literature"].append(
                {
                    "id": rid,
                    "value_kJ_per_mol_atom": rec.get("lit"),
                    "evidence_class": rec.get("ev"),
                    "in_default_subset": bool(in_default),
                    "extraction_accepted": rec.get("acc"),
                    "phase": rec.get("phase"),
                    "temperature_K": rec.get("T"),
                    "method": rec.get("method"),
                    "method_class": rec.get("mcls"),
                    "paper": rec.get("paper"),
                    "citation": pap.get("citation"),
                    "doi": pap.get("doi"),
                    "page": rec.get("page"),
                    "table": rec.get("table"),
                    "quote": rec.get("quote"),
                    "record_kind": rec.get("kind"),
                    "reported_in_system": rec.get("sys"),
                    "own_work_of_paper": rec.get("own"),
                    "cited_sources": rec.get("cited"),
                    "flags": {
                        "unsigned_in_print": rec.get("unsigned"),
                        "sign_lost": rec.get("sign_lost"),
                        "sign_verified": rec.get("sign_verified"),
                        "magnitude_suspect": rec.get("suspect"),
                        "match": rec.get("match"),
                        "match_note": rec.get("match_note"),
                    },
                    "dft_points": [
                        {
                            "source": p.get("src"),
                            "dft_kJ_per_mol_atom": p.get("dft"),
                            "dft_eV_per_atom": p.get("dft_eV"),
                            "residual_dft_minus_lit": p.get("res"),
                            "material_id": p.get("mid"),
                            "spacegroup": p.get("sg"),
                            "e_above_hull_eV": p.get("ehull"),
                            "n_polymorphs": p.get("npoly"),
                            "theoretical": p.get("theoretical"),
                        }
                        for p in points.get(rid, [])
                    ],
                }
            )
            for p in points.get(rid, []):
                c["dft"].setdefault(
                    p.get("src"),
                    {"kJ_per_mol_atom": p.get("dft"), "material_id": p.get("mid"), "spacegroup": p.get("sg"), "e_above_hull_eV": p.get("ehull")},
                )
                if in_default and _f(p.get("res")) is not None:
                    resid[p["src"]].append(float(p["res"]))
            if fields == "summary":
                full = c["literature"][-1]
                c["literature"][-1] = {
                    "id": full["id"],
                    "value_kJ_per_mol_atom": full["value_kJ_per_mol_atom"],
                    "evidence_class": full["evidence_class"],
                    "in_default_subset": full["in_default_subset"],
                    "accepted": full["extraction_accepted"],
                    "phase": full["phase"],
                    "T_K": full["temperature_K"],
                    "citation": full["citation"],
                    "doi": full["doi"],
                    "page": full["page"],
                    "quote": _short(full["quote"]),
                    "table": full["table"],
                    "residual_dft_minus_lit": {d["source"]: d["residual_dft_minus_lit"] for d in full["dft_points"]},
                }
        stats = {}
        for src, rs in resid.items():
            if rs:
                stats[src] = {
                    "n": len(rs),
                    "MAE_kJ_per_mol_atom": round(statistics.fmean(abs(r) for r in rs), 3),
                    "mean_residual": round(statistics.fmean(rs), 3),
                }
        hull_vertices: dict[str, Any] = {}
        if len(sid.split("-")) == 2:
            for layer, info in DFT_LAYERS.items():
                hull, _ = self._hull(layer, sid)
                if hull:
                    hull_vertices[layer] = [
                        {
                            "formula": v.get("formula"),
                            "x": v.get("x"),
                            "material_id": v.get("phase"),
                            "spacegroup": v.get("spacegroup_symbol"),
                            "formation_energy_kJ_per_mol_atom": round(float(v["energy"]) * KJ_PER_MOL_PER_EV, 3),
                            "theoretical": v.get("theoretical"),
                        }
                        for iso in hull.get("isotherms", [])
                        for v in iso.get("vertices", [])
                    ]
        return self._wrap(
            {
                "system": sid,
                "unit": "kJ/mol-atom",
                "fields": fields,
                "compounds": sorted(compounds.values(), key=lambda c: c["formula"]),
                "residual_stats_default_subset": stats,
                "dft_hull_vertices": hull_vertices,
                "dft_sources": par.get("dft_sources"),
                "rules": par.get("rules"),
                "description": par.get("description"),
                "viewer_url": f"{SITE_URL}dft-parity/",
                "selection_note": "compounds whose composition lies in this system, wherever reported "
                "(including papers on higher-order systems: see reported_in_system)",
            }
        )

    # -------------------------------------------------------------- TDB files
    def _catalog(self) -> list[dict[str, Any]]:
        cat = self.site.read_json("catalog.json")
        return cat if isinstance(cat, list) else cat.get("records", [])

    def _known_sha(self) -> dict[str, str]:
        out = {}
        for rows in self._pd_index().get("systems", {}).values():
            for r in rows:
                if r.get("tdb") and r.get("sha256"):
                    out[r["tdb"]] = r["sha256"]
        return out

    def _catalog_entry(self, e: dict[str, Any], sha: dict[str, str]) -> dict[str, Any]:
        out = dict(e)
        bundled = e.get("tier") == "bundled" and e.get("license_status") == "confirmed" and e.get("local_path")
        out["redistributable_here"] = bool(bundled)
        out["orchid_download_url"] = f"{PUBLIC_SITE_DATA_URL}{e['local_path']}" if bundled else None
        out["sha256"] = sha.get(e.get("name", "")) if bundled else None
        out["access"] = {
            "bundled": "download from ORCHID (licence permits redistribution); cite the original assessment",
            "fetched": "not redistributed by ORCHID: download from the original source (download_url / source_url)",
            "manual": "not redistributed: obtain from the original publisher / landing page (source_url)",
        }.get(e.get("tier"), "see source_url")
        return out

    def list_tdb_files(
        self,
        elements: list[str] | None = None,
        exact: bool = False,
        tier: str | None = None,
        limit: int = 50,
        offset: int = 0,
        fields: str = "summary",
    ) -> dict[str, Any]:
        """TDB catalog entries whose system contains all ``elements`` (``exact``: exactly them)."""
        fields = _check_fields(fields)
        try:
            want = {element(x) for x in elements or []}
        except ChemError as exc:
            raise OrchidError(str(exc)) from exc
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
        return self._wrap(
            {
                "query": {"elements": sorted(want), "exact": exact, "tier": tier},
                "fields": fields,
                "total": len(rows),
                "offset": offset,
                "truncated": offset + max(1, limit) < len(rows),
                "files": [
                    {k: r.get(k) for k in _TDB_SUMMARY} if fields == "summary" else r
                    for r in rows[max(0, offset) : max(0, offset) + max(1, limit)]
                ],
                "tiers": {
                    "bundled": "redistributable; served by ORCHID",
                    "fetched": "public upstream download, not redistributed here",
                    "manual": "restricted / landing page only",
                },
            }
        )

    def get_tdb(self, file: str, include_content: bool = True, max_bytes: int = 2_000_000) -> dict[str, Any]:
        """One TDB catalog entry; the file content only for bundled, confirmed-licence files."""
        name = file.strip().rsplit("/", 1)[-1]
        hits = [e for e in self._catalog() if (e.get("name") or "").lower() == name.lower()]
        if not hits:
            hits = [e for e in self._catalog() if (e.get("name") or "").lower().rsplit(".", 1)[0] == name.lower()]
        if not hits:
            raise OrchidError(f"TDB {file!r} not in the ORCHID catalog; use list_tdb_files")
        entry = self._catalog_entry(hits[0], self._known_sha())
        out: dict[str, Any] = {"entry": entry, "content": None}
        if not entry["redistributable_here"]:
            out["content_withheld"] = (
                f"tier '{entry.get('tier')}' / licence status '{entry.get('license_status')}': ORCHID does not "
                "redistribute this file. Obtain it from the original source."
            )
            return self._wrap(out)
        rel = entry["local_path"]
        if not isinstance(rel, str) or not rel.startswith("databases/"):
            raise OrchidError("catalog entry has an unexpected local_path")
        try:
            data = self.site.read_bytes(rel)
        except DataSourceError as exc:
            out["content_error"] = str(exc)
            return self._wrap(out)
        digest = hashlib.sha256(data).hexdigest()
        out["sha256_of_served_bytes"] = digest
        if entry.get("sha256"):
            out["sha256_matches_phase_diagram_record"] = digest == entry["sha256"]
        entry["sha256"] = entry.get("sha256") or digest
        out["size_bytes"] = len(data)
        if include_content:
            if len(data) > max_bytes:
                out["content_withheld"] = f"file larger than max_bytes={max_bytes}; download it from orchid_download_url"
            else:
                out["content"] = data.decode("latin-1") if b"\x00" not in data else None
                out["content_encoding"] = "latin-1 (bytes preserved 1:1)"
        out["licence_note"] = (
            f"{entry.get('license')}: cite the original assessment ({entry.get('source_url')}) and the collection"
        )
        return self._wrap(out)

    # ------------------------------------------------------------------ papers
    def get_paper(self, doi: str) -> dict[str, Any]:
        """Bibliographic record of a paper (by DOI or ORCHID paper id), its systems, values and contexts."""
        papers = self._papers()
        q = doi.strip()
        pid = q if q.split(":", 1)[0] in ("doi", "sha256", "ref", "unresolved") and q in papers else f"doi:{_norm_doi(q)}"
        if pid not in papers:
            raise OrchidError(f"paper {doi!r} not in ORCHID (id tried: {pid})")
        rec = papers[pid]
        reported: dict[str, dict[str, int]] = {}
        cited = 0
        for sid in rec.get("systems") or []:
            doc = self._system_doc(sid)
            if doc is None:
                continue
            for r in doc["records"]:
                if r.get("paper") == pid:
                    d = reported.setdefault(sid, {"values": 0, "accepted": 0})
                    d["values"] += 1
                    d["accepted"] += int(self._accepted(r))
                    ev = r.get("evidence_class") or "?"
                    d[f"evidence_class:{ev}"] = d.get(f"evidence_class:{ev}", 0) + 1
                elif any((c or {}).get("paper") == pid for c in r.get("cited_sources") or []):
                    cited += 1
        ctx = self.data.read_json("literature/contexts.json").get(pid)
        return self._wrap(
            {
                "paper": rec,
                "systems": rec.get("systems") or [],
                "n_values_reported": sum(v["values"] for v in reported.values()),
                "values_by_system": reported,
                "n_values_citing_this_as_source_in_its_systems": cited,
                "experimental_context": ctx,
                "n_context_items": len((ctx or {}).get("items", [])),
                "note": "Bibliographic record only; ORCHID does not redistribute the paper. Context items carry their quote and page.",
            }
        )

    # ------------------------------------------------- resources and prompts
    def resource_text(self, name: str) -> str:
        paths = {
            "schema": [("data", "literature/SCHEMA.md")],
            "dataset-card": [("data", "README.md"), ("data", "CITATION.cff")],
            "licence": [("data", "LICENSES.md"), ("data", "NOTICE.md"), ("data", "LICENSE")],
        }[name]
        parts = []
        for which, p in paths:
            src = self.data if which == "data" else self.site
            try:
                parts.append(f"<!-- {p} -->\n" + src.read_text(p))
            except DataSourceError as exc:
                parts.append(f"<!-- {p}: unavailable ({exc}) -->")
        return "\n\n".join(parts)


def cite_orchid_prompt(topic: str = "") -> str:
    """Prompt template: how to cite ORCHID and the original sources."""
    about = f" about {topic}" if topic else ""
    return (
        f"You are answering a question{about} with data from ORCHID (Open Repository for CALPHAD Hypotheses, "
        "Intelligence, and Discovery; https://orchid-project.org, data at https://data.orchid-project.org).\n\n"
        "Rules for citing:\n"
        "1. For every number you report, give its evidence class (experimental, model, dft, unspecified) and its "
        "original source: the paper citation and DOI from `source.citation` / `source.doi`, the page, and the quote "
        "or table locator. If the value is cited from earlier work (`origin.own_work_of_paper` false), name the "
        "`cited_sources` as the primary source.\n"
        "2. Calculated (model) values: cite the CALPHAD assessment (`model_citation`, `model_doi`) and name the TDB "
        "file and its SHA-256. DFT values: cite Materials Project (A. Jain et al., APL Mater. 1, 011002 (2013), "
        "doi:10.1063/1.4812323, release in the response) or OQMD (Saal et al., JOM 65, 1501 (2013), "
        "doi:10.1007/s11837-013-0755-4; Kirklin et al., npj Comput. Mater. 1, 15010 (2015)).\n"
        "3. Never average values across evidence classes, assessments or DFT sources. Report "
        "`uncertainty.basis: stated_default` as 'no printed uncertainty' rather than as a measurement.\n"
        "4. Cite the ORCHID data release you used (release string in `orchid.release`; Zenodo DOI per CITATION.cff): "
        "R. Arróyave, J. Saal, D. Shin, ORCHID data: literature thermodynamic data, calculated phase diagrams and "
        "DFT hulls, https://data.orchid-project.org (CC BY 4.0).\n"
        "5. Quotes are single sentences reproduced under fair use for verification; do not reproduce more of the "
        "source paper than the quote."
    )
