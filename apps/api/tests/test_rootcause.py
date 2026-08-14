"""Attribution must be arithmetically exact -- this is the load-bearing property."""
import numpy as np

from app.analytics.rootcause import RootCauseEngine


def test_contributions_reconcile_exactly(two_periods):
    prev, curr = two_periods
    r = RootCauseEngine().analyse(prev, curr, metric_column="revenue",
                                  dimensions=["region"], metric_name="revenue")
    assert r.reconciliation_error < 1e-9


def test_engineered_driver_is_ranked_first(two_periods):
    prev, curr = two_periods
    r = RootCauseEngine().analyse(prev, curr, metric_column="revenue",
                                  dimensions=["region"], metric_name="revenue")
    top = r.top(1)[0]
    assert top.segment == "South"
    assert np.isclose(top.segment_pct_change, -0.5, atol=1e-9)


def test_headline_change_matches_totals(two_periods):
    prev, curr = two_periods
    r = RootCauseEngine().analyse(prev, curr, metric_column="revenue",
                                  dimensions=["region"])
    expected = (curr.revenue.sum() - prev.revenue.sum()) / prev.revenue.sum()
    assert np.isclose(r.pct_change, expected)


def test_new_and_lost_segments_are_labelled(two_periods):
    prev, curr = two_periods
    curr = curr[curr.region != "East"]
    r = RootCauseEngine().analyse(prev, curr, metric_column="revenue",
                                  dimensions=["region"])
    lost = [d for d in r.drivers if d.segment == "East"]
    assert lost and lost[0].status == "lost"


def test_small_segments_filtered_but_reconciliation_still_exact():
    import pandas as pd
    prev = pd.DataFrame({"region": ["A", "B"] + [f"tiny{i}" for i in range(50)],
                         "revenue": [10000.0, 10000.0] + [1.0] * 50})
    curr = pd.DataFrame({"region": ["A", "B"] + [f"tiny{i}" for i in range(50)],
                         "revenue": [9000.0, 10000.0] + [0.5] * 50})
    r = RootCauseEngine().analyse(prev, curr, metric_column="revenue",
                                  dimensions=["region"])
    assert len(r.drivers) < 52          # tiny segments filtered out of the ranking
    assert r.reconciliation_error < 1e-9  # but reconciliation is unaffected
