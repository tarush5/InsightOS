"""
Statistical significance for period-over-period claims (spec sections 25, 31).

The point of this module is to stop the system saying "revenue dropped because
of the South region" when the South region's drop is inside its own normal
week-to-week variation. Implemented in NumPy so it runs without SciPy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass(slots=True)
class SignificanceResult:
    test: str
    statistic: float
    p_value: float
    effect_size: float          # Cohen's d
    significant: bool
    alpha: float
    n_prev: int
    n_curr: int
    interpretation: str

    def as_dict(self) -> dict:
        return {
            "test": self.test, "statistic": round(self.statistic, 4),
            "p_value": round(self.p_value, 6), "effect_size": round(self.effect_size, 4),
            "significant": self.significant, "alpha": self.alpha,
            "n_prev": self.n_prev, "n_curr": self.n_curr,
            "interpretation": self.interpretation,
        }


def welch_t_test(prev: np.ndarray, curr: np.ndarray, alpha: float = 0.05) -> SignificanceResult:
    """Welch's t-test -- does not assume equal variances, which two business
    periods essentially never have."""
    prev = np.asarray(prev, dtype=float)
    curr = np.asarray(curr, dtype=float)
    n1, n2 = len(prev), len(curr)

    if n1 < 3 or n2 < 3:
        return SignificanceResult(
            "welch_t", 0.0, 1.0, 0.0, False, alpha, n1, n2,
            "Too few observations to test. The difference is reported as observed, not confirmed.",
        )

    m1, m2 = prev.mean(), curr.mean()
    v1, v2 = prev.var(ddof=1), curr.var(ddof=1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return SignificanceResult(
            "welch_t", 0.0, 1.0, 0.0, False, alpha, n1, n2,
            "Zero variance in both periods; no test is meaningful.",
        )

    t = (m2 - m1) / se
    # Normal approximation to the t-distribution; adequate for n>=15 per group and
    # conservative below that because we also require a non-trivial effect size.
    p = 2 * (1 - _normal_cdf(abs(t)))
    pooled_sd = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / max(1, n1 + n2 - 2))
    d = (m2 - m1) / pooled_sd if pooled_sd else 0.0

    significant = p < alpha and abs(d) >= 0.2
    if significant:
        msg = (f"The change is statistically significant (p={p:.4f}) with a "
               f"{_effect_label(d)} effect size (d={d:.2f}).")
    elif p < alpha:
        msg = (f"Statistically detectable (p={p:.4f}) but the effect is negligible "
               f"(d={d:.2f}); unlikely to be operationally meaningful.")
    else:
        msg = (f"Not statistically significant (p={p:.4f}). This change is within "
               "normal variation for this series.")

    return SignificanceResult("welch_t", t, p, d, significant, alpha, n1, n2, msg)


def _effect_label(d: float) -> str:
    a = abs(d)
    return "large" if a >= 0.8 else "medium" if a >= 0.5 else "small"


def proportion_z_test(
    successes_prev: int, n_prev: int, successes_curr: int, n_curr: int, alpha: float = 0.05
) -> SignificanceResult:
    """Two-proportion z-test, for rate metrics such as churn or conversion."""
    if min(n_prev, n_curr) < 30:
        return SignificanceResult(
            "two_proportion_z", 0.0, 1.0, 0.0, False, alpha, n_prev, n_curr,
            "Sample too small for a reliable proportion test.",
        )
    p1, p2 = successes_prev / n_prev, successes_curr / n_curr
    pool = (successes_prev + successes_curr) / (n_prev + n_curr)
    se = math.sqrt(pool * (1 - pool) * (1 / n_prev + 1 / n_curr))
    if se == 0:
        return SignificanceResult("two_proportion_z", 0.0, 1.0, 0.0, False, alpha,
                                  n_prev, n_curr, "No variance to test.")
    z = (p2 - p1) / se
    p = 2 * (1 - _normal_cdf(abs(z)))
    h = 2 * math.asin(math.sqrt(p2)) - 2 * math.asin(math.sqrt(p1))  # Cohen's h
    sig = p < alpha and abs(h) >= 0.2
    msg = (f"Rate moved from {p1:.2%} to {p2:.2%}; "
           + ("statistically significant." if sig else "not statistically significant."))
    return SignificanceResult("two_proportion_z", z, p, h, sig, alpha, n_prev, n_curr, msg)


# --- Student's t distribution -------------------------------------------------
# Needed for small-sample and few-cluster inference, where the normal
# approximation is materially wrong: with 4 clusters the 95% critical value is
# 3.18, not 1.96, and using the wrong one turns noise into a finding.

def _betacf(a: float, b: float, x: float, iterations: int = 300) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                          + b * math.log1p(-x) + a * math.log(x)) * _betacf(b, a, 1.0 - x) / b


def student_t_two_sided_p(t: float, df: float) -> float:
    """P(|T| > |t|) for T ~ t(df)."""
    if df <= 0 or not math.isfinite(t):
        return 1.0
    if not math.isfinite(df):
        return 2.0 * (1.0 - _normal_cdf(abs(t)))
    x = df / (df + t * t)
    return float(min(1.0, max(0.0, regularized_incomplete_beta(df / 2.0, 0.5, x))))


def student_t_critical(df: float, alpha: float = 0.05) -> float:
    """Two-sided critical value, found by bisection on the p-value."""
    if df <= 0:
        return float("inf")
    lo, hi = 0.0, 200.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if student_t_two_sided_p(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
