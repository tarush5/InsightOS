"""
Golden cases for the evaluation harness (spec section 60).

Each case generates its own data from a seeded process, so the ground truth is
known by construction rather than by annotation. This matters: a benchmark
whose labels were written by hand drifts away from the data it describes, and a
benchmark whose labels came from the system under test measures nothing at all.

The set deliberately includes cases the system is supposed to *fail* to find an
answer for -- flat data with no driver, and a change that is pure noise. A
system scored only on cases with a real answer will learn to always produce
one, which is the failure mode that matters most here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(slots=True)
class GoldenCase:
    """A question with a mechanically known answer."""
    case_id: str
    question: str
    metric_key: str
    dimensions: list[str]
    current_period: tuple[date, date]
    comparison_period: tuple[date, date]
    build: Callable[[], pd.DataFrame]
    # --- ground truth ---
    expected_drivers: list[str] = field(default_factory=list)
    expect_finding: bool = True          # False => the honest answer is "nothing here"
    expect_significant: bool = True
    max_confidence: float | None = None  # cap the system must not exceed
    notes: str = ""


def _panel(rng: np.random.Generator, *, regions: dict[str, float],
           start: str, end: str, effects: dict[str, tuple[str, float]] | None = None,
           noise: float = 0.03) -> pd.DataFrame:
    """Daily revenue per region. ``effects`` maps region -> (from_date, multiplier)."""
    days = pd.date_range(start, end, freq="D")
    rows = []
    for region, level in regions.items():
        cut, mult = effects.get(region, (None, 1.0)) if effects else (None, 1.0)
        for day in days:
            # A weekly seasonal shape, so detectors face a realistic series.
            seasonal = 1.0 + 0.12 * np.sin(day.dayofweek / 7 * 2 * np.pi)
            value = level * seasonal * (1.0 + rng.normal(0, noise))
            if cut is not None and day >= pd.Timestamp(cut):
                value *= mult
            rows.append({"order_date": day, "region": region,
                         "segment": "All", "channel": "All", "revenue": value})
    return pd.DataFrame(rows)


def _case_single_driver() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    return _panel(rng, regions={"North": 30_000, "South": 20_000,
                                "East": 25_000, "West": 15_000},
                  start="2025-06-01", end="2025-08-31",
                  effects={"South": ("2025-08-01", 0.55)})


def _case_two_drivers() -> pd.DataFrame:
    rng = np.random.default_rng(23)
    return _panel(rng, regions={"North": 30_000, "South": 20_000,
                                "East": 25_000, "West": 15_000},
                  start="2025-06-01", end="2025-08-31",
                  effects={"South": ("2025-08-01", 0.70),
                           "West": ("2025-08-01", 0.60)})


def _case_offsetting() -> pd.DataFrame:
    """One region collapses while another grows by almost as much. The aggregate
    barely moves; a system that only looks at the total finds nothing."""
    rng = np.random.default_rng(37)
    return _panel(rng, regions={"North": 30_000, "South": 20_000,
                                "East": 25_000, "West": 15_000},
                  start="2025-06-01", end="2025-08-31",
                  effects={"South": ("2025-08-01", 0.60),
                           "North": ("2025-08-01", 1.28)})


def _case_flat() -> pd.DataFrame:
    rng = np.random.default_rng(41)
    return _panel(rng, regions={"North": 30_000, "South": 20_000,
                                "East": 25_000, "West": 15_000},
                  start="2025-06-01", end="2025-08-31")


def _case_noise_only() -> pd.DataFrame:
    """No structural change, but heavy noise. The system must not mistake
    sampling variation for a driver."""
    rng = np.random.default_rng(53)
    return _panel(rng, regions={"North": 30_000, "South": 20_000,
                                "East": 25_000, "West": 15_000},
                  start="2025-06-01", end="2025-08-31", noise=0.30)


def _case_small_segment() -> pd.DataFrame:
    """The largest *percentage* fall is in a tiny segment that barely moves the
    total. Ranking by percentage rather than contribution gets this wrong."""
    rng = np.random.default_rng(67)
    return _panel(rng, regions={"North": 60_000, "South": 30_000,
                                "East": 25_000, "Tiny": 400},
                  start="2025-06-01", end="2025-08-31",
                  effects={"Tiny": ("2025-08-01", 0.10),
                           "South": ("2025-08-01", 0.80)})


CURRENT = (date(2025, 8, 1), date(2025, 8, 31))
COMPARISON = (date(2025, 7, 1), date(2025, 7, 31))
DIMS = ["region", "segment", "channel"]


def golden_cases() -> list[GoldenCase]:
    return [
        GoldenCase("single-driver", "Why did revenue fall in August?", "revenue",
                   DIMS, CURRENT, COMPARISON, _case_single_driver,
                   expected_drivers=["South"],
                   notes="One region drops 45%. The top driver must be South."),
        GoldenCase("two-drivers", "Why did revenue fall in August?", "revenue",
                   DIMS, CURRENT, COMPARISON, _case_two_drivers,
                   expected_drivers=["South", "West"],
                   notes="Two regions fall; both must appear in the top three."),
        GoldenCase("offsetting", "What changed in August?", "revenue",
                   DIMS, CURRENT, COMPARISON, _case_offsetting,
                   expected_drivers=["South", "North"], expect_significant=False,
                   notes="Aggregate is nearly flat while two segments move "
                         "sharply in opposite directions."),
        GoldenCase("small-segment", "Why did revenue fall in August?", "revenue",
                   DIMS, CURRENT, COMPARISON, _case_small_segment,
                   expected_drivers=["South"],
                   notes="A tiny segment falls 90% but contributes almost "
                         "nothing. Ranking by contribution must put South first."),
        GoldenCase("flat", "Why did revenue fall in August?", "revenue",
                   DIMS, CURRENT, COMPARISON, _case_flat,
                   expected_drivers=[], expect_finding=False,
                   expect_significant=False, max_confidence=0.55,
                   notes="Nothing happened. The correct answer is to say so, "
                         "with low confidence."),
        GoldenCase("noise-only", "Why is revenue volatile?", "revenue",
                   DIMS, CURRENT, COMPARISON, _case_noise_only,
                   expected_drivers=[], expect_finding=False,
                   expect_significant=False, max_confidence=0.65,
                   notes="High variance, no structure. Confidence must stay low."),
    ]
