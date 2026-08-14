"""Panel construction must not manufacture moves out of calendar edges."""
import pandas as pd
import pytest

from app.analytics.panels import build_panel


def daily(start, end, units=("A", "B"), value=100.0):
    rows = [{"d": day, "u": unit, "v": value}
            for day in pd.date_range(start, end, freq="D") for unit in units]
    return pd.DataFrame(rows)


def test_partial_boundary_weeks_are_dropped():
    """Feb 1 2025 is a Saturday, so the first ISO week holds two days. Kept, it
    reads as a 70% collapse in week one."""
    build = build_panel(daily("2025-02-01", "2025-03-31"), date_col="d",
                        unit_col="u", value_col="v", grain="week")
    assert build.dropped_periods
    weekly_totals = build.frame.groupby("period")["outcome"].sum()
    assert weekly_totals.min() == weekly_totals.max()


def test_note_explains_the_drop():
    build = build_panel(daily("2025-02-01", "2025-03-31"), date_col="d",
                        unit_col="u", value_col="v", grain="week")
    assert "calendar edge" in build.as_note()


def test_complete_ranges_drop_nothing():
    build = build_panel(daily("2025-02-03", "2025-03-30"), date_col="d",
                        unit_col="u", value_col="v", grain="week")
    assert build.dropped_periods == []


def test_interior_gaps_are_preserved():
    """A hole in the middle is a data problem the caller must see, not
    something to quietly delete."""
    frame = daily("2025-02-03", "2025-03-30")
    frame = frame[~frame.d.between("2025-02-17", "2025-02-23")]
    build = build_panel(frame, date_col="d", unit_col="u", value_col="v",
                        grain="week")
    assert build.dropped_periods == []
    assert build.kept_periods == 7


def test_daily_grain_drops_nothing():
    build = build_panel(daily("2025-02-01", "2025-02-10"), date_col="d",
                        unit_col="u", value_col="v", grain="day")
    assert build.dropped_periods == [] and build.kept_periods == 10


def test_month_grain_uses_real_month_lengths():
    build = build_panel(daily("2025-01-15", "2025-04-30"), date_col="d",
                        unit_col="u", value_col="v", grain="month")
    assert "2025-01-01" in build.dropped_periods


def test_unknown_grain_is_rejected():
    with pytest.raises(ValueError, match="grain"):
        build_panel(daily("2025-02-03", "2025-03-30"), date_col="d",
                    unit_col="u", value_col="v", grain="fortnight")


def test_missing_column_is_named():
    with pytest.raises(ValueError, match="missing column 'nope'"):
        build_panel(daily("2025-02-03", "2025-03-30"), date_col="nope",
                    unit_col="u", value_col="v")
