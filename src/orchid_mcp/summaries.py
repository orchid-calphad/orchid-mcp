"""Short forms of literature records and uncertainty budgets, plus the small value helpers they use."""

from __future__ import annotations

import hashlib
import math
import statistics
from typing import TYPE_CHECKING, Any

from .constants import quote_max
from .report import report_url_for

if TYPE_CHECKING:
    from collections.abc import Iterable

n_folds = 5  # modulus of the machine-learning splits (orchid-project.org tools/ml_folds.py)
budget_components = ("sigma_printed", "sigma_reading", "sigma_composition", "sigma_scatter", "sigma_conversion")
record_flag_keys = ("review_state", "specification_gaps", "state_fully_specified")
record_identity_keys = ("duplicate_of", "evidence_identity", "merged_ids", "merged_pids")


def to_float(x: Any) -> float | None:
    """Finite float value of ``x``.

    Args:
        x (Any): Number or numeric string.

    Returns:
        The value as a float, or None when it is not a finite number.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def stats(values: Iterable[Any]) -> dict[str, Any]:
    """Count, mean, median, extremes and sample standard deviation of the finite values.

    Args:
        values (Iterable[Any]): Values convertible by ``to_float``; non-finite ones are dropped.

    Returns:
        ``{"n": 0}`` when nothing is left, else n, mean, median, min, max (rounded to 4 decimals) and stdev when n > 1.
    """
    vals = [v for v in (to_float(x) for x in values) if v is not None]
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


def shorten(text: Any, n: int = quote_max) -> Any:
    """Cut a string to at most ``n`` characters, ending with an ellipsis when it was cut.

    Args:
        text (Any): Text to shorten; non-strings are returned unchanged.
        n (int): Maximum length in characters, including the ellipsis.

    Returns:
        The shortened string, or ``text`` itself when it is not a string or already fits.
    """
    if not isinstance(text, str) or len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def phase_labels(phases: Any) -> list[str]:
    """Human-readable phase labels of a record, e.g. ``"L12 (L12_FCC)"`` when label and model phase differ.

    Args:
        phases (Any): The record's ``phases`` list (dicts with ``label`` and ``model_phase``, or plain values).

    Returns:
        One string per phase.
    """
    out = []
    for ph in phases or []:
        if not isinstance(ph, dict):
            out.append(str(ph))
            continue
        lab, mp = ph.get("label"), ph.get("model_phase")
        out.append(f"{lab} ({mp})" if lab and mp and mp != lab else (lab or mp or "?"))
    return out


def _budget_quantity(q: dict[str, Any]) -> dict[str, Any]:
    """Compact form of one quantity of an uncertainty budget (values in the quantity's own unit).

    Args:
        q (dict[str, Any]): The quantity's budget entry.

    Returns:
        Unit, component values, the method floor with class and basis, and sigma_effective.
    """
    e: dict[str, Any] = {"unit": q.get("unit")}
    for k in budget_components:
        c = q.get(k)
        e[k] = c.get("value") if isinstance(c, dict) else None
    m = q.get("sigma_method") or {}
    e["sigma_method"] = {"value": m.get("value"), "class": m.get("class"), "basis": m.get("basis")}
    if m.get("value") is None and m.get("reason"):
        e["sigma_method"]["reason"] = m["reason"]
    e["sigma_effective"] = q.get("sigma_effective")
    if q.get("sigma_effective") is None and q.get("sigma_effective_reason"):
        e["sigma_effective_reason"] = q["sigma_effective_reason"]
    return e


def summarize_budget(b: Any) -> dict[str, Any] | None:
    """Compact form of a record's ``uncertainty_budget`` (literature SCHEMA.md).

    Args:
        b (Any): The record's ``uncertainty_budget``.

    Returns:
        Quantities (sorted by name), the sigma rule and the calibration id, or None when the budget has no quantities.
    """
    if not isinstance(b, dict) or not b.get("quantities"):
        return None
    qs = {name: _budget_quantity(q) for name, q in sorted(b["quantities"].items()) if isinstance(q, dict)}
    return {"quantities": qs, "sigma_rule": b.get("sigma_rule"), "calibration": b.get("calibration")}


def ml_fold(key: Any) -> int | None:
    """Machine-learning fold of a key: ``int(sha256(key), 16) mod 5``.

    Args:
        key (Any): Chemical system or paper id; falsy values have no fold.

    Returns:
        Fold index in 0..4, or None for an empty key.
    """
    return None if not key else int(hashlib.sha256(str(key).encode("utf-8")).hexdigest(), 16) % n_folds


def ml_folds(r: dict[str, Any]) -> dict[str, Any]:
    """Machine-learning split keys of a record, taken from the release or recomputed with the same rule.

    Args:
        r (dict[str, Any]): Expanded literature record.

    Returns:
        chemical_system, paper_id, fold_lso (leave-chemical-system-out) and fold_lpo (leave-paper-out); plus
        source_system when the record was reported in a different system than its paper's.
    """
    cs = r.get("chemical_system") or r.get("system")
    pid = r.get("paper_id") or r.get("paper") or (r.get("source") or {}).get("paper")
    out = {
        "chemical_system": cs,
        "paper_id": pid,
        "fold_lso": r["fold_lso"] if r.get("fold_lso") is not None else ml_fold(cs),
        "fold_lpo": r["fold_lpo"] if r.get("fold_lpo") is not None else ml_fold(pid),
    }
    if r.get("source_system") and r.get("source_system") != cs:
        # since audit round 2 (A1 C4) the system is that of the record's own state; the paper's system is kept
        out["source_system"] = r["source_system"]
    return out


def _record_head(r: dict[str, Any]) -> dict[str, Any]:
    """Identity, value, unit, temperature and phases of a record, plus its reaction when it has one.

    Args:
        r (dict[str, Any]): Expanded literature record.

    Returns:
        The first block of the summary, in output order.
    """
    t = r.get("temperature_K")
    if t is None and r.get("unit") == "K":
        t = r.get("value")
    out: dict[str, Any] = {
        "id": r.get("id"),
        "property": r.get("property"),
        "value": r.get("value"),
        "unit": r.get("unit"),
        "T_K": t,
        "phases": phase_labels(r.get("phases")),
    }
    rx = r.get("reaction")
    if isinstance(rx, dict) and (rx.get("type") or rx.get("printed")):
        out["reaction"] = ": ".join(str(x) for x in (rx.get("type"), rx.get("printed")) if x)
    return out


def _record_status(r: dict[str, Any]) -> dict[str, Any]:
    """Evidence class, acceptance, ESPEI readiness and citation of a record.

    ESPEI semantics of the release: ready = usable in ESPEI (complete state, phase mapped, physical phase rule);
    certified = ready AND agreement with a CALPHAD (pycalphad) prediction.

    Args:
        r (dict[str, Any]): Expanded literature record with ``source`` and ``flags``.

    Returns:
        The status and citation block of the summary.
    """
    src = r.get("source") or {}
    flags = r.get("flags") or {}
    return {
        "evidence_class": r.get("evidence_class"),
        "accepted": bool(flags.get("extraction_accepted")),
        "espei_ready": bool(flags.get("espei_ready")),
        "espei_certified": bool(flags.get("espei_certified")),
        "calphad_check": flags.get("calphad_check") or "not_available",
        "citation": src.get("citation"),
        "doi": src.get("doi"),
        "page": src.get("page"),
        "quote": shorten(src.get("quote")),
    }


def _review_corrections(r: dict[str, Any]) -> list[dict[str, Any]]:
    """Corrections adjudicated from source review, kept with their before/after values and provenance.

    Args:
        r (dict[str, Any]): Expanded literature record.

    Returns:
        The matching corrections (empty when there are none).
    """
    return [c for c in r.get("corrections") or [] if isinstance(c, dict) and c.get("rule") == "source_adjudicated_human_review"]


def summarize_record(r: dict[str, Any]) -> dict[str, Any]:
    """Short form of an expanded literature record (see ``fields_note`` in ``constants``).

    Args:
        r (dict[str, Any]): Expanded literature record.

    Returns:
        Summary keys in a fixed order; empty optional blocks are omitted, and a table locator replaces the quote
        when the value has no sentence quote.
    """
    src = r.get("source") or {}
    loc = r.get("locator") or {}
    out = _record_head(r)
    out.update(_record_status(r))
    out.update(ml_folds(r))
    flags = r.get("flags") or {}
    for key in record_flag_keys:
        if key in flags:
            out[key] = flags[key]
    for key in record_identity_keys:
        if key in r:
            out[key] = r[key]
    review = _review_corrections(r)
    if review:
        # Preserve before/after values and source/adjudicator provenance without relabelling it as human validation.
        out["human_review_corrections"] = review
    out["report_issue_url"] = r.get("report_issue_url") or report_url_for(r)
    # literature-level consistency gates: codes of the gates that demoted / flagged the value, rules that corrected it
    gf = [g.get("code") for g in r.get("gate_failures") or [] if isinstance(g, dict) and g.get("code")]
    if gf:
        out["gate_failures"] = gf
    cr = [c.get("rule") for c in r.get("corrections") or [] if isinstance(c, dict) and c.get("rule")]
    if cr:
        out["corrected_by"] = cr
    ub = summarize_budget(r.get("uncertainty_budget"))
    if ub:
        out["uncertainty_budget"] = ub
    if src.get("quote") is None and isinstance(loc, dict) and loc.get("label") is not None:
        kind = loc.get("artifact") or "table"
        out["table"] = f"{kind} {loc.get('label')}" + (f", row {loc['row']}" if loc.get("row") is not None else "")
    return out
