"""Pre-filled "Report an error in a value" GitHub issue URLs for ORCHID records.

The URL opens the issue form `.github/ISSUE_TEMPLATE/report-value-error.yml` of orchid-calphad/orchid-data with
the record's non-personal fields filled in: record id, chemical system, source (DOI and page) and the value as
shown. Nothing about the person reporting is ever put in the URL, and nothing is posted: the reader submits the
form on GitHub. The same function (same output for the same input) is in orchid-project.org
(`tools/orchid_report.py`), which puts the link next to every value of the static data pages.

Standard library only; deterministic.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode

ISSUE_URL = "https://github.com/orchid-calphad/orchid-data/issues/new"
TEMPLATE = "report-value-error.yml"
VALUE_MAX = 120
_ID = re.compile(r"^ORC-[A-Za-z0-9.\-]{4,40}$")
_SYS = re.compile(r"^[A-Z]{1,2}(?:-[A-Z]{1,2}){0,7}$")
_DOI = re.compile(r"^10\.\d{4,9}/\S{1,200}$")
_PAGE = re.compile(r"^[A-Za-z]{0,3}\d{1,5}$")


def _clean(s: object, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def report_issue_url(record_id: object, system: object = None, doi: object = None, page: object = None,
                     value: object = None) -> str | None:
    """URL of the pre-filled issue form for one value, or None when record_id is not an ORCHID id.

    Only these fields go into the URL, each validated: record_id (ORC-...), system (CU-ZR), doi (10.xxxx/...),
    page (12, S3) and value (the value as shown, at most 120 characters).
    """
    rid = str(record_id or "").strip()
    if not _ID.match(rid):
        return None
    sysid = str(system or "").strip().upper()
    sysid = sysid if _SYS.match(sysid) else None
    d = re.sub(r"(?i)^(https?://(dx\.)?doi\.org/|doi:)", "", str(doi or "").strip())
    d = d if _DOI.match(d) else None
    pg = str(page).strip() if page is not None and _PAGE.match(str(page).strip()) else None
    params = [("template", TEMPLATE), ("title", "[value] %s%s" % (rid, " (%s)" % sysid if sysid else "")),
              ("record_id", rid)]
    if sysid:
        params.append(("system", sysid))
    if d or pg:
        params.append(("source", ", ".join(x for x in ("doi:%s" % d if d else None, "p. %s" % pg if pg else None) if x)))
    if value not in (None, ""):
        params.append(("value_shown", _clean(value, VALUE_MAX)))
    return ISSUE_URL + "?" + urlencode(params, quote_via=quote, safe="")


REPORT_NOTE = (
    "report_issue_url: a pre-filled GitHub issue ('Report an error in a value') for this value, carrying only its id, "
    "system, DOI, page and value. Give it to the user when a value looks wrong; the user submits the form. The server "
    "never posts anything."
)


def _fmt(v: object) -> str:
    if isinstance(v, float):
        s = repr(float("%.6g" % v))
        return s[:-2] if s.endswith(".0") else s
    return str(v)


def report_url_for(r: dict) -> str | None:
    """report_issue_url of an expanded literature record (value as shown: value and unit, else the temperature)."""
    src = r.get("source") or {}
    loc = r.get("locator") or {}
    page = src.get("page") if src.get("page") is not None else r.get("quote_page") or (loc.get("page") if isinstance(loc, dict) else None)
    v = r.get("value")
    if v is not None and not isinstance(v, bool):
        shown = _fmt(v) + (" " + str(r["unit"]) if r.get("unit") else "")
    elif r.get("temperature_K") is not None:
        shown = _fmt(r["temperature_K"]) + " K"
    else:
        shown = None
    return report_issue_url(r.get("id"), r.get("system"), src.get("doi"), page, shown)
