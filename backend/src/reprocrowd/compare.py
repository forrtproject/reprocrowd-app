"""Checking outputs/results.json and comparing reproduced values with the targets.

Rules (as in the MetaReader plugin):
- numbers are rounded to the decimals reported in the paper (or a fixed number) before comparing; the
  comparator applies (< and > compare unrounded); a deviation up to ``threshold`` % is "within tolerance";
- table targets are compared cell by cell (numeric cells as numbers, text cells as normalized text); a
  reproduced table may omit the header row and/or the label column;
- categorical targets match on normalized text; figure targets can only be compared visually.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .numbers import parse_printed, reproduced_decimal, round_to, compare_num, quantum

import math
import re
from typing import Any

from pydantic import ValidationError

from .errors import pydantic_errors
from .schemas.comparison import Cell, Comparison, CompareRow, CompareSettings, Status
from .schemas.curation import Target
from .schemas.exports import OutputResult, ReproducedOutput


def parse_num(s: Any) -> float | None:
    """A number as printed in papers: unicode minus, thousands separators, %, leading < > = ~."""
    try:
        return float(parse_printed(s).value)
    except (ValueError, InvalidOperation):
        return None


def decimals_of(s: Any) -> int:
    try:
        return max(0, -quantum(s))
    except ValueError:
        return 0


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str("" if s is None else s).lower()).strip()


def _fmt(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return str(int(x)) if x.is_integer() else f"{x:.8g}"
    return str(x)


def _finite(x: float | None) -> float | None:
    return x if x is not None and math.isfinite(x) else None


def _compare_table(exp: list[list[str]], rep: list[list[Any]], st: CompareSettings) -> tuple[list[list[Cell]], int, int]:
    row_off = 1 if len(rep) == len(exp) - 1 else 0
    col_off = 1 if rep and exp and len(rep[0]) == len(exp[0]) - 1 else 0
    matched = total = 0
    cells: list[list[Cell]] = []
    for i, row in enumerate(exp):
        out_row = []
        for j, e in enumerate(row):
            given = i < row_off or j < col_off  # header / label cells are taken as given
            rv = e if given else (rep[i - row_off][j - col_off] if i - row_off < len(rep) and j - col_off < len(rep[i - row_off]) else None)
            en, rn = parse_num(e), parse_num(rv)
            deviation = None
            if en is not None:
                total += 1
                if rn is None:
                    status: Status = "missing"
                else:
                    printed = parse_printed(e)
                    status, deviation = compare_num(printed.value, reproduced_decimal(rv), printed.comparator or "=",
                                                    printed.quantum if st.rounding == "reported" else -st.rounding, st.threshold)
                if status == "match":
                    matched += 1
            else:
                status = "match" if not _norm(e) or _norm(e) == _norm(rv) else "unparsable"
            out_row.append(Cell(expected=e, reproduced=_fmt(rv), status=status, deviation=_finite(deviation),
                                 printed=parse_printed(e).token if en is not None else None, comparator=parse_printed(e).comparator if en is not None else None,
                                 quantum=quantum(e) if en is not None else None))
        cells.append(out_row)
    return cells, matched, total


def compare_target(t: Target, out: OutputResult | None, st: CompareSettings) -> CompareRow:
    if t.type == "table":
        expected_text = f"{len(t.expected or [])} rows"
    elif t.type == "figure":
        expected_text = "(figure)"
    else:
        expected_text = f"{t.comparator + ' ' if t.comparator and t.comparator != '=' else ''}{t.printed or _fmt(t.expected)}{' ' + t.unit if t.unit else ''}"
    base = {"target": t.id, "type": t.type, "description": t.description, "expected_text": expected_text, "note": out.note if out else None, "refs": t.refs}
    if t.type == "figure":
        return CompareRow(**base, status="figure" if out and out.file else "missing", reproduced_text=out.file if out and out.file else "—")
    if out is None:
        return CompareRow(**base, status="missing", reproduced_text="—")
    if t.type == "table":
        if out.table is None:
            return CompareRow(**base, status="missing", reproduced_text="—")
        cells, matched, total = _compare_table(t.expected, out.table, st)
        flat = [c.status for row in cells for c in row]
        status = "deviation" if any(s in ("deviation", "missing", "unparsable") for s in flat) else "within" if "within" in flat else "match"
        return CompareRow(**base, status=status, reproduced_text=f"{matched}/{total} numeric cells match", cells=cells, matched=matched, total=total)
    if out.value is None:
        return CompareRow(**base, status="missing", reproduced_text="—")
    if t.type == "categorical":
        return CompareRow(**base, status="match" if _norm(out.value) == _norm(t.expected) else "deviation", reproduced_text=_fmt(out.value))
    e, r = parse_num(t.expected), parse_num(out.value)
    if e is None or r is None:
        return CompareRow(**base, status="unparsable", reproduced_text=_fmt(out.value))
    printed = parse_printed(t.printed if t.printed is not None else repr(t.expected))
    exponent = printed.quantum if t.printed is not None or t.decimals is None else -t.decimals
    used_exponent = exponent if st.rounding == "reported" else -st.rounding
    status, dev = compare_num(printed.value, reproduced_decimal(out.value), t.comparator or "=", used_exponent, st.threshold)
    rounded = round_to(reproduced_decimal(out.value), used_exponent)
    suffix = "" if st.rounding == "reported" else f"; rounded to {st.rounding} decimals (setting)"
    return CompareRow(**base, status=status, deviation=_finite(dev), reproduced_text=f"{rounded:f}  ({_fmt(out.value)}){suffix}",
                      precision="printed" if t.printed is not None else "explicit" if t.decimals is not None else "inferred", quantum=exponent)



def check_output(raw: Any, targets: list[Target], plan=None) -> tuple[list[str], list[str], ReproducedOutput | None]:
    """Errors: the file doesn't have the required format; warnings: targets not covered, unknown ids."""
    try:
        output = ReproducedOutput.model_validate(raw)
    except ValidationError as e:
        return pydantic_errors(e), [], None
    errors, warnings = [], []
    by_id = {t.id: t for t in targets}
    seen: set[str] = set()
    for i, r in enumerate(output.results):
        at = f"results[{i}] ({r.id})"
        if r.id in seen:
            errors.append(f"{at}: duplicate id")
        seen.add(r.id)
        t = by_id.get(r.id)
        if plan is not None and r.step:
            steps = {s.id: s for s in plan.steps}
            if r.step not in steps or r.id not in steps[r.step].produces:
                warnings.append(f"{at}: step {r.step} does not produce {r.id} in the plan")
        if t is None:
            warnings.append(f"{at}: not a target")
        elif t.type == "number" and r.value is not None and parse_num(r.value) is None:
            errors.append(f"{at}: number target needs a numeric value")
        elif t.type == "table" and r.table is None and r.value is not None:
            errors.append(f"{at}: table target needs \"table\"")
        elif t.type == "figure" and r.value is not None and not r.file:
            errors.append(f"{at}: figure target needs \"file\"")
    warnings += [f"{t.id}: precision inferred from an older numeric target; original trailing zeroes are unknown" for t in targets if t.type == "number" and t.printed is None and t.decimals is None]
    warnings += [f"{t}: missing from the output" for t in by_id if t not in seen]
    return errors, warnings, output


def compare(targets: list[Target], raw_output: Any, st: CompareSettings) -> Comparison:
    errors, warnings, output = check_output(raw_output, targets)
    outs = {r.id: r for r in (output.results if output else [])}
    rows = [compare_target(t, outs.get(t.id), st) for t in targets]
    summary: dict[str, int] = {}
    for r in rows:
        summary[r.status] = summary.get(r.status, 0) + 1
    return Comparison(settings=st, summary=summary, rows=rows, output_errors=errors, output_warnings=warnings)
