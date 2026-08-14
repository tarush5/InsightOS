"""
Coercion to JSON-safe primitives.

The analytics layer works in numpy and pandas, so the values that come out of it
are `np.float64`, `np.bool_`, `pd.Timestamp` and friends. Those are not JSON
types. Both boundaries out of the system -- the HTTP response and the JSON
columns in the database -- have to coerce them, and they must coerce them the
same way, or a value round-trips through storage correctly and fails on the
wire.

This module exists because they did not. Storage had its own coercion and the
API response returned the in-memory dict untouched, which worked for months and
then failed the moment a code path returned `np.bool_` instead of a Python
`bool`. One function, used at both boundaries.
"""
from __future__ import annotations

import datetime as _dt
import decimal as _dec
from typing import Any


def jsonable(value: Any) -> Any:
    """Recursively convert to types `json.dumps` accepts.

    Order matters: `bool` is checked before the `.item()` branch because
    `np.bool_` has `item()` and returns a Python bool, while a plain `bool`
    does not -- and `int`/`float` subclasses from numpy must reach `.item()`
    rather than being passed through as-is.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        # Guard against non-finite floats, which json.dumps emits as NaN and
        # Infinity -- valid Python, invalid JSON, and silently accepted by some
        # parsers as null.
        if isinstance(value, float) and value != value:      # NaN
            return None
        if isinstance(value, float) and value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if isinstance(value, (_dt.date, _dt.datetime, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return value.total_seconds()
    if isinstance(value, _dec.Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    # `tolist` is tried before `item` because a multi-element array has both:
    # `item()` raises on it, and catching that to fall back on `str(value)`
    # turned an array into the literal text "[1. 2.]".
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return jsonable(tolist())
        except (ValueError, AttributeError, TypeError):
            pass
    # numpy scalars, pandas Timestamps and anything else exposing .item()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return jsonable(item())
        except (ValueError, AttributeError, TypeError):
            pass
    return str(value)
