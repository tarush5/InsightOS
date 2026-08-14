"""Coercion to JSON-safe primitives, at both boundaries."""
import datetime
import json

import numpy as np
import pandas as pd
import pytest

from app.core.serialization import jsonable


@pytest.mark.parametrize("value,expected", [
    (np.bool_(True), True),
    (np.bool_(False), False),
    (np.float64(0.5), 0.5),
    (np.int64(7), 7),
    (np.float32(1.5), 1.5),
    (datetime.date(2025, 8, 1), "2025-08-01"),
    (pd.Timestamp("2025-08-01"), "2025-08-01T00:00:00"),
    (np.array([1.0, 2.0]), [1.0, 2.0]),
    (np.array([[1, 2], [3, 4]]), [[1, 2], [3, 4]]),
    ({"a", "b"}, ["a", "b"]),
])
def test_values_become_json_types(value, expected):
    result = jsonable(value)
    if isinstance(expected, list):
        assert sorted(map(str, result)) == sorted(map(str, expected))
    else:
        assert result == expected
    json.dumps(result)


def test_a_numpy_bool_becomes_a_real_bool():
    """`np.bool_` is what broke serialisation in production: it passes an
    `isinstance(x, int)` check and fails to serialise."""
    assert jsonable(np.bool_(True)) is True


def test_an_array_is_a_list_not_its_repr():
    """`item()` raises on a multi-element array; catching that and falling back
    on `str` turned an array into the literal text '[1. 2.]'."""
    assert jsonable(np.array([1.0, 2.0])) == [1.0, 2.0]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                 np.float64("nan")])
def test_non_finite_floats_become_null(bad):
    """json.dumps emits NaN and Infinity, which are valid Python and invalid
    JSON, and some parsers silently read them as null anyway."""
    assert jsonable(bad) is None
    assert json.dumps(jsonable(bad)) == "null"


def test_nested_structures_are_coerced_throughout():
    payload = {"drivers": [{"segment": "South", "sig": np.bool_(True),
                            "pct": np.float64(-0.15)}],
               "as_of": datetime.date(2025, 8, 1)}
    assert json.loads(json.dumps(jsonable(payload))) == {
        "drivers": [{"segment": "South", "sig": True, "pct": -0.15}],
        "as_of": "2025-08-01"}


def test_plain_types_pass_through_untouched():
    payload = {"a": 1, "b": "two", "c": True, "d": None, "e": [1, 2]}
    assert jsonable(payload) == payload


def test_an_unserialisable_object_becomes_a_string_not_an_error():
    class Opaque:
        def __repr__(self):
            return "<opaque>"

    assert jsonable(Opaque()) == "<opaque>"


@pytest.mark.asyncio
async def test_investigation_events_serialise_over_the_wire():
    """The regression: the repository coerced for storage and the HTTP response
    returned the in-memory dict untouched, so a value stored fine and failed to
    send."""
    from tests.test_orchestrator_e2e import _run  # reuse the fixture's provider

    import numpy as np
    import pandas as pd

    rows = []
    for period, factor in (("2025-07-01", 1.0), ("2025-08-01", 0.6)):
        for day in range(28):
            for region in ("North", "South"):
                rows.append({"order_date": pd.Timestamp(period) + pd.Timedelta(days=day),
                             "region": region, "segment": "Enterprise",
                             "channel": "Web", "revenue": 10_000.0 * factor})
    frame = pd.DataFrame(rows)

    def provider(metric, start, end):
        mask = ((frame.order_date >= pd.Timestamp(start)) &
                (frame.order_date <= pd.Timestamp(end)))
        return frame.loc[mask].copy()

    events = await _run(provider)
    for event in events:
        json.dumps(event.as_dict())      # raises if any numpy leaked through
