import numpy as np
import pandas as pd

from app.analytics import confidence as conf
from app.analytics.anomaly import AnomalyDetector
from app.analytics.forecast import Forecaster


def _seasonal_series(n=180, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    trend = np.linspace(1000, 1400, n)
    weekly = 150 * np.sin(2 * np.pi * np.arange(n) / 7)
    return pd.Series(trend + weekly + rng.normal(0, 30, n), index=idx)


def test_forecast_never_returns_negative_bounds_for_positive_series():
    f = Forecaster().fit_predict(_seasonal_series(), horizon=30)
    assert min(f.lower_95) >= 0
    assert min(f.predicted) >= 0


def test_forecast_detects_weekly_seasonality():
    assert Forecaster().fit_predict(_seasonal_series(), horizon=14).seasonality_detected


def test_forecast_reports_measured_not_claimed_accuracy():
    m = Forecaster().fit_predict(_seasonal_series(), horizon=30).metrics
    assert m.n_backtest_folds > 0 and m.mae > 0


def test_short_history_degrades_to_naive_with_caveat():
    s = _seasonal_series(n=10)
    f = Forecaster().fit_predict(s, horizon=7)
    assert f.model == "naive_last_value" and f.caveats


def test_intervals_widen_with_horizon():
    f = Forecaster().fit_predict(_seasonal_series(), horizon=30)
    first = f.upper_95[0] - f.lower_95[0]
    last = f.upper_95[-1] - f.lower_95[-1]
    assert last >= first


def test_anomaly_detector_finds_injected_spike():
    s = _seasonal_series()
    s.iloc[100] = s.iloc[100] * 4
    found = AnomalyDetector().detect(s, metric="revenue")
    assert any(a.timestamp == str(s.index[100].date()) for a in found)


def test_anomaly_severity_scales_with_deviation():
    s = _seasonal_series()
    s.iloc[50] *= 10
    top = AnomalyDetector().detect(s)[0]
    assert top.severity in ("high", "critical") and top.direction == "spike"


def test_confidence_is_dragged_down_by_weakest_component():
    strong = (0.95, "ok")
    weak = (0.05, "bad")
    result = conf.combine(strong, weak, strong, strong)
    assert result.overall < 0.6
    assert result.limiting_factor == "statistical"


def test_confidence_labels_insufficient_when_evidence_is_thin():
    r = conf.combine((0.2, ""), (0.2, ""), (0.2, ""), (0.2, ""))
    assert r.label == "insufficient"


def test_insignificant_result_yields_low_statistical_confidence():
    score, _ = conf.score_statistical(p_value=0.62, effect_size=0.03)
    assert score < 0.3
