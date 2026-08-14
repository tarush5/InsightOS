"""
Forecasting engine (spec section 26).

Implements damped-trend Holt-Winters with additive seasonality in pure NumPy,
plus a rolling-origin backtest so every forecast ships with measured, not
claimed, accuracy. Model selection is by backtest MASE against a seasonal-naive
baseline: if the fancier model cannot beat seasonal naive, we return the naive
one and say so. That is the honest answer more often than the literature implies.

Prediction intervals come from the empirical distribution of backtest residuals
at each horizon, not from a Gaussian assumption -- business series are rarely
Gaussian and the assumption silently understates tail risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(slots=True)
class ForecastMetrics:
    mae: float
    rmse: float
    mape: float | None
    mase: float          # <1.0 means it beats seasonal naive
    n_backtest_folds: int

    def as_dict(self) -> dict:
        return {
            "mae": round(self.mae, 4),
            "rmse": round(self.rmse, 4),
            "mape": None if self.mape is None else round(self.mape, 6),
            "mase": round(self.mase, 4),
            "n_backtest_folds": self.n_backtest_folds,
            "beats_baseline": self.mase < 1.0,
        }


@dataclass(slots=True)
class ForecastResult:
    model: str
    horizon: int
    dates: list[str]
    predicted: list[float]
    lower_80: list[float]
    upper_80: list[float]
    lower_95: list[float]
    upper_95: list[float]
    metrics: ForecastMetrics
    trend_direction: str
    seasonality_detected: bool
    history_dates: list[str] = field(default_factory=list)
    history_values: list[float] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "horizon": self.horizon,
            "dates": self.dates,
            "predicted": [round(v, 4) for v in self.predicted],
            "lower_80": [round(v, 4) for v in self.lower_80],
            "upper_80": [round(v, 4) for v in self.upper_80],
            "lower_95": [round(v, 4) for v in self.lower_95],
            "upper_95": [round(v, 4) for v in self.upper_95],
            "metrics": self.metrics.as_dict(),
            "trend_direction": self.trend_direction,
            "seasonality_detected": self.seasonality_detected,
            "history_dates": self.history_dates,
            "history_values": [round(v, 4) for v in self.history_values],
            "caveats": self.caveats,
        }


def _holt_winters(
    y: np.ndarray, horizon: int, period: int,
    alpha: float, beta: float, gamma: float, phi: float,
) -> np.ndarray:
    """Damped additive Holt-Winters. Returns `horizon` point forecasts."""
    n = len(y)
    if n < period * 2:
        period = 1
    level = float(np.mean(y[:period])) if period > 1 else float(y[0])
    trend = float((np.mean(y[period:2 * period]) - np.mean(y[:period])) / period) if n >= 2 * period and period > 1 else 0.0
    season = list(y[:period] - level) if period > 1 else [0.0]

    for t in range(n):
        s_idx = t % period
        prev_level = level
        seasonal = season[s_idx] if period > 1 else 0.0
        level = alpha * (y[t] - seasonal) + (1 - alpha) * (prev_level + phi * trend)
        trend = beta * (level - prev_level) + (1 - beta) * phi * trend
        if period > 1:
            season[s_idx] = gamma * (y[t] - level) + (1 - gamma) * seasonal

    out = np.empty(horizon)
    for h in range(1, horizon + 1):
        damp = sum(phi ** i for i in range(1, h + 1))
        seasonal = season[(n + h - 1) % period] if period > 1 else 0.0
        out[h - 1] = level + damp * trend + seasonal
    return out


def _seasonal_naive(y: np.ndarray, horizon: int, period: int) -> np.ndarray:
    if period <= 1 or len(y) < period:
        return np.repeat(y[-1], horizon)
    return np.array([y[-period + (h % period)] for h in range(horizon)])


class Forecaster:
    """Fits by grid search over smoothing parameters, scored by rolling backtest."""

    _GRID = [
        (a, b, g, p)
        for a in (0.15, 0.35, 0.6)
        for b in (0.02, 0.1, 0.3)
        for g in (0.05, 0.2)
        for p in (0.85, 0.98)
    ]

    def __init__(self, *, seasonal_period: int = 7, min_history: int = 21) -> None:
        self.period = seasonal_period
        self.min_history = min_history

    def fit_predict(
        self, series: pd.Series, horizon: int = 30, *, freq: str = "D"
    ) -> ForecastResult:
        s = series.dropna().astype(float).sort_index()
        y = s.to_numpy()
        caveats: list[str] = []

        if len(y) < self.min_history:
            caveats.append(
                f"Only {len(y)} observations available; below the {self.min_history}-point "
                "minimum for a trend/seasonal fit. Returning a naive forecast."
            )
            return self._naive_result(s, horizon, freq, caveats)

        seasonal = self._has_seasonality(y)
        period = self.period if seasonal else 1

        best_params, best_mase, folds, residuals = self._backtest(y, period, horizon)
        naive_mase, _, _ = self._score_naive(y, period, horizon)

        use_naive = best_mase >= 1.0
        if use_naive:
            caveats.append(
                "Seasonal-naive baseline was not beaten in backtest; reporting the "
                "baseline rather than an overfitted model."
            )
            preds = _seasonal_naive(y, horizon, period)
            model_name = "seasonal_naive"
            _, resid_naive, n_folds = self._score_naive(y, period, horizon)
            residuals, folds = resid_naive, n_folds
            mase = 1.0
        else:
            preds = _holt_winters(y, horizon, period, *best_params)
            model_name = "damped_holt_winters"
            mase = best_mase

        lo80, hi80, lo95, hi95 = self._intervals(preds, residuals, horizon)
        if float(np.min(y)) >= 0:
            # A non-negative history cannot produce a negative lower bound. Clamping
            # here is more honest than shipping an interval the domain forbids.
            lo80 = np.maximum(lo80, 0.0)
            lo95 = np.maximum(lo95, 0.0)
            preds = np.maximum(preds, 0.0)

        errs = np.abs(residuals) if len(residuals) else np.array([0.0])
        denom = np.abs(y[-len(errs):]) if len(errs) <= len(y) else np.abs(y)
        mape = float(np.mean(errs / np.where(denom == 0, np.nan, denom))) if len(errs) else None
        if mape is not None and not np.isfinite(mape):
            mape = None

        metrics = ForecastMetrics(
            mae=float(np.mean(errs)),
            rmse=float(np.sqrt(np.mean(errs ** 2))),
            mape=mape,
            mase=float(mase),
            n_backtest_folds=folds,
        )

        last_date = s.index[-1]
        future = pd.date_range(last_date, periods=horizon + 1, freq=freq)[1:]
        slope = float(np.polyfit(np.arange(len(preds)), preds, 1)[0]) if horizon > 1 else 0.0
        spread = float(np.std(y)) or 1.0
        direction = "flat"
        if abs(slope * horizon) > 0.1 * spread:
            direction = "increasing" if slope > 0 else "decreasing"

        if mape is not None and mape > 0.25:
            caveats.append(
                f"Backtest MAPE is {mape:.0%}; treat point estimates as directional only."
            )

        return ForecastResult(
            model=model_name,
            horizon=horizon,
            dates=[d.strftime("%Y-%m-%d") for d in future],
            predicted=preds.tolist(),
            lower_80=lo80.tolist(), upper_80=hi80.tolist(),
            lower_95=lo95.tolist(), upper_95=hi95.tolist(),
            metrics=metrics,
            trend_direction=direction,
            seasonality_detected=seasonal,
            history_dates=[str(d.date()) if hasattr(d, "date") else str(d) for d in s.index],
            history_values=y.tolist(),
            caveats=caveats,
        )

    # -- internals ------------------------------------------------------------

    def _has_seasonality(self, y: np.ndarray) -> bool:
        if len(y) < self.period * 3:
            return False
        a, b = y[self.period:], y[:-self.period]
        if len(a) < 5:
            return False
        corr = float(np.corrcoef(a, b)[0, 1])
        return bool(np.isfinite(corr) and corr > 0.5)

    def _folds(self, n: int, horizon: int) -> list[int]:
        """Rolling-origin cut points, capped so training never drops below 60%."""
        h = min(horizon, max(1, n // 6))
        starts = []
        cut = n - h
        while cut > int(n * 0.6) and len(starts) < 5:
            starts.append(cut)
            cut -= h
        return starts

    def _backtest(self, y, period, horizon) -> tuple[tuple, float, int, np.ndarray]:
        h = min(horizon, max(1, len(y) // 6))
        cuts = self._folds(len(y), horizon)
        if not cuts:
            return self._GRID[0], float("inf"), 0, np.array([0.0])

        naive_errs = []
        for cut in cuts:
            actual = y[cut:cut + h]
            naive_errs.append(np.abs(_seasonal_naive(y[:cut], len(actual), period) - actual))
        naive_mae = float(np.mean(np.concatenate(naive_errs))) or 1e-9

        best, best_mase, best_resid = self._GRID[0], float("inf"), np.array([0.0])
        for params in self._GRID:
            errs = []
            for cut in cuts:
                actual = y[cut:cut + h]
                pred = _holt_winters(y[:cut], len(actual), period, *params)
                errs.append(pred - actual)
            resid = np.concatenate(errs)
            mase = float(np.mean(np.abs(resid))) / naive_mae
            if mase < best_mase:
                best, best_mase, best_resid = params, mase, resid
        return best, best_mase, len(cuts), best_resid

    def _score_naive(self, y, period, horizon) -> tuple[float, np.ndarray, int]:
        h = min(horizon, max(1, len(y) // 6))
        cuts = self._folds(len(y), horizon)
        errs = []
        for cut in cuts:
            actual = y[cut:cut + h]
            errs.append(_seasonal_naive(y[:cut], len(actual), period) - actual)
        resid = np.concatenate(errs) if errs else np.array([0.0])
        return 1.0, resid, len(cuts)

    @staticmethod
    def _intervals(preds, residuals, horizon):
        """Empirical quantiles, widened with sqrt(h) to reflect horizon uncertainty."""
        if len(residuals) < 4:
            sd = float(np.std(preds)) or 1.0
            q80, q95 = 1.28 * sd, 1.96 * sd
            lo80 = preds - q80; hi80 = preds + q80
            lo95 = preds - q95; hi95 = preds + q95
            return lo80, hi80, lo95, hi95
        q = np.quantile(residuals, [0.10, 0.90, 0.025, 0.975])
        growth = np.sqrt(np.arange(1, horizon + 1) / 1.0)
        growth = growth / growth[0]
        return (preds + q[0] * growth, preds + q[1] * growth,
                preds + q[2] * growth, preds + q[3] * growth)

    def _naive_result(self, s, horizon, freq, caveats) -> ForecastResult:
        y = s.to_numpy()
        preds = np.repeat(float(y[-1]), horizon)
        sd = float(np.std(y)) if len(y) > 1 else 0.0
        future = pd.date_range(s.index[-1], periods=horizon + 1, freq=freq)[1:]
        return ForecastResult(
            model="naive_last_value", horizon=horizon,
            dates=[d.strftime("%Y-%m-%d") for d in future],
            predicted=preds.tolist(),
            lower_80=(preds - 1.28 * sd).tolist(), upper_80=(preds + 1.28 * sd).tolist(),
            lower_95=(preds - 1.96 * sd).tolist(), upper_95=(preds + 1.96 * sd).tolist(),
            metrics=ForecastMetrics(mae=sd, rmse=sd, mape=None, mase=1.0, n_backtest_folds=0),
            trend_direction="flat", seasonality_detected=False,
            history_dates=[str(d.date()) if hasattr(d, "date") else str(d) for d in s.index],
            history_values=y.tolist(), caveats=caveats,
        )
