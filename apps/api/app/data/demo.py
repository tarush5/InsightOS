"""
The demo warehouse: CSVs produced by ``scripts/seed_data.py``.

This is the only place that knows the demo is a demo. Everything downstream --
orchestrator, alert engine, causal estimator -- receives a DataFrame and cannot
tell whether it came from these files or from a customer's Postgres. Swapping
in ``SQLExecutor`` replaces this provider and nothing else.
"""
from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd

def resolve_seed_dir() -> Path:
    env_dir = os.getenv("SEED_DIR")
    if env_dir:
        p = Path(env_dir)
        if (p / "orders.csv").exists():
            return p

    cwd = Path.cwd()
    candidates = [
        Path("/app/seed"),
        cwd / "seed",
        cwd / "apps" / "api" / "seed",
        cwd.parent / "seed",
        Path("/data/seed"),
        Path(__file__).resolve().parents[3] / "seed",
        Path(__file__).resolve().parents[2] / "seed",
        Path(__file__).resolve().parents[1] / "seed",
    ]
    for c in candidates:
        if c.exists() and (c / "orders.csv").exists():
            return c

    return Path(env_dir) if env_dir else Path("/app/seed")


DATE_COLUMNS: dict[str, list[str]] = {
    "orders": ["order_date"],
    "support_tickets": ["created_date"],
    "customers": ["signup_date", "churn_date"],
    "refunds": ["refund_date"],
    "subscriptions": ["start_date", "end_date"],
    "marketing_campaigns": ["start_date", "end_date"],
}
DEMO_TABLES = tuple(DATE_COLUMNS) + ("order_items", "products", "inventory")

VALUE_COLUMNS: dict[str, str] = {
    "revenue": "total_amount",
    "order_count": "order_id",
    "support_first_response_hours": "first_response_hours",
    "csat": "csat_score",
}


class SeedDataMissing(FileNotFoundError):
    """Raised with the exact command that fixes it."""


@lru_cache(maxsize=12)
def load_table(table: str) -> pd.DataFrame:
    seed_dir = resolve_seed_dir()
    path = seed_dir / f"{table}.csv"
    if not path.exists():
        raise SeedDataMissing(
            f"Seed table '{table}' not found at {path}. Generate it with:\n"
            f"  python scripts/seed_data.py --out ./seed")
    parse = [c for c in DATE_COLUMNS.get(table, [])]
    frame = pd.read_csv(path, parse_dates=parse)
    return frame


def demo_data_provider(metric, start: date, end: date) -> pd.DataFrame:
    """Return one row per (date, dimensions) with the metric already aggregated."""
    src = load_table(metric.base_table)
    dc = metric.date_column
    if dc not in src.columns:
        raise ValueError(f"Metric '{metric.key}' expects a '{dc}' column on "
                         f"'{metric.base_table}', which is not present.")
    mask = (src[dc] >= pd.Timestamp(start)) & (src[dc] <= pd.Timestamp(end))
    df = src.loc[mask].copy()
    if metric.base_table == "orders" and "status" in df.columns:
        df = df[df["status"] == "completed"]

    value = VALUE_COLUMNS.get(metric.key)
    if value is None or value not in df.columns:
        raise ValueError(
            f"Metric '{metric.key}' is not available in the demo warehouse. "
            f"Available: {', '.join(sorted(VALUE_COLUMNS))}.")

    dims = [d for d in metric.dimensions if d in df.columns]
    grouped = df.groupby([dc, *dims], as_index=False)
    if metric.key == "order_count":
        out = grouped[value].count()
    elif getattr(metric.aggregation, "value", metric.aggregation) == "avg":
        out = grouped[value].mean()
    else:
        out = grouped[value].sum()
    return out.rename(columns={value: metric.key})


def metric_series(metric, start: date, end: date,
                  segment: dict[str, str] | None = None) -> pd.Series:
    """A single daily series for one metric, optionally filtered to a segment.
    Used by the alert engine, which evaluates one series at a time."""
    frame = demo_data_provider(metric, start, end)
    for column, value in (segment or {}).items():
        if column not in frame.columns:
            raise ValueError(f"Unknown segment column '{column}' for "
                             f"metric '{metric.key}'.")
        frame = frame[frame[column].astype(str) == str(value)]
    if frame.empty:
        return pd.Series(dtype="float64")
    dc = metric.date_column
    agg = "mean" if getattr(metric.aggregation, "value",
                            metric.aggregation) == "avg" else "sum"
    series = frame.groupby(dc)[metric.key].agg(agg)
    series.index = pd.to_datetime(series.index)
    return series.sort_index()


@lru_cache(maxsize=32)
def latest_available_date(table: str, date_column: str) -> date | None:
    """The most recent date the warehouse actually holds.

    Evaluation windows anchored on ``today`` return nothing against a fixed
    demo dataset, and an empty window is easy to misread as "no alerts fired".
    Callers anchor on this instead and report the anchor, so a stale warehouse
    is visible rather than silently producing quiet results.
    """
    try:
        frame = load_table(table)
    except SeedDataMissing:
        return None
    if date_column not in frame.columns or frame.empty:
        return None
    value = pd.to_datetime(frame[date_column]).max()
    return None if pd.isna(value) else value.date()


def evaluation_anchor(metric, today: date | None = None) -> tuple[date, bool]:
    """Returns (anchor_date, is_stale). ``is_stale`` is True when the warehouse
    ends before today, meaning results describe history, not the present."""
    today = today or date.today()
    latest = latest_available_date(metric.base_table, metric.date_column)
    if latest is None or latest >= today:
        return today, False
    return latest, True
