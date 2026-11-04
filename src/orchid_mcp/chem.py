"""Element symbols, system identifiers and a conservative formula parser."""

from __future__ import annotations

import re
from fractions import Fraction

elements = (  # noqa: SIM905 (one symbol table, split for readability)
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr "
    "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb "
    "Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Va"
).split()
_by_upper = {e.upper(): e for e in elements}

_system_re = re.compile(r"^[A-Za-z]{1,2}([-–_/ ,][A-Za-z]{1,2})*$")
_token = re.compile(r"([A-Z][a-z]?)(\d+(?:\.\d+)?|\.\d+)?")
_formula_run = re.compile(r"(?:\((?:[A-Z][a-z]?\d*(?:\.\d+)?)+\)\d*(?:\.\d+)?|[A-Z][a-z]?\d*(?:\.\d+)?)+")


class ChemError(ValueError):
    """Raised when an element symbol or a system or formula string cannot be parsed."""


def element(sym: str) -> str:
    """Return the uppercase ORCHID element token for a symbol in any case.

    Args:
        sym (str): Element symbol, e.g. ``"ni"`` or ``"Ni"``.

    Returns:
        The upper-case token, e.g. ``"NI"``.

    Raises:
        ChemError: When the symbol is not a known element.
    """
    s = sym.strip().upper()
    if s not in _by_upper:
        raise ChemError(f"unknown element symbol: {sym!r}")
    return s


def system_id(system: str | list[str] | tuple[str, ...]) -> str:
    """Normalize a system to its sorted, upper-case, hyphen-joined id.

    Args:
        system (str | list[str] | tuple[str, ...]): ``'Ni-Al'``, ``'al ni'``, ``'AlNi'`` or ``['Ni', 'Al']``.

    Returns:
        The id, e.g. ``'AL-NI'``.

    Raises:
        ChemError: When the string cannot be parsed or contains no element.
    """
    if isinstance(system, (list, tuple)):
        parts = list(system)
    else:
        s = system.strip()
        if not _system_re.match(s):
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
    """Display form of a system id, e.g. ``'AL-NI'`` -> ``'Al-Ni'``.

    Args:
        sys_id (str): Normalised system id.

    Returns:
        The id with each token in its element-symbol case.
    """
    return "-".join(_by_upper[t] for t in sys_id.split("-"))


def _expand_groups(s: str) -> str:
    """Expand one level of '(AB2)3' groups into 'A3B6'.

    Args:
        s (str): Formula text that may contain parenthesised groups.

    Returns:
        The formula with each group written out.
    """

    def repl(m: re.Match[str]) -> str:
        """Expand one matched group.

        Args:
            m (re.Match[str]): Match of a parenthesised group and its multiplier.

        Returns:
            The expanded group.
        """
        inner, mult = m.group(1), m.group(2)
        k = Fraction(mult) if mult else Fraction(1)
        out = []
        for sym, n in _token.findall(inner):
            v = (Fraction(n) if n else Fraction(1)) * k
            out.append(sym + (str(float(v)) if v.denominator != 1 else str(v.numerator)))
        return "".join(out)

    return re.sub(r"\(((?:[A-Z][a-z]?\d*(?:\.\d+)?)+)\)(\d+(?:\.\d+)?)?", repl, s)


def _amounts(run: str) -> dict[str, Fraction] | None:
    """Element amounts of one formula run, or None when the run is not a plain formula of known elements.

    Args:
        run (str): Formula run without parentheses.

    Returns:
        Upper-case element to amount, or None when the run is invalid or has fewer than two elements.
    """
    toks = _token.findall(run)
    if "".join(s + n for s, n in toks) != run:
        return None
    amounts: dict[str, Fraction] = {}
    for sym, n in toks:
        if sym not in _by_upper.values() or sym == "Va":
            return None
        amounts[sym.upper()] = amounts.get(sym.upper(), Fraction(0)) + (Fraction(n) if n else Fraction(1))
    return amounts if len(amounts) >= 2 else None


def parse_formula(text: str) -> dict[str, float] | None:
    """Parse a formula into reduced mole fractions.

    Accepts formulas such as ``'Al3Ni'``, ``'NiAl'``, ``'Ni(Al)3'``, ``'Al0.75Ni0.25'`` or a phase label such as
    ``"γ'-Ni3Al (L12)"``. Refuses rather than guesses: several distinct formulas give None.

    Args:
        text (str): Formula or phase label.

    Returns:
        Element to mole fraction (rounded to 4 decimals, sorted by element), or None.
    """
    if not text:
        return None
    found: list[dict[str, float]] = []
    runs: list[str] = []
    for run in _formula_run.findall(text):
        runs.append(run)
        if "(" in run:  # also try the plain parts, e.g. "Ni3Al(L12)" -> "Ni3Al"
            runs.extend(r for r in re.split(r"\([^)]*\)\d*(?:\.\d+)?", run) if r)
    for raw in runs:
        amounts = _amounts(_expand_groups(raw))
        if amounts is None:
            continue
        tot = sum(amounts.values())
        comp = {k: round(float(v / tot), 4) for k, v in sorted(amounts.items())}
        if comp not in found:
            found.append(comp)
    return found[0] if len(found) == 1 else None


def composition_system(comp: dict[str, float]) -> str:
    """System id of a composition, e.g. ``{'AL': 0.25, 'NI': 0.75}`` -> ``'AL-NI'``.

    Args:
        comp (dict[str, float]): Mole fractions keyed by upper-case element token.

    Returns:
        The sorted, hyphen-joined element tokens.
    """
    return "-".join(sorted(comp))
