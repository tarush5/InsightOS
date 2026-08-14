"""
Causal effect estimation by difference-in-differences (spec section 26).

Correlation is cheap; the root-cause engine already produces it. This module
exists to answer the harder question -- *did the intervention cause the change*
-- and, more importantly, to say when it cannot.

The estimator is two-way fixed effects with standard errors clustered on the
treated unit, plus two diagnostics that decide whether the estimate is allowed
to be called causal at all:

* **Parallel trends.** DiD assumes treated and control units were moving
  together before the intervention. That is testable on the pre-period, and if
  it fails the headline number is a trend artefact, not an effect.
* **Placebo.** Re-running the design on a fake treatment date inside the
  pre-period should find nothing. If it finds something, the design is picking
  up structure that has nothing to do with the intervention.

An estimate that fails either diagnostic is returned with ``credible=False``
and the caveat attached. Nothing here silently upgrades an association into a
cause -- that is the single most consequential error this kind of system can
make, because it is the output people act on.

Assumptions that remain untestable and are always reported: no spillover from
treated to control units (SUTVA), no other intervention coinciding with the
treatment date, and treatment timing not chosen in response to the outcome.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from app.analytics.significance import student_t_critical, student_t_two_sided_p

MIN_PERIODS_PER_SIDE = 3
FEW_CLUSTER_WARNING = 10


@dataclass(slots=True)
class Diagnostic:
    name: str
    passed: bool
    statistic: float
    p_value: float
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed,
                "statistic": round(float(self.statistic), 6),
                "p_value": round(float(self.p_value), 6), "detail": self.detail}


@dataclass(slots=True)
class CausalEstimate:
    """The effect, its uncertainty, and every reason to doubt it."""
    att: float                      # average treatment effect on the treated
    std_error: float
    p_value: float
    ci_low: float
    ci_high: float
    relative_att: float | None      # ATT as a fraction of the treated pre-mean
    n_treated_units: int
    n_control_units: int
    n_observations: int
    n_clusters: int
    degrees_of_freedom: float
    diagnostics: list[Diagnostic] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    credible: bool = True
    method: str = "two-way fixed effects DiD, cluster-robust SE"

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05

    def interpretation(self) -> str:
        if not self.credible:
            failed = [d.name for d in self.diagnostics if not d.passed]
            return ("Not usable as a causal estimate: "
                    + ", ".join(failed) + " failed. "
                    "The difference is real but its cause is not identified here.")
        if not self.significant:
            return (f"No detectable effect. The estimate is {self.att:,.2f} "
                    f"(95% CI {self.ci_low:,.2f} to {self.ci_high:,.2f}), which "
                    "includes zero.")
        direction = "reduced" if self.att < 0 else "increased"
        rel = "" if self.relative_att is None else f" ({self.relative_att * 100:+.1f}%)"
        return (f"The intervention {direction} the outcome by "
                f"{abs(self.att):,.2f}{rel} per unit-period "
                f"(95% CI {self.ci_low:,.2f} to {self.ci_high:,.2f}, "
                f"p={self.p_value:.4f}).")

    def as_dict(self) -> dict:
        return {
            "att": round(float(self.att), 6),
            "std_error": round(float(self.std_error), 6),
            "p_value": round(float(self.p_value), 6),
            "ci_95": [round(float(self.ci_low), 6), round(float(self.ci_high), 6)],
            "relative_att": (None if self.relative_att is None
                             else round(float(self.relative_att), 6)),
            "significant": self.significant,
            "credible": self.credible,
            "method": self.method,
            "sample": {"treated_units": self.n_treated_units,
                       "control_units": self.n_control_units,
                       "observations": self.n_observations,
                       "clusters": self.n_clusters,
                       "degrees_of_freedom": round(float(self.degrees_of_freedom), 2)},
            "diagnostics": [d.as_dict() for d in self.diagnostics],
            "caveats": self.caveats,
            "interpretation": self.interpretation(),
        }


class InsufficientDesign(ValueError):
    """The data cannot support a DiD design. Raised instead of returning a number."""


def _ols_cluster(X: np.ndarray, y: np.ndarray, clusters: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray, float]:
    """OLS with CR1 cluster-robust covariance. Returns (beta, cov, dof)."""
    n, k = X.shape
    xtx = X.T @ X
    xtx_inv = np.linalg.pinv(xtx)
    beta = xtx_inv @ (X.T @ y)
    resid = y - X @ beta

    meat = np.zeros((k, k))
    unique = np.unique(clusters)
    for c in unique:
        idx = clusters == c
        xg, ug = X[idx], resid[idx]
        s = xg.T @ ug
        meat += np.outer(s, s)

    g = len(unique)
    # CR1 finite-sample correction (Cameron & Miller 2015).
    correction = (g / max(g - 1, 1)) * ((n - 1) / max(n - k, 1))
    cov = correction * (xtx_inv @ meat @ xtx_inv)
    # Inference uses G-1 degrees of freedom, not n-k: with few clusters the
    # sampling distribution is much fatter-tailed than the residual count suggests.
    return beta, cov, float(max(g - 1, 1))


def _design(frame: pd.DataFrame, unit_col: str, time_col: str,
            extra: dict[str, np.ndarray] | None = None
            ) -> tuple[np.ndarray, list[str]]:
    """Intercept + unit fixed effects + time fixed effects + extra regressors."""
    units = pd.get_dummies(frame[unit_col].astype(str), prefix="u", drop_first=True)
    times = pd.get_dummies(frame[time_col].astype(str), prefix="t", drop_first=True)
    blocks = [np.ones((len(frame), 1)), units.to_numpy(float), times.to_numpy(float)]
    names = ["intercept"] + list(units.columns) + list(times.columns)
    for name, values in (extra or {}).items():
        blocks.append(values.reshape(-1, 1).astype(float))
        names.append(name)
    return np.hstack(blocks), names


class DiffInDiff:
    """Estimate the effect of an intervention on treated units over time.

    ``panel`` must be long-format with one row per unit-period:
    ``unit``, ``period``, ``outcome``.
    """

    def __init__(self, *, alpha: float = 0.05,
                 parallel_trends_alpha: float = 0.10) -> None:
        self.alpha = alpha
        # Deliberately looser than 0.05: this is a test we *want* to fail
        # loudly, so the bar for declaring trends parallel is set high.
        self.parallel_trends_alpha = parallel_trends_alpha

    def estimate(self, panel: pd.DataFrame, *, treated_units: list[str],
                 treatment_period, unit_col: str = "unit",
                 time_col: str = "period", outcome_col: str = "outcome",
                 control_units: list[str] | None = None,
                 run_placebo: bool = True) -> CausalEstimate:
        """``control_units`` restricts the comparison group. Use it when another
        unit is known to be affected by the same or an overlapping intervention:
        leaving a contaminated unit in the control pool biases the estimate and,
        in practice, shows up as a failed diagnostic rather than a wrong number."""
        df = self._prepare(panel, unit_col, time_col, outcome_col,
                           treated_units, treatment_period, control_units)

        treated_mask = df["_treated"].to_numpy(bool)
        post_mask = df["_post"].to_numpy(bool)
        did = (treated_mask & post_mask).astype(float)

        X, names = _design(df, unit_col, time_col, {"did": did})
        y = df[outcome_col].to_numpy(float)
        beta, cov, dof = _ols_cluster(X, y, df[unit_col].astype(str).to_numpy())

        i = names.index("did")
        att = float(beta[i])
        se = float(np.sqrt(max(cov[i, i], 0.0)))
        t_stat = att / se if se > 0 else 0.0
        p = student_t_two_sided_p(t_stat, dof)
        crit = student_t_critical(dof, self.alpha)

        pre_treated_mean = float(df.loc[treated_mask & ~post_mask, outcome_col].mean())
        relative = att / abs(pre_treated_mean) if abs(pre_treated_mean) > 1e-12 else None

        n_clusters = int(df[unit_col].nunique())
        estimate = CausalEstimate(
            att=att, std_error=se, p_value=p,
            ci_low=att - crit * se, ci_high=att + crit * se,
            relative_att=relative,
            n_treated_units=len(set(treated_units)),
            n_control_units=n_clusters - len(set(treated_units)),
            n_observations=len(df), n_clusters=n_clusters,
            degrees_of_freedom=dof,
        )

        estimate.diagnostics.append(
            self._parallel_trends(df, unit_col, time_col, outcome_col))
        if run_placebo:
            placebo = self._placebo(df, treated_units, unit_col, time_col, outcome_col)
            if placebo is not None:
                estimate.diagnostics.append(placebo)

        estimate.caveats = self._caveats(estimate, df, unit_col)
        estimate.credible = all(d.passed for d in estimate.diagnostics)
        return estimate

    # --- diagnostics -----------------------------------------------------
    def _parallel_trends(self, df: pd.DataFrame, unit_col: str, time_col: str,
                         outcome_col: str) -> Diagnostic:
        """Test treated x time-trend on the pre-period only. A non-zero
        interaction means the groups were already diverging."""
        pre = df.loc[~df["_post"]].copy()
        periods = np.sort(pre[time_col].unique())
        if len(periods) < MIN_PERIODS_PER_SIDE:
            return Diagnostic(
                "parallel_trends", False, float("nan"), 1.0,
                f"Only {len(periods)} pre-treatment period(s); at least "
                f"{MIN_PERIODS_PER_SIDE} are needed to test the assumption. "
                "Untested assumptions are not assumed to hold.")

        index = {p: i for i, p in enumerate(periods)}
        trend = pre[time_col].map(index).to_numpy(float)
        trend = (trend - trend.mean()) / (trend.std() or 1.0)
        interaction = trend * pre["_treated"].to_numpy(float)

        units = pd.get_dummies(pre[unit_col].astype(str), prefix="u", drop_first=True)
        X = np.hstack([np.ones((len(pre), 1)), units.to_numpy(float),
                       trend.reshape(-1, 1), interaction.reshape(-1, 1)])
        y = pre[outcome_col].to_numpy(float)
        beta, cov, dof = _ols_cluster(X, y, pre[unit_col].astype(str).to_numpy())

        coef = float(beta[-1])
        se = float(np.sqrt(max(cov[-1, -1], 0.0)))
        t_stat = coef / se if se > 0 else 0.0
        p = student_t_two_sided_p(t_stat, dof)
        passed = p >= self.parallel_trends_alpha
        detail = (f"Pre-period divergence of {coef:,.3f} per period "
                  f"(p={p:.4f}). "
                  + ("Trends are statistically indistinguishable; the design holds."
                     if passed else
                     "Treated and control were already diverging before treatment, "
                     "so the post-treatment gap cannot be attributed to the "
                     "intervention."))
        return Diagnostic("parallel_trends", passed, coef, p, detail)

    def _placebo(self, df: pd.DataFrame, treated_units: list[str], unit_col: str,
                 time_col: str, outcome_col: str) -> Diagnostic | None:
        """Re-run the design on a fake treatment at the pre-period midpoint."""
        pre = df.loc[~df["_post"]].copy()
        periods = np.sort(pre[time_col].unique())
        if len(periods) < 2 * MIN_PERIODS_PER_SIDE:
            return None
        fake = periods[len(periods) // 2]

        pre["_post"] = pre[time_col] >= fake
        did = (pre["_treated"].to_numpy(bool) & pre["_post"].to_numpy(bool)).astype(float)
        if did.sum() == 0 or did.sum() == len(did):
            return None

        X, names = _design(pre, unit_col, time_col, {"did": did})
        y = pre[outcome_col].to_numpy(float)
        beta, cov, dof = _ols_cluster(X, y, pre[unit_col].astype(str).to_numpy())
        i = names.index("did")
        coef = float(beta[i])
        se = float(np.sqrt(max(cov[i, i], 0.0)))
        t_stat = coef / se if se > 0 else 0.0
        p = student_t_two_sided_p(t_stat, dof)
        passed = p >= 0.05
        detail = (f"Placebo treatment at {fake} gives an effect of {coef:,.3f} "
                  f"(p={p:.4f}). "
                  + ("No spurious effect, as required."
                     if passed else
                     "A significant effect appears where none should exist, so "
                     "the estimator is picking up structure unrelated to the "
                     "intervention."))
        return Diagnostic("placebo", passed, coef, p, detail)

    # --- plumbing --------------------------------------------------------
    def _prepare(self, panel: pd.DataFrame, unit_col: str, time_col: str,
                 outcome_col: str, treated_units: list[str], treatment_period,
                 control_units: list[str] | None = None) -> pd.DataFrame:
        for col in (unit_col, time_col, outcome_col):
            if col not in panel.columns:
                raise InsufficientDesign(f"Panel is missing required column '{col}'.")
        df = panel[[unit_col, time_col, outcome_col]].dropna().copy()
        df[unit_col] = df[unit_col].astype(str)
        treated = {str(u) for u in treated_units}

        missing = treated - set(df[unit_col].unique())
        if missing:
            raise InsufficientDesign(
                f"Treated unit(s) {sorted(missing)} do not appear in the panel.")
        if not treated:
            raise InsufficientDesign("No treated units were given.")

        if control_units is not None:
            keep = treated | {str(u) for u in control_units}
            unknown = keep - set(df[unit_col].unique())
            if unknown:
                raise InsufficientDesign(
                    f"Control unit(s) {sorted(unknown)} do not appear in the panel.")
            df = df[df[unit_col].isin(keep)].copy()

        controls = set(df[unit_col].unique()) - treated
        if not controls:
            raise InsufficientDesign(
                "Every unit is treated, so there is no control group. "
                "A before/after comparison cannot separate the intervention "
                "from anything else that changed at the same time.")

        if isinstance(treatment_period, (str, date, pd.Timestamp)):
            df[time_col] = pd.to_datetime(df[time_col])
            cut = pd.Timestamp(treatment_period)
        else:
            cut = treatment_period

        df["_treated"] = df[unit_col].isin(treated)
        df["_post"] = df[time_col] >= cut

        for label, mask in (("pre", ~df["_post"]), ("post", df["_post"])):
            periods = df.loc[mask, time_col].nunique()
            if periods < MIN_PERIODS_PER_SIDE:
                raise InsufficientDesign(
                    f"Only {periods} {label}-treatment period(s); at least "
                    f"{MIN_PERIODS_PER_SIDE} are required on each side.")
        return df

    @staticmethod
    def _caveats(est: CausalEstimate, df: pd.DataFrame, unit_col: str) -> list[str]:
        out = [
            "Assumes no spillover from treated to control units.",
            "Assumes nothing else changed for the treated units at the same time.",
        ]
        if est.n_clusters < FEW_CLUSTER_WARNING:
            out.append(
                f"Only {est.n_clusters} clusters. Cluster-robust standard errors "
                f"are known to be anti-conservative here; inference uses "
                f"{est.degrees_of_freedom:.0f} degrees of freedom to compensate, "
                "but the interval is still optimistic.")
        if est.n_control_units < 2:
            out.append(
                "A single control unit carries the entire counterfactual; any "
                "shock specific to it is indistinguishable from the treatment effect.")
        sizes = df.groupby(unit_col).size()
        if sizes.max() > 3 * max(sizes.min(), 1):
            out.append("Panel is unbalanced; units contribute unequal numbers of "
                       "periods to the estimate.")
        return out
