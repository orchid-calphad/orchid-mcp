"""Formation enthalpies: literature values beside Materials Project and OQMD DFT entries, and the DFT parity table."""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any

from .chem import composition_system, parse_formula
from .constants import dft_layers, evidence_classes, kj_per_mol_per_ev, public_data_url, site_url
from .core import check_fields
from .datasource import NotFound
from .errors import OrchidError
from .report import report_issue_url
from .summaries import shorten, stats, summarize_record, to_float

if TYPE_CHECKING:
    from .core import OrchidCore

comp_tolerance = 2e-3  # mole-fraction tolerance when matching a composition
hm_unit = "kJ/mol-atom"
hm_summary_rule = (
    "Statistics per evidence class over values in kJ/mol-atom (records with basis-unstated kJ/mol are "
    "listed in literature_other_units and excluded), excluding flags unsigned_in_print and "
    "magnitude_suspect. DFT: lowest-energy entry at the composition per source; MP and OQMD use "
    "different references and are never averaged. 1 eV/atom = 96.485 kJ/mol-atom. Values for different "
    "structures or sublattice end-members of the same composition are not separated here: check each "
    "record's phases[].label before comparing."
)
hm_match_rule = (
    "record property HM_FORM in the compound's system file (and, by default, in files of higher-order "
    "systems containing it; the record's `system` says where it was reported), with one formula parsed from its phase label(s) "
    "(or a single printed mole-fraction phase composition) equal to the requested composition (±0.002)"
)
parity_selection_note = (
    "compounds whose composition lies in this system, wherever reported (including papers on higher-order "
    "systems: see reported_in_system)"
)


def ehull_ev(v: Any) -> float | None:
    """Energy above the hull in eV/atom from the stored kJ/mol-atom value.

    The DFT parity file stores the energy above hull in kJ/mol-atom (build_dft_parity.py); eV/atom = kJ/mol / 96.485.

    Args:
        v (Any): Energy above hull in kJ/mol-atom, or None.

    Returns:
        The energy in eV/atom rounded to 5 decimals, or None when it is missing or not numeric.
    """
    try:
        return None if v is None else round(float(v) / kj_per_mol_per_ev, 5)
    except (TypeError, ValueError):
        return None


def _same_composition(c: dict[str, float] | None, comp: dict[str, float], tol: float) -> bool:
    """Whether a composition has the same elements as ``comp`` with every fraction within ``tol``.

    Args:
        c (dict[str, float] | None): Candidate composition (mole fractions).
        comp (dict[str, float]): Requested composition (mole fractions).
        tol (float): Absolute tolerance on each mole fraction.

    Returns:
        True when they match.
    """
    if not c or set(c) != set(comp):
        return False
    return all(abs(float(c[k]) - comp[k]) <= tol for k in comp)


def _single_mole_fraction(rec: dict[str, Any], comp: dict[str, float]) -> dict[str, float] | None:
    """The one mole-fraction composition printed for a record, completed for binaries stored as one fraction.

    Args:
        rec (dict[str, Any]): Parity or literature record.
        comp (dict[str, float]): Requested composition, used to know the element count.

    Returns:
        The composition, or None when the record has no single mole-fraction composition.
    """
    pcs = [pc.get("composition") for pc in rec.get("phase_compositions") or [] if pc.get("basis") == "mole_fraction"]
    if len(pcs) != 1 or not pcs[0]:
        return None
    c = dict(pcs[0])
    if len(c) == len(comp) - 1:  # binary stored as one element's fraction
        missing = (set(comp) - set(c)).pop() if set(c) < set(comp) else None
        if missing:
            c[missing] = round(1 - sum(float(v) for v in c.values()), 4)
    return c


def comp_matches(rec: dict[str, Any], comp: dict[str, float], tol: float = comp_tolerance) -> bool:
    """Whether a record's composition equals ``comp``.

    The composition comes from one formula parsed out of the record's phase label(s), or from a single printed
    mole-fraction phase composition. Records with several distinct formulas never match.

    Args:
        rec (dict[str, Any]): Literature or parity record.
        comp (dict[str, float]): Requested composition as mole fractions.
        tol (float): Absolute tolerance on each mole fraction.

    Returns:
        True when the record's composition matches.
    """
    parsed = {
        tuple(sorted(c.items())) for c in (parse_formula(p.get("label") or "") for p in rec.get("phases") or []) if c is not None
    }
    if len(parsed) == 1:
        return _same_composition(dict(next(iter(parsed))), comp, tol)
    if len(parsed) > 1:
        return False
    return _same_composition(_single_mole_fraction(rec, comp), comp, tol)


def _hull_hits(hull: dict[str, Any], entries: dict[str, Any] | None, x0: float) -> list[dict[str, Any]]:
    """Hull vertices and above-hull entries at one composition.

    Args:
        hull (dict[str, Any]): Hull document of the layer (``isotherms`` with ``vertices``).
        entries (dict[str, Any] | None): Entries document with ``above_hull``, or None.
        x0 (float): Mole fraction of the hull's ``x_element`` to match within ``comp_tolerance``.

    Returns:
        Copies of the matching vertices (on hull) and entries (above hull).
    """
    hits = []
    for iso in hull.get("isotherms", []):
        for v in iso.get("vertices", []):
            if abs(float(v["x"]) - x0) < comp_tolerance:
                hits.append({**v, "on_hull": True, "energy_above_hull_eV": 0.0})
    for v in (entries or {}).get("above_hull", []):
        if abs(float(v["x"]) - x0) < comp_tolerance:
            hits.append({**v, "on_hull": False, "energy_above_hull_eV": v.get("energy_above_hull_recomputed")})
    return hits


def _in_default(rec: dict[str, Any], pts: list[dict[str, Any]]) -> Any:
    """Whether a parity record belongs to the default subset (experimental, accepted, unflagged, default point).

    Args:
        rec (dict[str, Any]): Parity record.
        pts (list[dict[str, Any]]): The record's DFT points.

    Returns:
        The truthiness of the subset test (the same value the flag expression gives).
    """
    return (
        rec.get("ev") == "experimental"
        and rec.get("acc")
        and not rec.get("unsigned")
        and not rec.get("sign_lost")
        and not rec.get("suspect")
        # per-point default of build_dft_parity (structure match, composition offset, 0 K experimental;
        # ORCHID audit round 2, A4 E1/E5/E6): a record counts when at least one of its points is default
        and any(p.get("default", True) for p in pts)
    )


def _dft_point(p: dict[str, Any]) -> dict[str, Any]:
    """One DFT point of a parity record, as shown in the full form.

    Args:
        p (dict[str, Any]): Parity point (``src``, ``dft``, ``ehull`` and so on).

    Returns:
        The point with its energies in kJ/mol-atom and eV/atom.
    """
    return {
        "source": p.get("src"),
        "dft_kJ_per_mol_atom": p.get("dft"),
        "dft_eV_per_atom": p.get("dft_eV"),
        "residual_dft_minus_lit": p.get("res"),
        "material_id": p.get("mid"),
        "spacegroup": p.get("sg"),
        "e_above_hull_kJ_per_mol_atom": p.get("ehull"),
        "e_above_hull_eV": ehull_ev(p.get("ehull")),
        "n_polymorphs": p.get("npoly"),
        "theoretical": p.get("theoretical"),
    }


def _summary_literature(full: dict[str, Any]) -> dict[str, Any]:
    """Short form of a full literature entry of ``compare_to_dft``.

    Args:
        full (dict[str, Any]): Full literature entry.

    Returns:
        Id, value, evidence class, acceptance, phase, T, citation, DOI, page, short quote, table, residuals and link.
    """
    return {
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
        "quote": shorten(full["quote"]),
        "table": full["table"],
        "residual_dft_minus_lit": {d["source"]: d["residual_dft_minus_lit"] for d in full["dft_points"]},
        "report_issue_url": full["report_issue_url"],
    }


class DftTools:
    """Formation-enthalpy lookups and literature-versus-DFT comparisons.

    Args:
        core (OrchidCore): Shared data sources and response envelope.
    """

    def __init__(self, core: OrchidCore) -> None:
        """Keep a reference to the shared core; the parity index is built on first use."""
        self._core = core
        self._parity_by_sys: dict[str, list[str]] | None = None

    # ----------------------------------------------------------------- layers
    def _hull(self, layer: str, sid: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Hull document and entries document of a binary system in one DFT layer.

        Args:
            layer (str): Key of ``dft_layers`` (``"mp"`` or ``"oqmd"``).
            sid (str): Binary system id.

        Returns:
            (hull, entries); either is None when the file is absent.
        """
        a, b = sid.split("-")
        base = dft_layers[layer]["dir"]
        try:
            hull = self._core.data.read_json(f"{base}/{a}/{a}-{b}.json")
        except NotFound:
            return None, None
        try:
            entries = self._core.data.read_json(f"{base}/entries/{a}/{a}-{b}.json")
        except NotFound:
            entries = None
        return hull, entries

    def _layer_at(self, layer: str, sid: str, comp: dict[str, float]) -> dict[str, Any]:
        """DFT entries of one layer at one composition, lowest formation energy first.

        Args:
            layer (str): Key of ``dft_layers``.
            sid (str): Binary system id.
            comp (dict[str, float]): Requested composition (mole fractions).

        Returns:
            The layer's source, availability, provenance, entries (energies in eV/atom and kJ/mol-atom) and link.
        """
        info = dft_layers[layer]
        hull, entries = self._hull(layer, sid)
        if hull is None:
            return {"source": info["label"], "available": False}
        xel = hull.get("x_element")
        x0 = comp.get(xel, 0.0)
        hits = _hull_hits(hull, entries, x0)
        for h in hits:
            h["formation_energy_eV_per_atom"] = h.get("energy")
            h["formation_energy_kJ_per_mol_atom"] = round(float(h["energy"]) * kj_per_mol_per_ev, 3)
        hits.sort(key=lambda h: h["formation_energy_eV_per_atom"])
        prov = hull.get("provenance") or {}
        return {
            "source": info["label"],
            "available": True,
            "evidence_class": "dft",
            "reference": hull.get("reference"),
            "release": prov.get("mp_release") or prov.get("oqmd_release") or prov.get("release"),
            "provenance": hull.get("provenance"),
            "x_element": xel,
            "x": x0,
            "entries": hits,
            "lowest_kJ_per_mol_atom": hits[0]["formation_energy_kJ_per_mol_atom"] if hits else None,
            "window_note": "entries above the hull are kept only within the layer's above_hull_window_eV",
            "data_url": f"{public_data_url}{info['dir']}/{sid.split('-', maxsplit=1)[0]}/{sid}.json",
        }

    def _dft_at(self, sid: str, comp: dict[str, float]) -> dict[str, Any]:
        """DFT entries of every layer at one composition.

        Args:
            sid (str): Binary system id.
            comp (dict[str, float]): Requested composition (mole fractions).

        Returns:
            One block per layer, keyed by layer name.
        """
        return {layer: self._layer_at(layer, sid, comp) for layer in dft_layers}

    # -------------------------------------------------------- formation tool
    def get_formation_enthalpy(
        self, *, formula: str, accepted_only: bool, include_higher_order_systems: bool, fields: str
    ) -> dict[str, Any]:
        """Formation enthalpy of a compound: literature values (kJ/mol-atom) and MP / OQMD DFT values.

        Args:
            formula (str): A single multi-element formula such as ``"Al3Ni"`` or ``"Ni(Al)3"``.
            accepted_only (bool): Keep only accepted literature records.
            include_higher_order_systems (bool): Also search the files of higher-order systems containing the elements.
            fields (str): ``"summary"`` or ``"full"``.

        Returns:
            Literature values in kJ/mol-atom (per evidence class statistics in ``summary``), DFT entries per layer, and
            the matching rules.

        Raises:
            OrchidError: When the formula cannot be parsed as a single multi-element composition.
        """
        fields = check_fields(fields)
        comp = parse_formula(formula)
        if comp is None:
            raise OrchidError(f"cannot parse {formula!r} as a single multi-element formula (e.g. 'Al3Ni')")
        sid = composition_system(comp)
        sys_ids = self._sys_ids(sid, include_higher_order_systems)
        lit, other_units = self._hm_form_records(sys_ids, comp, accepted_only)
        summary: dict[str, Any] = self._literature_summary(lit)
        dft = self._dft_at(sid, comp) if len(comp) == 2 else {"note": "DFT hull layers hold unary/binary entries only"}
        for layer in dft_layers:
            ents = (dft.get(layer) or {}).get("entries") or []
            if ents:
                summary[f"dft_{layer}_lowest"] = ents[0]["formation_energy_kJ_per_mol_atom"]
        return self._core.wrap(
            {
                "formula": formula,
                "composition": comp,
                "system": sid,
                "unit": hm_unit,
                "fields": fields,
                "literature": [summarize_record(r) for r in lit] if fields == "summary" else lit,
                "literature_other_units": [summarize_record(r) for r in other_units] if fields == "summary" else other_units,
                "dft": dft,
                "summary": summary,
                "summary_rule": hm_summary_rule,
                "literature_match_rule": hm_match_rule,
            }
        )

    def _sys_ids(self, sid: str, include_higher_order_systems: bool) -> list[str]:
        """Systems whose literature files are searched for a composition.

        Args:
            sid (str): The compound's own system id.
            include_higher_order_systems (bool): Include every indexed system that contains all its elements.

        Returns:
            The system ids, sorted.
        """
        if not include_higher_order_systems:
            return [sid]
        want = set(sid.split("-"))
        return sorted(k for k in self._core.lit_index().get("systems", {}) if want <= set(k.split("-")))

    def _hm_form_records(
        self, sys_ids: list[str], comp: dict[str, float], accepted_only: bool
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Expanded HM_FORM records at a composition, split into kJ/mol-atom and other units.

        Args:
            sys_ids (list[str]): Systems to search.
            comp (dict[str, float]): Requested composition (mole fractions).
            accepted_only (bool): Keep only accepted records.

        Returns:
            (records in kJ/mol-atom, records in any other unit).
        """
        lit: list[dict[str, Any]] = []
        other_units: list[dict[str, Any]] = []
        for one in sys_ids:
            doc = self._core.system_doc(one)
            if doc is None:
                continue
            for rec in doc["records"]:
                if rec.get("property") != "HM_FORM":
                    continue
                if accepted_only and not self._core.accepted(rec):
                    continue
                if not comp_matches(rec, comp):
                    continue
                r = self._core.expand(rec, doc)
                (lit if rec.get("unit") == hm_unit else other_units).append(r)
        return lit, other_units

    @staticmethod
    def _literature_summary(lit: list[dict[str, Any]]) -> dict[str, Any]:
        """Per evidence class statistics of kJ/mol-atom values, excluding unsigned and magnitude-suspect values.

        Args:
            lit (list[dict[str, Any]]): Expanded records in kJ/mol-atom.

        Returns:
            ``literature_<class>`` statistics for each class that has values.
        """
        summary: dict[str, Any] = {}
        for ev in evidence_classes:
            vals = [
                r["value"]
                for r in lit
                if r.get("evidence_class") == ev
                and not (r.get("flags") or {}).get("unsigned_in_print")
                and not (r.get("flags") or {}).get("magnitude_suspect")
            ]
            if vals:
                summary[f"literature_{ev}"] = stats(vals)
        return summary

    # ------------------------------------------------------- DFT comparison
    def _parity(self) -> dict[str, Any]:
        """The literature-versus-DFT parity table (``literature/dft_parity.json``).

        Returns:
            The parsed table.
        """
        return self._core.data.read_json("literature/dft_parity.json")

    def _rids_by_system(self, par: dict[str, Any]) -> dict[str, list[str]]:
        """Parity record ids grouped by their element set; built once per instance.

        Args:
            par (dict[str, Any]): The parity table.

        Returns:
            System id to record ids.
        """
        if self._parity_by_sys is None:
            by: dict[str, list[str]] = {}
            for rid, rec in par.get("records", {}).items():
                comp_sys = "-".join(sorted(k.upper() for k in (rec.get("comp") or {})))
                by.setdefault(comp_sys, []).append(rid)
            self._parity_by_sys = by
        return self._parity_by_sys

    def compare_to_dft(self, *, system: str, default_subset_only: bool, fields: str) -> dict[str, Any]:
        """Literature formation enthalpies vs Materials Project vs OQMD for one system.

        Args:
            system (str): System as written by the user.
            default_subset_only (bool): Keep only records of the default subset (see ``_in_default``).
            fields (str): ``"summary"`` shortens each literature entry; ``"full"`` keeps the DFT point details.

        Returns:
            Compounds with their literature values and DFT points, residual statistics per source (over the default
            subset), the DFT hull vertices of both layers and the parity rules.
        """
        fields = check_fields(fields)
        sid = self._core.sys_id(system)
        par = self._parity()
        rids = set(self._rids_by_system(par).get(sid, []))
        points: dict[str, list[dict[str, Any]]] = {}
        for p in par.get("points", []):
            if p.get("rid") in rids:
                points.setdefault(p["rid"], []).append(p)
        papers = par.get("papers", {})
        compounds: dict[str, dict[str, Any]] = {}
        resid: dict[str, list[float]] = {"mp": [], "oqmd": []}
        for rid in sorted(rids):
            rec = par["records"][rid]
            pts = points.get(rid, [])
            in_default = _in_default(rec, pts)
            if default_subset_only and not in_default:
                continue
            key = rec.get("formula") or "?"
            c = compounds.setdefault(key, {"formula": key, "composition": rec.get("comp"), "literature": [], "dft": {}})
            pap = papers.get(rec.get("paper"), {}) or self._core.paper_info(rec.get("paper"))
            c["literature"].append(self._literature_entry(rid, rec, pap, in_default=in_default, pts=pts, sid=sid))
            for p in pts:
                c["dft"].setdefault(
                    p.get("src"),
                    {
                        "kJ_per_mol_atom": p.get("dft"),
                        "material_id": p.get("mid"),
                        "spacegroup": p.get("sg"),
                        "e_above_hull_kJ_per_mol_atom": p.get("ehull"),
                        "e_above_hull_eV": ehull_ev(p.get("ehull")),
                    },
                )
                if in_default and to_float(p.get("res")) is not None:
                    resid[p["src"]].append(float(p["res"]))
            if fields == "summary":
                c["literature"][-1] = _summary_literature(c["literature"][-1])
        return self._core.wrap(
            {
                "system": sid,
                "unit": hm_unit,
                "fields": fields,
                "compounds": sorted(compounds.values(), key=lambda c: c["formula"]),
                "residual_stats_default_subset": _residual_stats(resid),
                "dft_hull_vertices": self._hull_vertices(sid),
                "dft_sources": par.get("dft_sources"),
                "rules": par.get("rules"),
                "description": par.get("description"),
                "viewer_url": f"{site_url}dft-parity/",
                "selection_note": parity_selection_note,
            }
        )

    def _literature_entry(
        self,
        rid: str,
        rec: dict[str, Any],
        pap: dict[str, Any],
        *,
        in_default: Any,
        pts: list[dict[str, Any]],
        sid: str,
    ) -> dict[str, Any]:
        """Full literature entry of one parity record, with its DFT points.

        Args:
            rid (str): Record id.
            rec (dict[str, Any]): Parity record.
            pap (dict[str, Any]): Citation record of the paper.
            in_default (Any): Default-subset flag from ``_in_default``.
            pts (list[dict[str, Any]]): DFT points of the record.
            sid (str): System being compared (fallback for the report link).

        Returns:
            The entry with value, evidence, flags, locator, citation, report link and DFT points.
        """
        lit = rec.get("lit")
        return {
            "id": rid,
            "value_kJ_per_mol_atom": lit,
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
            "report_issue_url": report_issue_url(
                rid,
                rec.get("sys") or sid,
                pap.get("doi"),
                rec.get("page"),
                None if lit is None else f"{lit} {hm_unit}",
            ),
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
            "dft_points": [_dft_point(p) for p in pts],
        }

    def _hull_vertices(self, sid: str) -> dict[str, list[dict[str, Any]]]:
        """Vertices of the DFT hull of each layer, for binary systems only.

        Args:
            sid (str): System id.

        Returns:
            Layer name to vertex list (formation energies in kJ/mol-atom); empty for other systems.
        """
        out: dict[str, list[dict[str, Any]]] = {}
        if len(sid.split("-")) != 2:
            return out
        for layer in dft_layers:
            hull, _ = self._hull(layer, sid)
            if hull:
                out[layer] = [
                    {
                        "formula": v.get("formula"),
                        "x": v.get("x"),
                        "material_id": v.get("phase"),
                        "spacegroup": v.get("spacegroup_symbol"),
                        "formation_energy_kJ_per_mol_atom": round(float(v["energy"]) * kj_per_mol_per_ev, 3),
                        "theoretical": v.get("theoretical"),
                    }
                    for iso in hull.get("isotherms", [])
                    for v in iso.get("vertices", [])
                ]
        return out


def _residual_stats(resid: dict[str, list[float]]) -> dict[str, dict[str, Any]]:
    """Count, mean absolute error and mean residual of DFT minus literature, per source with values.

    Args:
        resid (dict[str, list[float]]): Residuals in kJ/mol-atom per DFT source.

    Returns:
        Source to statistics; sources without residuals are omitted.
    """
    stats_out: dict[str, dict[str, Any]] = {}
    for src, rs in resid.items():
        if rs:
            stats_out[src] = {
                "n": len(rs),
                "MAE_kJ_per_mol_atom": round(statistics.fmean(abs(r) for r in rs), 3),
                "mean_residual": round(statistics.fmean(rs), 3),
            }
    return stats_out
