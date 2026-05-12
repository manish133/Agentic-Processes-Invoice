"""Parse numeric amounts from Indian / international formatted statements."""

from __future__ import annotations

import re


def parse_amount_token(token: str) -> float | None:
    """
    Parse a cell like '4,86,246', '83,098', '(12)', '-', or '—'.
    Parentheses indicate negatives.
    """

    if token is None:
        return None
    t = token.strip()
    if not t or t in {"-", "—", "–", "NA", "N/A"}:
        return None
    neg = "(" in t and ")" in t
    t = re.sub(r"[(),]", "", t)
    t = t.replace(",", "").replace(" ", "")
    if not t:
        return None
    try:
        v = float(t)
        return -abs(v) if neg else v
    except ValueError:
        return None


def split_trailing_amounts(line: str) -> tuple[str, list[str]]:
    """
    Take a line of text and return (label, last two numeric tokens as strings).
    Heuristic for table rows like: 'Cash and cash equivalents 4 83,098 79,811'
    """

    # Strip note column digit(s) before amounts: '... 4 83,098 79,811'
    parts = re.split(r"\s+", line.strip())
    amount_like: list[str] = []
    label_parts: list[str] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if re.match(r"^-?[\d,()]+$", p) or (p.startswith("(") and ")" in p):
            amount_like.append(p)
            i += 1
            continue
        if amount_like:
            # stop accumulating label once we started amounts
            break
        label_parts.append(p)
        i += 1
    # remainder should be more amounts
    while i < len(parts):
        p = parts[i]
        if re.match(r"^-?[\d,()]+$", p) or (p.startswith("(") and ")" in p):
            amount_like.append(p)
        i += 1
    label = " ".join(label_parts).strip()
    return label, amount_like
