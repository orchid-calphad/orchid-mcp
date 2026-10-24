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



def _ehull_eV(v):
    """The DFT parity file stores the energy above hull in kJ/mol-atom (build_dft_parity.py); eV/atom = kJ/mol / 96.485."""
    try:
        return None if v is None else round(float(v) / 96.485, 5)
    except (TypeError, ValueError):
        return None

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
            # ESPEI semantics of the release: ready = usable in ESPEI (complete state, phase mapped, physical phase
            # rule); certified = ready AND agreement with a CALPHAD (pycalphad) prediction
            "espei_ready": bool((r.get("flags") or {}).get("espei_ready")),
            "espei_certified": bool((r.get("flags") or {}).get("espei_certified")),
            "calphad_check": (r.get("flags") or {}).get("calphad_check") or "not_available",
            "citation": src.get("citation"),
            "doi": src.get("doi"),
            "page": src.get("page"),
            "quote": _short(src.get("quote")),
        }
    )
    # literature-level consistency gates: codes of the gates that demoted / flagged the value, rules that corrected it
    gf = [g.get("code") for g in r.get("gate_failures") or [] if isinstance(g, dict) and g.get("code")]
    if gf:
        out["gate_failures"] = gf
    cr = [c.get("rule") for c in r.get("corrections") or [] if isinstance(c, dict) and c.get("rule")]
    if cr:
        out["corrected_by"] = cr
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


# ---------------------------------------------------------------- phase registry
PHASE_NAMES_MAX = 30
PHASE_MODELS_MAX = 50
PHASE_REFS_SUMMARY = 5
PHASE_EXAMPLES = 5
TDB_STATEMENT_MAX = 4000
PHASE_STRUCTURE_KEYS = ("structure_type", "basis", "tier", "confidence", "prototype", "strukturbericht", "pearson", "space_group")
PHASE_TIERS = {
    "A": "standard_name: the TDB phase name encodes one structure; model stoichiometry/sublattices and DFT agree",
    "B": "dft_unique_match ('DFT-supported'): one on-hull entry in both Materials Project and OQMD, same space group",
    "C": "assessment_declared ('assessor-stated'): one structure in the Hallstedt 2025 crystallographic table (mmc2)",
    "liquid": "liquid (no prototype)",
    "gas": "gas (no prototype)",
    "none": "no structure assigned (needs_review with candidates, or unresolved)",
}
PHASE_CITE_TEXT = (
    "Cite the ORCHID phase registry (binary systems) of the release you used (phases/CITATION.cff; Zenodo record "
    "phase-registry), the original CALPHAD assessment of every TDB model you use (references with role 'assessment "
    "that models this phase'), B. Hallstedt, Calphad 89 (2025) 102833, doi:10.1016/j.calphad.2025.102833 for the "
    "SGTE TDB files and tier-C structures, and Materials Project / OQMD for tier-B (DFT-supported) structures."
)


def _struct_norm(x: Any) -> str:
    """Case- and separator-insensitive key for structure labels (L1_2 == L12, D0_19 == D019, 'Pm-3m' == 'pm3m')."""
    if x is None:
        return ""
    return re.sub(r"[\s_\-()'′,.]", "", str(x)).lower()


# ---------------------------------------------------------------- bibliography
BIB_ROLES = (
    "corpus_assessment_read", "cited_original_source", "tdb_source_assessment", "tdb_compilation",
    "handbook_diagram_source", "discovery_lead", "context_source", "espei_reference",
)
BIB_RELATIONS = (
    "cites", "used_in_assessment", "models_system_in_tdb", "reassesses", "handbook_reproduces", "cited_by_crossref",
)
BIB_DIRECTIONS = ("out", "in", "both")
BIB_USED_MAX = 25
BIB_CITE_TEXT = (
    "Cite the papers themselves when you use their data (doi / citation of each record), and the ORCHID "
    "bibliography (bibliography/CITATION.cff; Zenodo record 'bibliography'). Crossref metadata is CC0; ORCHID-derived "
    "fields (roles, evidence counts, relations) are CC BY 4.0."
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
                            "e_above_hull_kJ_per_mol_atom": p.get("ehull"), "e_above_hull_eV": _ehull_eV(p.get("ehull")),
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
                    {"kJ_per_mol_atom": p.get("dft"), "material_id": p.get("mid"), "spacegroup": p.get("sg"), "e_above_hull_kJ_per_mol_atom": p.get("ehull"), "e_above_hull_eV": _ehull_eV(p.get("ehull"))},
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
    def get_paper(self, doi: str, fields: str = "summary") -> dict[str, Any]:
        """Bibliographic record of a paper (by DOI or ORCHID paper id), its systems, values and contexts, and its
        bibliography entry (roles, evidence summary; fields='full' adds relations and the full record)."""
        fields = _check_fields(fields)
        try:
            papers = self._papers()
        except NotFound:  # a data source with the bibliography only
            papers = {}
        q = doi.strip()
        pid = q if q.split(":", 1)[0] in ("doi", "sha256", "ref", "unresolved") and q in papers else f"doi:{_norm_doi(q)}"
        bib = self._bib_paper(q, required=False)
        if pid not in papers:
            if bib is None:
                raise OrchidError(f"paper {doi!r} not in ORCHID (id tried: {pid})")
            return self._wrap({"paper": None, "bibliography": self._bib_view(bib, fields),
                               "note": "Known to the ORCHID bibliography only (no literature values read from it).",
                               "how_to_cite_bibliography": BIB_CITE_TEXT})
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
        out = {
            "paper": rec,
            "systems": rec.get("systems") or [],
            "n_values_reported": sum(v["values"] for v in reported.values()),
            "values_by_system": reported,
            "n_values_citing_this_as_source_in_its_systems": cited,
            "experimental_context": ctx,
            "n_context_items": len((ctx or {}).get("items", [])),
            "note": "Bibliographic record only; ORCHID does not redistribute the paper. Context items carry their quote and page.",
        }
        if bib is None:
            bib = self._bib_paper(pid, required=False)
        if bib is not None:
            out["bibliography"] = self._bib_view(bib, fields)
            out["how_to_cite_bibliography"] = BIB_CITE_TEXT
        return self._wrap(out)

    # ------------------------------------------------------------- bibliography
    def _bib_index(self) -> dict[str, Any]:
        try:
            idx = self.data.read_json("bibliography/index.json")
        except NotFound as exc:
            raise OrchidError("the bibliography (bibliography/index.json) is not in this ORCHID data source") from exc
        if "_by" not in idx:
            cols = idx["columns"]
            rows = [dict(zip(cols, r)) for r in idx["rows"]]
            by: dict[str, str] = {}
            for r in rows:
                by[r["id"]] = r["key"]
                if r.get("doi"):
                    by["doi:" + r["doi"]] = r["key"]
            idx["_rows"] = rows
            idx["_by"] = by
        return idx

    def _bib_key(self, q: str) -> str | None:
        idx = self._bib_index()
        q = (q or "").strip()
        if not q:
            raise OrchidError("give a DOI or a bibliography id")
        if re.fullmatch(r"p[0-9a-f]{16}", q):
            return q if any(r["key"] == q for r in idx["_rows"]) else None
        if q.startswith(("nodoi:", "doi:")) and q in idx["_by"]:
            return idx["_by"][q]
        return idx["_by"].get("doi:" + _norm_doi(q.removeprefix("doi:")))

    def _bib_paper(self, q: str, required: bool = True) -> dict[str, Any] | None:
        try:
            key = self._bib_key(q)
        except OrchidError:
            if required:
                raise
            return None
        if key is None:
            if required:
                raise OrchidError(f"paper {q!r} not in the ORCHID bibliography")
            return None
        return self.data.read_json(f"bibliography/papers/{key}.json")

    @staticmethod
    def _bib_summary_record(r: dict[str, Any]) -> dict[str, Any]:
        es = r.get("evidence_summary") or {}
        ev = {}
        for k in ("reported", "cited_as_source"):
            if k in es:
                ev[k] = {x: es[k][x] for x in ("n_values", "n_values_accepted", "by_evidence_class", "by_property_group",
                                                "n_tables", "n_text_passages", "n_citing_papers") if x in es[k]}
        for k in ("espei_datasets", "phase_registry_phases"):
            if k in es:
                ev["n_" + k] = len(es[k])
        if "assessed_assemblage_records" in es:
            ev["assessed_assemblage_records"] = es["assessed_assemblage_records"]
        rd = {}
        for role, d in (r.get("role_details") or {}).items():
            d = dict(d)
            if "tdb_files" in d:
                d["tdb_files"] = [{k: t.get(k) for k in ("file", "system", "sha256", "bundled")} for t in d["tdb_files"]]
            rd[role] = d
        return {
            "id": r["id"], "key": r["key"], "doi": r.get("doi"), "citation": r.get("citation"), "title": r.get("title"),
            "authors": [a.get("family") or a.get("name") for a in r.get("authors") or []][:10],
            "year": r.get("year"), "container_title": r.get("container_title"), "type": r.get("type"),
            "systems": r.get("systems") or [], "roles": r.get("roles") or [], "role_details": rd,
            "evidence_summary": ev, "availability": r.get("availability"),
            "is_referenced_by_count": r.get("is_referenced_by_count"),
            "is_referenced_by_count_retrieved": r.get("is_referenced_by_count_retrieved"),
            "doi_match": r.get("doi_match"), "doi_check": r.get("doi_check"), "relation_counts": r.get("relation_counts"),
            "urls": r.get("urls"),
        }

    def _bib_view(self, doc: dict[str, Any], fields: str) -> dict[str, Any]:
        r = doc["paper"]
        if fields == "full":
            return {"record": r, "relations": doc.get("relations") or []}
        return self._bib_summary_record(r)

    @staticmethod
    def _bib_row(r: dict[str, Any]) -> dict[str, Any]:
        return {k: r.get(k) for k in ("id", "key", "doi", "title", "year", "container", "authors", "roles", "systems",
                                      "values_reported", "values_cited")} | {
            "data_url": f"{PUBLIC_DATA_URL}bibliography/papers/{r['key']}.json"}

    def search_papers(
        self,
        system: str | None = None,
        elements: list[str] | None = None,
        role: str | None = None,
        property: str | None = None,
        evidence_class: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        text: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Papers of the bibliography filtered by exact system, elements, role, property code, evidence class,
        year range and title/author text."""
        sid = self._sys(system) if system else None
        try:
            want = {element(x) for x in elements or []}
        except ChemError as exc:
            raise OrchidError(str(exc)) from exc
        if role and role not in BIB_ROLES:
            raise OrchidError(f"role must be one of {BIB_ROLES}")
        if evidence_class and evidence_class not in EVIDENCE_CLASSES[:4]:
            raise OrchidError(f"evidence_class must be one of {EVIDENCE_CLASSES[:4]}")
        prop = (property or "").strip().upper()
        tq = re.sub(r"\s+", " ", (text or "").strip().lower())
        rows = []
        for r in self._bib_index()["_rows"]:
            if sid and sid not in r["systems"]:
                continue
            if want and not any(want <= set(s.split("-")) for s in r["systems"]):
                continue
            if role and role not in r["roles"]:
                continue
            if prop and prop not in r["properties"]:
                continue
            if evidence_class and evidence_class not in r["evidence_classes"]:
                continue
            y = r.get("year")
            if year_from is not None and (y is None or y < int(year_from)):
                continue
            if year_to is not None and (y is None or y > int(year_to)):
                continue
            if tq:
                hay = " ".join([str(r.get("title") or ""), " ".join(r.get("authors") or []), str(r.get("doi") or "")]).lower()
                if tq not in re.sub(r"\s+", " ", hay):
                    continue
            rows.append(r)
        rows.sort(key=lambda r: (r.get("year") or 0, r["id"]))
        lim, off = max(1, min(int(limit), 500)), max(0, int(offset))
        page = rows[off : off + lim]
        return self._wrap({
            "query": {"system": sid, "elements": sorted(want), "role": role, "property": prop or None,
                      "evidence_class": evidence_class, "year_from": year_from, "year_to": year_to, "text": text},
            "total": len(rows), "offset": off, "returned": len(page), "truncated": off + lim < len(rows),
            "papers": [self._bib_row(r) for r in page],
            "roles": list(BIB_ROLES),
            "how_to_cite_bibliography": BIB_CITE_TEXT,
        })

    def get_paper_relations(
        self, doi: str, relation: str | None = None, direction: str = "both", limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """Typed relations of one paper (edges with provenance)."""
        if relation and relation not in BIB_RELATIONS:
            raise OrchidError(f"relation must be one of {BIB_RELATIONS}")
        d = (direction or "both").strip().lower()
        if d not in BIB_DIRECTIONS:
            raise OrchidError(f"direction must be one of {BIB_DIRECTIONS}")
        doc = self._bib_paper(doi)
        rels = [x for x in doc.get("relations") or []
                if (not relation or x["type"] == relation) and (d == "both" or x["direction"] == d)]
        rels.sort(key=lambda x: (x["type"], x["direction"], -(x.get("count") or 0), x["other"]["id"]))
        lim, off = max(1, min(int(limit), 1000)), max(0, int(offset))
        page = rels[off : off + lim]
        by_type: dict[str, dict[str, int]] = {}
        for x in rels:
            by_type.setdefault(x["type"], {"out": 0, "in": 0})[x["direction"]] += 1
        return self._wrap({
            "paper": self._bib_summary_record(doc["paper"]) | {"evidence_summary": None, "role_details": None},
            "query": {"relation": relation, "direction": d},
            "total": len(rels), "offset": off, "returned": len(page), "truncated": off + lim < len(rels),
            "counts_by_type": by_type, "relations": page,
            "relation_types": {
                "cites": "paper ORCHID read -> work printed as the source of its values (count = values)",
                "used_in_assessment": "original data paper -> paper/assessment that used it (inverse of cites)",
                "models_system_in_tdb": "paper -> TDB file whose header cites it",
                "reassesses": "later assessment -> earlier assessment of the same exact system it cites",
                "handbook_reproduces": "handbook diagram -> original evaluation",
                "cited_by_crossref": "src's publisher-deposited Crossref reference list contains dst",
            },
            "how_to_cite_bibliography": BIB_CITE_TEXT,
        })

    def get_assessment_evidence(self, system: str, max_data_papers: int = BIB_USED_MAX) -> dict[str, Any]:
        """Assessments of an exact system with the original data papers they used, counts, and reassessment links."""
        sid = self._sys(system)
        self._bib_index()
        try:
            doc = self.data.read_json(f"bibliography/systems/{sid}.json")
        except NotFound as exc:
            raise OrchidError(f"no papers for {sid} in the ORCHID bibliography") from exc
        mx = max(0, min(int(max_data_papers), 500))
        assessments = []
        chain = []
        for a in doc.get("assessments") or []:
            used = a.get("data_papers_used") or []
            assessments.append({**{k: v for k, v in a.items() if k != "data_papers_used"},
                                "data_papers_used": used[:mx], "data_papers_truncated": len(used) > mx,
                                "data_url": f"{PUBLIC_DATA_URL}bibliography/papers/{a['key']}.json"})
            for earlier in a.get("reassesses") or []:
                chain.append({"later": a["id"], "earlier": earlier, "method": "citation+same-system"})
        return self._wrap({
            "system": sid,
            "n_assessments": len(assessments),
            "assessments": assessments,
            "reassessment_links": chain,
            "papers_by_role": {k: len(v) for k, v in (doc.get("papers_by_role") or {}).items()},
            "data_url": f"{PUBLIC_DATA_URL}bibliography/systems/{sid}.json",
            "static_page_url": f"{PUBLIC_DATA_URL}bibliography/pages/systems/{sid}.html",
            "note": "Assessments = papers behind a TDB file of this system or read by ORCHID; data papers used = works "
                    "printed as the source of the values ORCHID extracted from the assessment (cites edges).",
            "how_to_cite_bibliography": BIB_CITE_TEXT,
        })

    # ---------------------------------------------------------- phase registry
    def _phase_index(self) -> dict[str, Any]:
        try:
            return self.data.read_json("phases/registry.json")
        except NotFound as exc:
            raise OrchidError("the phase registry (phases/registry.json) is not in this ORCHID data source") from exc

    def _phase_system(self, sid: str) -> dict[str, Any]:
        try:
            return self.data.read_json(f"phases/systems/{sid}.json")
        except NotFound as exc:
            raise OrchidError(f"no phase-registry file for {sid} (binary systems only)") from exc

    def _phase_rows(self, sid: str | None = None) -> list[dict[str, Any]]:
        rows = self._phase_index().get("phases", [])
        return [r for r in rows if r.get("system") == sid] if sid else rows

    @staticmethod
    def _phase_links(pid: str, sid: str) -> dict[str, str]:
        return {
            "viewer_url": f"{SITE_URL}phases/#{pid}",
            "data_url": f"{PUBLIC_DATA_URL}phases/systems/{sid}.json",
            "static_page_url": f"{PUBLIC_DATA_URL}phases/pages/{sid}.html#"
            + re.sub(r"[^a-z0-9]+", "-", pid.lower()).strip("-"),
        }

    def _phase_row_summary(self, r: dict[str, Any]) -> dict[str, Any]:
        names = r.get("names") or []
        return {
            "id": r.get("id"),
            "system": r.get("system"),
            "preferred_name": r.get("preferred_name"),
            "status": r.get("status"),
            "structure": r.get("structure"),
            "tdb_names": r.get("tdb_names") or [],
            "stoichiometry": r.get("stoichiometry"),
            "n_names": len(names),
            "counts": r.get("counts") or {},
            "viewer_url": f"{SITE_URL}phases/#{r.get('id')}",
        }

    @staticmethod
    def _labels_of(r: dict[str, Any]) -> set[str]:
        out = {str(r.get("preferred_name") or "").lower()}
        out |= {str(n).lower() for n in r.get("tdb_names") or []}
        for n in r.get("names") or []:
            out.add(str(n).lower())
            out.add(str(n).rsplit(":", 1)[-1].lower())
        out.add(str(r.get("id") or "").rsplit(":", 1)[-1].lower())
        out.discard("")
        return out

    def _phase_summary(self, e: dict[str, Any]) -> dict[str, Any]:
        by_ns: dict[str, list[str]] = {}
        for n in e.get("names") or []:
            by_ns.setdefault(n.get("namespace") or "?", []).append(n.get("name"))
        names = {
            ns: {"n": len(v), "names": v[:PHASE_NAMES_MAX], **({"truncated": True} if len(v) > PHASE_NAMES_MAX else {})}
            for ns, v in sorted(by_ns.items())
        }
        stab = [
            {k: s.get(k) for k in ("tdb", "stable", "T_min_K", "T_max_K", "x_element", "x_min", "x_max") if k in s}
            for s in e.get("stability") or []
            if s.get("status") == "ok"
        ]
        models = []
        for m in (e.get("models") or [])[:PHASE_MODELS_MAX]:
            v = ((m.get("tdb_definition") or {}).get("verbatim") or {})
            models.append(
                {
                    "tdb": m.get("tdb"),
                    "phase_name": m.get("phase_name"),
                    "type_code": m.get("type_code"),
                    "sublattices": " : ".join(
                        f"{sl.get('site_ratio')} ({','.join(sl.get('constituents') or [])})" for sl in m.get("sublattices") or []
                    ),
                    "phase_statement": _short((v.get("phase") or {}).get("text")),
                    "constituent_statement": _short((v.get("constituent") or {}).get("text")),
                    "website_url": ((m.get("tdb_definition") or {}).get("provenance") or {}).get("website_url"),
                }
            )
        refs = e.get("references") or []
        out = {
            "id": e.get("id"),
            "system": e.get("system"),
            "preferred_name": e.get("preferred_name"),
            "status": e.get("status"),
            "structure": {k: (e.get("structure") or {}).get(k) for k in PHASE_STRUCTURE_KEYS} if e.get("structure") else None,
            "review_reasons": e.get("review_reasons") or [],
            "n_candidates": len(e.get("candidates") or []),
            "stoichiometry": e.get("stoichiometry"),
            "names": names,
            "models": models,
            "n_models": len(e.get("models") or []),
            "stability": stab,
            "literature": e.get("literature"),
            "references": [{"doi": r.get("doi"), "roles": r.get("roles")} for r in refs[:PHASE_REFS_SUMMARY]],
            "total_papers": e.get("total_papers"),
            **self._phase_links(e["id"], e["system"]),
        }
        if len(e.get("models") or []) > PHASE_MODELS_MAX:
            out["models_truncated"] = True
        return out

    def _phase_full(self, e: dict[str, Any]) -> dict[str, Any]:
        out = dict(e)
        models = []
        for m in (e.get("models") or [])[:PHASE_MODELS_MAX]:
            m = dict(m)
            td = m.get("tdb_definition")
            if td:
                td = dict(td)
                vb = dict(td.get("verbatim") or {})
                for key in ("phase", "constituent"):
                    if isinstance(vb.get(key), dict) and len(vb[key].get("text") or "") > TDB_STATEMENT_MAX:
                        vb[key] = {**vb[key], "text": _short(vb[key]["text"], TDB_STATEMENT_MAX), "text_truncated": True}
                vb["type_definitions"] = [
                    {**t, "text": _short(t.get("text"), TDB_STATEMENT_MAX)} for t in vb.get("type_definitions") or []
                ]
                td["verbatim"] = vb
                m["tdb_definition"] = td
            models.append(m)
        out["models"] = models
        if len(e.get("models") or []) > PHASE_MODELS_MAX:
            out["models_truncated"] = f"first {PHASE_MODELS_MAX} of {len(e['models'])}; see data_url"
        out.update(self._phase_links(e["id"], e["system"]))
        return out

    def get_phase(self, system: str, phase: str, fields: str = "summary") -> dict[str, Any]:
        """One phase of a binary system from the ORCHID phase registry (by id, namespaced name or plain label)."""
        fields = _check_fields(fields)
        q = (phase or "").strip()
        if not q:
            raise OrchidError("phase is empty: give a registry id, a namespaced name or a label such as 'L12_FCC'")
        if q.lower().startswith("phase:") and (not system or str(system).lower().startswith("phase:")):
            parts = q.split(":")
            system = parts[1].upper() if len(parts) > 2 else system
        sid = self._sys(system)
        rows = self._phase_rows(sid)
        if not rows:
            raise OrchidError(f"no phases for {sid} in the phase registry (binary systems only; use search_phases)")
        ql = q.lower()
        hits = [r for r in rows if str(r.get("id")).lower() == ql]
        if not hits:
            hits = [r for r in rows if ql in {str(n).lower() for n in r.get("names") or []}]
        if not hits:
            hits = [r for r in rows if ql in self._labels_of(r)]
        if not hits:
            hits = [r for r in rows if any(ql in lab for lab in self._labels_of(r))]
        if not hits:
            raise OrchidError(
                f"no phase {phase!r} in {sid}; phases: " + ", ".join(sorted(str(r.get("preferred_name")) for r in rows))
            )
        if len(hits) > 1:
            return self._wrap(
                {
                    "query": {"system": sid, "phase": q},
                    "ambiguous": True,
                    "matches": [self._phase_row_summary(r) for r in hits],
                    "hint": "call get_phase again with one of the ids",
                    "how_to_cite_phase_registry": PHASE_CITE_TEXT,
                }
            )
        row = hits[0]
        doc = self._phase_system(sid)
        e = next((x for x in doc.get("phases", []) if x.get("id") == row["id"]), None)
        if e is None:
            raise OrchidError(f"{row['id']} is in registry.json but not in phases/systems/{sid}.json")
        return self._wrap(
            {
                "query": {"system": sid, "phase": q},
                "fields": fields,
                "phase": self._phase_summary(e) if fields == "summary" else self._phase_full(e),
                "how_to_cite_phase_registry": PHASE_CITE_TEXT,
            }
        )

    def search_phases(
        self,
        elements: list[str] | None = None,
        name: str | None = None,
        prototype: str | None = None,
        tier: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Phases of the registry filtered by elements, name, prototype / Strukturbericht / Pearson, tier, status."""
        try:
            want = {element(x) for x in elements or []}
        except ChemError as exc:
            raise OrchidError(str(exc)) from exc
        t = (tier or "").strip()
        tiers = {"a": "A", "b": "B", "c": "C", "liquid": "liquid", "gas": "gas", "none": "none"}
        if t and t.lower() not in tiers:
            raise OrchidError("tier must be one of A, B, C, liquid, gas, none")
        t = tiers.get(t.lower()) if t else None
        nq = (name or "").strip().lower()
        pq = _struct_norm(prototype) if prototype else ""
        rows = []
        for r in self._phase_rows():
            if want and not want <= set(r.get("elements") or str(r.get("system", "")).split("-")):
                continue
            s = r.get("structure") or {}
            if t:
                rt = "none" if not s else ("liquid" if s.get("basis") == "liquid" else "gas" if s.get("basis") == "gas" else s.get("tier"))
                if rt != t:
                    continue
            if status and r.get("status") != status:
                continue
            if nq and not any(nq in lab for lab in self._labels_of(r)):
                continue
            if pq:
                keys = {_struct_norm(s.get(k)) for k in ("prototype", "strukturbericht", "pearson", "structure_type")}
                keys.add(_struct_norm(str(s.get("structure_type") or "").removeprefix("st:")))
                if pq not in keys:
                    continue
            rows.append(r)
        rows.sort(key=lambda r: (r.get("system") or "", r.get("id") or ""))
        lim, off = max(1, min(int(limit), 500)), max(0, int(offset))
        return self._wrap(
            {
                "query": {"elements": sorted(want), "name": name, "prototype": prototype, "tier": t, "status": status},
                "total": len(rows),
                "offset": off,
                "returned": len(rows[off : off + lim]),
                "truncated": off + lim < len(rows),
                "phases": [self._phase_row_summary(r) for r in rows[off : off + lim]],
                "tiers": PHASE_TIERS,
                "how_to_cite_phase_registry": PHASE_CITE_TEXT,
            }
        )

    def get_structure_type(self, query: str, limit: int = 20) -> dict[str, Any]:
        """Structure types by id, prototype, Strukturbericht, Pearson symbol or space group (symbol or number)."""
        q = (query or "").strip()
        if not q:
            raise OrchidError("query is empty: give a structure-type id, prototype, Strukturbericht or Pearson symbol")
        try:
            st = self.data.read_json("phases/structure_types.json").get("structure_types", [])
        except NotFound as exc:
            raise OrchidError("phases/structure_types.json is not in this ORCHID data source") from exc
        qn = _struct_norm(q)
        hits = []
        for t in st:
            sg = t.get("space_group") or {}
            keys = {_struct_norm(t.get(k)) for k in ("id", "prototype", "strukturbericht", "pearson")}
            keys.add(_struct_norm(str(t.get("id") or "").removeprefix("st:")))
            keys |= {_struct_norm(x) for x in t.get("prototype_spellings") or []}
            keys |= {_struct_norm(sg.get("symbol")), _struct_norm(str(sg.get("number") or ""))}
            keys.discard("")
            if qn in keys:
                hits.append(t)
        if not hits:
            raise OrchidError(f"no structure type matches {query!r} (try a prototype like 'AuCu3', 'L1_2' or 'cP4')")
        hits.sort(key=lambda t: (-(t.get("registry_phases") or 0), t.get("id") or ""))
        ids = {t["id"] for t in hits[: max(1, limit)]}
        examples: dict[str, list[str]] = {}
        for r in self._phase_rows():
            sid = (r.get("structure") or {}).get("structure_type")
            if sid in ids and len(examples.setdefault(sid, [])) < PHASE_EXAMPLES:
                examples[sid].append(r["id"])
        return self._wrap(
            {
                "query": q,
                "total": len(hits),
                "truncated": len(hits) > max(1, limit),
                "structure_types": [
                    {**t, "example_phases": examples.get(t["id"], [])} for t in hits[: max(1, limit)]
                ],
                "how_to_cite_phase_registry": PHASE_CITE_TEXT,
            }
        )

    # ------------------------------------------------- resources and prompts
    def resource_text(self, name: str) -> str:
        paths = {
            "schema": [("data", "literature/SCHEMA.md")],
            "dataset-card": [("data", "README.md"), ("data", "CITATION.cff")],
            "licence": [("data", "LICENSES.md"), ("data", "NOTICE.md"), ("data", "LICENSE")],
            "bibliography-schema": [("data", "bibliography/SCHEMA.md")],
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
