"""Printed numbers and reported-precision comparisons. Pure; no filesystem or provider code.

Expected tokens and reproduced values are both read as decimals: a JSON float is taken
as its shortest round-trip text (2.675, not its binary approximation 2.67499...).
At the printed precision a value matches if rounding it half-up or half-down gives the
printed number, so exact ties match either neighbour (2.675 -> 2.67 or 2.68).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, localcontext
from typing import Any

from pydantic_core import PydanticCustomError


@dataclass(frozen=True)
class Printed:
    token: str
    value: Decimal
    quantum: int
    comparator: str | None = None


def parse_printed(raw: Any, comparator: str | None = None) -> Printed:
    if isinstance(raw, bool) or raw is None:
        raise ValueError("not a finite printed number")
    text = repr(raw) if isinstance(raw, float) else str(raw).strip()
    text = re.sub(r"[−–—]", "-", text)
    text = re.sub(r"[\s%*†‡§]", "", text)
    op = re.match(r"^(<=|>=|[<>=~≤≥≈])", text)
    found = op.group() if op else None
    found = {"≤": "<=", "≥": ">=", "≈": "~"}.get(found, found)
    if found and comparator and found != comparator:
        raise PydanticCustomError("target_contradiction", "printed and explicit comparators differ")
    if op:
        text = text[op.end():]
    # Only well-formed thousands grouping; a decimal comma ("0,512", "1,5") must never become an integer.
    if "," in text:
        if not re.fullmatch(r"[-+]?[1-9]\d{0,2}(?:,\d{3})+(?:\.\d*)?(?:[eE][-+]?\d+)?", text):
            raise ValueError(f"not a printed number (decimal comma or malformed thousands grouping): {raw!r}")
        text = text.replace(",", "")
    if not re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", text):
        raise ValueError(f"not a printed number: {raw!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise ValueError("not a representable printed number") from error
    if not value.is_finite() or not math.isfinite(float(value)) or (value != 0 and float(value) == 0):
        raise ValueError("not a finite representable number")
    return Printed(text, value, int(value.as_tuple().exponent), found or comparator)


def normalize_number(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    printed, expected = data.get("printed"), data.get("expected")
    if data.get("type") != "number":
        if printed is not None:
            raise PydanticCustomError("target_contradiction", "printed is only valid for number targets")
        return data
    if printed is None and not isinstance(expected, str):
        if data.get("comparator") == "=":
            data["comparator"] = None
        return data  # legacy numeric values: precision may already have been lost
    parsed = parse_printed(printed if printed is not None else expected, data.get("comparator"))
    if printed is not None and expected is not None:
        supplied = parse_printed(expected, data.get("comparator"))
        # Numeric expected is a derived float in saved packages; compare its float
        # projection so a long printed decimal can be loaded without losing its token.
        differs = supplied.value != parsed.value if isinstance(expected, str) else float(supplied.value) != float(parsed.value)
        if differs:
            raise PydanticCustomError("target_contradiction", "printed and expected values differ")
    decimals = -parsed.quantum if parsed.quantum <= 0 else None
    if data.get("decimals") is not None and data["decimals"] != decimals:
        raise PydanticCustomError("target_contradiction", "decimals contradict the printed precision")
    data.update(printed=parsed.token, expected=float(parsed.value), decimals=decimals,
                comparator=None if parsed.comparator in (None, "=") else parsed.comparator)
    return data


def quantum(value: Any) -> int:
    return parse_printed(value).quantum


def reproduced_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a reproduced number")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite result")
        return Decimal(repr(value))  # the decimal the analysis wrote, not its binary approximation
    return parse_printed(value).value


def round_to(value: Decimal, exponent: int, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = max(50, len(value.as_tuple().digits) + abs(exponent) + abs(value.adjusted()) + 10)
        return value.quantize(Decimal(1).scaleb(exponent), rounding=rounding)


def rounded_both(value: Decimal, exponent: int) -> tuple[Decimal, Decimal]:
    """The value rounded at a tie towards and away from zero; equal unless it is an exact tie."""
    return round_to(value, exponent, ROUND_HALF_DOWN), round_to(value, exponent, ROUND_HALF_UP)


def compare_num(expected: Decimal, reproduced: Decimal, comparator: str, exponent: int,
                threshold: float) -> tuple[str, float]:
    rounded, wanted = set(rounded_both(reproduced, exponent)), set(rounded_both(expected, exponent))
    deviation = float(abs(reproduced - expected) / abs(expected) * 100) if expected else (0.0 if not reproduced else math.inf)
    # Either tie direction is accepted; strict bounds compare unrounded values.
    matches = {"<": reproduced < expected, ">": reproduced > expected,
               "<=": min(rounded) <= max(wanted), ">=": max(rounded) >= min(wanted)}.get(comparator, bool(rounded & wanted))
    if matches:
        return "match", deviation
    if comparator not in ("<", ">") and deviation <= threshold:
        return "within", deviation
    return "deviation", deviation
