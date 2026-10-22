"""Element symbols, system identifiers and a conservative formula parser."""

from __future__ import annotations

import re
from fractions import Fraction

ELEMENTS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr "
    "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb "
    "Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Va"
).split()
_BY_UPPER = {e.upper(): e for e in ELEMENTS}

_SYSTEM_RE = re.compile(r"^[A-Za-z]{1,2}([-–_/ ,][A-Za-z]{1,2})*$")


class ChemError(ValueError):
    pass


def element(sym: str) -> str:
    """Return the uppercase ORCHID element token for a symbol in any case."""
    s = sym.strip().upper()
    if s not in _BY_UPPER:
        raise ChemError(f"unknown element symbol: {sym!r}")
    return s


def system_id(system: str | list[str] | tuple[str, ...]) -> str:
    """Normalize 'Ni-Al', 'al ni', ['Ni','Al'] -> 'AL-NI' (sorted, uppercase, '-')."""
    if isinstance(system, (list, tuple)):
        parts = list(system)
    else:
        s = system.strip()
        if not _SYSTEM_RE.match(s):
            # concatenated form such as "AlNi"
            parts = re.findall(r"[A-Z][a-z]?", s)
            if not parts or "".join(parts) != s:
                raise ChemError(f"cannot parse system: {system!r}")
        else:
            parts = re.split(r"[-–_/ ,]+", s)
    toks = sorted({element(p) for p in parts if p})
    if not toks:
        raise ChemError(f"empty system: {system!r}")
    return "-".join(toks)


def pretty_system(sys_id: str) -> str:
    return "-".join(_BY_UPPER[t] for t in sys_id.split("-"))


_TOKEN = re.compile(r"([A-Z][a-z]?)(\d+(?:\.\d+)?|\.\d+)?")
_FORMULA_RUN = re.compile(r"(?:\((?:[A-Z][a-z]?\d*(?:\.\d+)?)+\)\d*(?:\.\d+)?|[A-Z][a-z]?\d*(?:\.\d+)?)+")


def _expand_groups(s: str) -> str:
    """Expand one level of '(AB2)3' groups into 'A3B6'."""

    def repl(m: re.Match[str]) -> str:
        inner, mult = m.group(1), m.group(2)
        k = Fraction(mult) if mult else Fraction(1)
        out = []
        for sym, n in _TOKEN.findall(inner):
            v = (Fraction(n) if n else Fraction(1)) * k
            out.append(sym + (str(float(v)) if v.denominator != 1 else str(v.numerator)))
        return "".join(out)

    return re.sub(r"\(((?:[A-Z][a-z]?\d*(?:\.\d+)?)+)\)(\d+(?:\.\d+)?)?", repl, s)


def parse_formula(text: str) -> dict[str, float] | None:
    """Parse a formula such as 'Al3Ni', 'NiAl', 'Ni(Al)3', 'Al0.75Ni0.25' or a phase
    label such as "γ'-Ni3Al (L12)" into reduced mole fractions {'AL':0.75,'NI':0.25}.

    Returns None when no unambiguous multi-element formula is found (refuses
    rather than guesses: several distinct formulas -> None).
    """
    if not text:
        return None
    found: list[dict[str, float]] = []
    runs: list[str] = []
    for run in _FORMULA_RUN.findall(text):
        runs.append(run)
        if "(" in run:  # also try the plain parts, e.g. "Ni3Al(L12)" -> "Ni3Al"
            runs.extend(r for r in re.split(r"\([^)]*\)\d*(?:\.\d+)?", run) if r)
    for run in runs:
        run = _expand_groups(run)
        toks = _TOKEN.findall(run)
        if "".join(s + n for s, n in toks) != run:
            continue
        amounts: dict[str, Fraction] = {}
        ok = True
        for sym, n in toks:
            if sym not in _BY_UPPER.values() or sym == "Va":
                ok = False
                break
            amounts[sym.upper()] = amounts.get(sym.upper(), Fraction(0)) + (Fraction(n) if n else Fraction(1))
        if not ok or len(amounts) < 2:
            continue
        tot = sum(amounts.values())
        comp = {k: round(float(v / tot), 4) for k, v in sorted(amounts.items())}
        if comp not in found:
            found.append(comp)
    return found[0] if len(found) == 1 else None


def composition_system(comp: dict[str, float]) -> str:
    return "-".join(sorted(comp))
