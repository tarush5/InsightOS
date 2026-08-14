"""
Data profiling and the Data Quality Engine (spec sections 11, 12, 19).

Produces a per-column profile plus a weighted quality score. The score is
computed from measured defects -- it is never assigned by a model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(slots=True)
class ColumnProfile:
    name: str
    dtype: str
    inferred_role: str          # identifier | measure | dimension | temporal | text
    non_null: int
    null_pct: float
    unique_count: int
    unique_pct: float
    stats: dict = field(default_factory=dict)
    top_values: list[dict] = field(default_factory=list)
    outlier_count: int = 0

    def as_dict(self) -> dict:
        return {
            "name": self.name, "dtype": self.dtype, "inferred_role": self.inferred_role,
            "non_null": self.non_null, "null_pct": round(self.null_pct, 6),
            "unique_count": self.unique_count, "unique_pct": round(self.unique_pct, 6),
            "stats": self.stats, "top_values": self.top_values,
            "outlier_count": self.outlier_count,
        }


@dataclass(slots=True)
class QualityIssue:
    code: str
    severity: str               # low | medium | high | critical
    column: str | None
    message: str
    remediation: str
    affected_rows: int = 0
    penalty: float = 0.0

    def as_dict(self) -> dict:
        return {
            "code": self.code, "severity": self.severity, "column": self.column,
            "message": self.message, "remediation": self.remediation,
            "affected_rows": self.affected_rows, "penalty": round(self.penalty, 2),
        }


@dataclass(slots=True)
class DatasetProfile:
    row_count: int
    column_count: int
    duplicate_rows: int
    memory_bytes: int
    columns: list[ColumnProfile]
    issues: list[QualityIssue]
    quality_score: float
    correlations: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "row_count": self.row_count, "column_count": self.column_count,
            "duplicate_rows": self.duplicate_rows, "memory_bytes": self.memory_bytes,
            "quality_score": round(self.quality_score, 1),
            "columns": [c.as_dict() for c in self.columns],
            "issues": [i.as_dict() for i in self.issues],
            "correlations": self.correlations,
        }


_SEVERITY_PENALTY = {"low": 1.0, "medium": 4.0, "high": 10.0, "critical": 20.0}


class DataProfiler:
    def __init__(self, *, null_warn: float = 0.05, null_critical: float = 0.30) -> None:
        self.null_warn = null_warn
        self.null_critical = null_critical

    def profile(self, df: pd.DataFrame, *, key_columns: list[str] | None = None) -> DatasetProfile:
        rows = len(df)
        issues: list[QualityIssue] = []
        columns = [self._profile_column(df[c], rows, issues) for c in df.columns]

        dupes = int(df.duplicated().sum())
        if dupes:
            pct = dupes / rows if rows else 0
            issues.append(QualityIssue(
                code="duplicate_rows",
                severity="high" if pct > 0.02 else "medium",
                column=None,
                message=f"{dupes:,} fully duplicated rows ({pct:.1%} of the dataset).",
                remediation="De-duplicate on the natural key before aggregating; "
                            "duplicated facts inflate every SUM downstream.",
                affected_rows=dupes,
            ))

        for key in key_columns or []:
            if key in df.columns:
                key_dupes = int(df[key].duplicated().sum())
                if key_dupes:
                    issues.append(QualityIssue(
                        code="key_not_unique", severity="critical", column=key,
                        message=f"Declared key '{key}' has {key_dupes:,} repeated values.",
                        remediation="Joins on this column will fan out and multiply measures.",
                        affected_rows=key_dupes,
                    ))

        correlations = self._correlations(df)

        for issue in issues:
            issue.penalty = _SEVERITY_PENALTY[issue.severity]
        score = max(0.0, 100.0 - sum(i.penalty for i in issues))

        return DatasetProfile(
            row_count=rows, column_count=len(df.columns), duplicate_rows=dupes,
            memory_bytes=int(df.memory_usage(deep=True).sum()),
            columns=columns, issues=issues, quality_score=score, correlations=correlations,
        )

    def _profile_column(self, s: pd.Series, rows: int, issues: list[QualityIssue]) -> ColumnProfile:
        non_null = int(s.notna().sum())
        null_pct = 1 - (non_null / rows) if rows else 0.0
        unique = int(s.nunique(dropna=True))
        stats: dict = {}
        outliers = 0

        if null_pct >= self.null_critical:
            issues.append(QualityIssue(
                code="high_null_rate", severity="high", column=s.name,
                message=f"{null_pct:.1%} of '{s.name}' values are missing.",
                remediation="Exclude this column from aggregates, or impute explicitly "
                            "and record the imputation in the evidence trail.",
                affected_rows=rows - non_null,
            ))
        elif null_pct >= self.null_warn:
            issues.append(QualityIssue(
                code="missing_values", severity="medium", column=s.name,
                message=f"{null_pct:.1%} of '{s.name}' values are missing.",
                remediation="Confirm whether missing means zero or unknown; "
                            "SUM() silently treats them as zero.",
                affected_rows=rows - non_null,
            ))

        if pd.api.types.is_numeric_dtype(s) and non_null:
            v = s.dropna().astype(float)
            q1, q3 = float(v.quantile(0.25)), float(v.quantile(0.75))
            iqr = q3 - q1
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            outliers = int(((v < lo) | (v > hi)).sum())
            stats = {
                "min": float(v.min()), "max": float(v.max()),
                "mean": float(v.mean()), "median": float(v.median()),
                "std": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
                "p25": q1, "p75": q3,
                "p95": float(v.quantile(0.95)), "zeros": int((v == 0).sum()),
                "negatives": int((v < 0).sum()),
            }
            if stats["negatives"] and any(t in str(s.name).lower()
                                          for t in ("amount", "price", "revenue", "qty", "quantity")):
                issues.append(QualityIssue(
                    code="impossible_value", severity="high", column=str(s.name),
                    message=f"{stats['negatives']:,} negative values in '{s.name}'.",
                    remediation="Separate refunds/reversals into their own measure "
                                "rather than letting them net silently inside the metric.",
                    affected_rows=stats["negatives"],
                ))
        elif pd.api.types.is_datetime64_any_dtype(s) and non_null:
            v = s.dropna()
            future = int((v > pd.Timestamp.now("UTC").tz_localize(None)).sum())
            stats = {"min": str(v.min()), "max": str(v.max()), "future_dated": future}
            if future:
                issues.append(QualityIssue(
                    code="invalid_date", severity="medium", column=str(s.name),
                    message=f"{future:,} future-dated values in '{s.name}'.",
                    remediation="Filter future dates out of period comparisons.",
                    affected_rows=future,
                ))

        top: list[dict] = []
        if not pd.api.types.is_numeric_dtype(s) and non_null:
            counts = s.value_counts(dropna=True).head(5)
            top = [{"value": str(k), "count": int(v)} for k, v in counts.items()]

        return ColumnProfile(
            name=str(s.name), dtype=str(s.dtype),
            inferred_role=self._role(s, unique, rows),
            non_null=non_null, null_pct=null_pct, unique_count=unique,
            unique_pct=unique / non_null if non_null else 0.0,
            stats=stats, top_values=top, outlier_count=outliers,
        )

    @staticmethod
    def _role(s: pd.Series, unique: int, rows: int) -> str:
        name = str(s.name).lower()
        if pd.api.types.is_datetime64_any_dtype(s):
            return "temporal"
        if unique == rows and rows > 0:
            return "identifier"
        if name.endswith("_id") or name == "id":
            return "identifier"
        if pd.api.types.is_numeric_dtype(s):
            return "measure" if unique > 20 else "dimension"
        if unique <= max(50, rows * 0.05):
            return "dimension"
        return "text"

    @staticmethod
    def _correlations(df: pd.DataFrame, threshold: float = 0.5) -> list[dict]:
        num = df.select_dtypes(include=[np.number])
        if num.shape[1] < 2:
            return []
        corr = num.corr(numeric_only=True)
        out = []
        cols = list(corr.columns)
        for i, a in enumerate(cols):
            for b in cols[i + 1:]:
                r = corr.loc[a, b]
                if pd.notna(r) and abs(r) >= threshold:
                    out.append({
                        "a": a, "b": b, "pearson_r": round(float(r), 4),
                        "strength": "strong" if abs(r) >= 0.8 else "moderate",
                        "note": "Correlation only. Not evidence of causation.",
                    })
        return sorted(out, key=lambda d: abs(d["pearson_r"]), reverse=True)[:20]
