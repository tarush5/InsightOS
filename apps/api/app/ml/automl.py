"""
AutoML (spec sections 28-31).

Fitting a model is the easy part; sklearn does it in three lines. The work here
is in the things that decide whether the resulting number should be believed,
and every one of them is a place where a naive implementation reports a good
score for a useless model:

* **Temporal splits by default.** Business data is time-ordered, and a random
  split trains on next month to predict last month. It is the single most common
  reason a churn model scores 0.94 in a notebook and fails in production. If a
  date column exists, the split follows it.
* **A baseline the model has to beat.** Predicting the majority class scores 97%
  accuracy on a 3% churn rate. Every run reports the trivial baseline, and a
  model that cannot beat it is returned as *not useful* rather than as a model.
* **Calibration, not just discrimination.** A ranking that is right in order but
  wrong in magnitude is fine for a leaderboard and dangerous for a decision that
  multiplies a probability by a revenue figure. Brier score is always reported.
* **Leakage detection.** A single feature that alone predicts the target almost
  perfectly is nearly always leakage — a column populated *because* the outcome
  happened. The run flags it rather than celebrating the AUC.

Experiment tracking writes a local JSON run record. MLflow is the intended
backend and `RunRecorder` is the swap point; nothing else in the module knows
where runs are stored.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

MIN_ROWS = 200
MIN_POSITIVES = 20
LEAKAGE_AUC = 0.98
TEST_FRACTION = 0.25
# Smallest improvement worth reporting even when the sample is large.
MIN_MARGIN = 0.02
# How many standard errors above the baseline a score must sit before the
# improvement is treated as real rather than as sampling noise.
BASELINE_SIGMAS = 2.0


class NotEnoughData(ValueError):
    """Raised instead of fitting something that cannot be evaluated."""


@dataclass(slots=True)
class Candidate:
    name: str
    metrics: dict[str, float]
    fit_seconds: float
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"name": self.name,
                "metrics": {k: round(float(v), 5) for k, v in self.metrics.items()},
                "fit_seconds": round(self.fit_seconds, 3), "params": self.params}


@dataclass(slots=True)
class TrainingResult:
    task: str
    target: str
    n_train: int
    n_test: int
    split: str                       # "temporal" | "random"
    base_rate: float | None
    baseline: dict[str, float]
    best: Candidate | None
    candidates: list[Candidate]
    importances: list[dict]
    beats_baseline: bool
    useful: bool
    warnings: list[str] = field(default_factory=list)
    leakage_suspects: list[dict] = field(default_factory=list)
    run_id: str = ""

    def verdict(self) -> str:
        if not self.useful:
            return ("Not usable. " + (self.warnings[0] if self.warnings else
                                      "The model did not beat the trivial baseline, "
                                      "so it adds nothing over guessing."))
        assert self.best is not None
        headline = self.best.metrics.get("roc_auc") or self.best.metrics.get("r2")
        return (f"{self.best.name} beat the baseline "
                f"({'AUC' if 'roc_auc' in self.best.metrics else 'R²'} "
                f"{headline:.3f}). Held out on a {self.split} split.")

    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "task": self.task, "target": self.target,
                "split": self.split, "n_train": self.n_train, "n_test": self.n_test,
                "base_rate": (None if self.base_rate is None
                              else round(self.base_rate, 5)),
                "baseline": {k: round(float(v), 5) for k, v in self.baseline.items()},
                "best": self.best.as_dict() if self.best else None,
                "candidates": [c.as_dict() for c in self.candidates],
                "importances": self.importances,
                "beats_baseline": self.beats_baseline, "useful": self.useful,
                "leakage_suspects": self.leakage_suspects,
                "warnings": self.warnings, "verdict": self.verdict()}


class RunRecorder:
    """Writes a run record. The swap point for MLflow."""

    def __init__(self, directory: str | Path = "mlruns") -> None:
        self.directory = Path(directory)

    def record(self, result: TrainingResult) -> str:
        run_id = result.run_id or uuid.uuid4().hex[:12]
        payload = {"run_id": run_id,
                   "recorded_at": datetime.now(timezone.utc).isoformat(),
                   **result.as_dict()}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / f"{run_id}.json").write_text(json.dumps(payload, indent=2))
        except OSError as exc:
            # Losing the record must not lose the model. Say so rather than
            # failing a training run over a filesystem problem.
            log.warning("automl.run_not_recorded error=%s", exc)
            result.warnings.append(
                "The run completed but could not be written to the experiment "
                f"log ({exc}). The result below is not reproducible from disk.")
        return run_id


def detect_task(y: pd.Series) -> str:
    """Classification when the target is binary or low-cardinality; regression
    otherwise. Guessing wrong here produces confident nonsense, so anything
    ambiguous is refused rather than assumed."""
    clean = y.dropna()
    if clean.empty:
        raise NotEnoughData("The target column is entirely null.")
    unique = clean.nunique()
    if unique < 2:
        raise NotEnoughData(
            f"The target has a single value ({clean.iloc[0]!r}), so there is "
            "nothing to predict.")
    if unique == 2:
        return "binary_classification"
    if pd.api.types.is_numeric_dtype(clean) and unique > 20:
        return "regression"
    if unique <= 20:
        raise NotEnoughData(
            f"The target has {unique} distinct values. Multi-class training is "
            "not implemented; collapse it to a binary outcome or pick a "
            "continuous target.")
    raise NotEnoughData("The target is non-numeric with high cardinality.")


class AutoML:
    """Trains a small set of candidates and reports whether any is worth using."""

    def __init__(self, *, recorder: RunRecorder | None = None,
                 random_state: int = 7, test_fraction: float = TEST_FRACTION) -> None:
        self.recorder = recorder or RunRecorder()
        self.random_state = random_state
        self.test_fraction = test_fraction

    def train(self, frame: pd.DataFrame, *, target: str,
              date_column: str | None = None,
              exclude: list[str] | None = None) -> TrainingResult:
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import (GradientBoostingClassifier,
                                      GradientBoostingRegressor,
                                      RandomForestClassifier, RandomForestRegressor)
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression, Ridge
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder, StandardScaler

        if target not in frame.columns:
            raise NotEnoughData(f"Target column '{target}' is not in the data.")

        data = frame.dropna(subset=[target]).copy()
        if len(data) < MIN_ROWS:
            raise NotEnoughData(
                f"{len(data)} usable rows; at least {MIN_ROWS} are needed for a "
                "held-out evaluation to mean anything.")

        task = detect_task(data[target])
        warnings: list[str] = []

        dropped = set(exclude or []) | {target}
        if date_column:
            dropped.add(date_column)
        features = [c for c in data.columns if c not in dropped]
        # Free-text and identifier columns are dropped rather than one-hot
        # encoded into thousands of useless columns.
        # Checked by dtype family, not `== object`: pandas 3 reports string
        # columns as `str`, so the object comparison silently stopped matching
        # and identifier columns were being one-hot encoded into thousands of
        # useless features.
        high_card = [c for c in features
                     if not pd.api.types.is_numeric_dtype(data[c])
                     and not pd.api.types.is_datetime64_any_dtype(data[c])
                     and data[c].nunique() > 50]
        if high_card:
            warnings.append(
                f"Dropped high-cardinality text column(s) {', '.join(high_card)}; "
                "they are identifiers or free text, not features.")
            features = [c for c in features if c not in high_card]
        if not features:
            raise NotEnoughData("No usable feature columns remain after filtering.")

        X = data[features]
        y = data[target]
        if task == "binary_classification":
            classes = sorted(y.dropna().unique())
            y = (y == classes[-1]).astype(int)
            positives = int(y.sum())
            if positives < MIN_POSITIVES or (len(y) - positives) < MIN_POSITIVES:
                raise NotEnoughData(
                    f"Only {positives} positive and {len(y) - positives} negative "
                    f"cases. At least {MIN_POSITIVES} of each are needed before a "
                    "score is meaningful.")

        X_train, X_test, y_train, y_test, split = self._split(
            X, y, data, date_column, warnings)

        numeric = [c for c in features if pd.api.types.is_numeric_dtype(data[c])]
        categorical = [c for c in features if c not in numeric]
        preprocess = ColumnTransformer([
            ("num", Pipeline([("impute", SimpleImputer(strategy="median")),
                              ("scale", StandardScaler())]), numeric),
            ("cat", Pipeline([
                ("impute", SimpleImputer(strategy="most_frequent")),
                ("encode", OneHotEncoder(handle_unknown="ignore",
                                         min_frequency=0.01))]), categorical),
        ], remainder="drop")

        if task == "binary_classification":
            models = {
                "logistic_regression": LogisticRegression(
                    max_iter=1000, class_weight="balanced"),
                "random_forest": RandomForestClassifier(
                    n_estimators=200, min_samples_leaf=5,
                    class_weight="balanced", random_state=self.random_state),
                "gradient_boosting": GradientBoostingClassifier(
                    random_state=self.random_state),
            }
        else:
            models = {
                "ridge": Ridge(alpha=1.0),
                "random_forest": RandomForestRegressor(
                    n_estimators=200, min_samples_leaf=5,
                    random_state=self.random_state),
                "gradient_boosting": GradientBoostingRegressor(
                    random_state=self.random_state),
            }

        baseline = self._baseline(task, y_train, y_test)
        candidates: list[Candidate] = []
        fitted: dict[str, object] = {}
        for name, estimator in models.items():
            pipeline = Pipeline([("prep", preprocess), ("model", estimator)])
            started = time.perf_counter()
            try:
                pipeline.fit(X_train, y_train)
            except Exception as exc:
                warnings.append(f"{name} failed to fit: {type(exc).__name__}. Skipped.")
                continue
            elapsed = time.perf_counter() - started
            metrics = self._evaluate(task, pipeline, X_test, y_test)
            candidates.append(Candidate(name, metrics, elapsed))
            fitted[name] = pipeline

        if not candidates:
            raise NotEnoughData("Every candidate model failed to fit.")

        key = "roc_auc" if task == "binary_classification" else "r2"
        best = max(candidates, key=lambda c: c.metrics.get(key, float("-inf")))
        beats = self._beats_baseline(task, best, baseline, key, y_test)

        # Discrimination and calibration are different properties and the best
        # model on one is regularly not the best on the other. Selecting on AUC
        # alone reliably picks a class-weighted model that ranks well and emits
        # probabilities worse than the prior -- fine for a leaderboard, wrong for
        # any decision that multiplies a probability by a revenue figure.
        best, calibration_note = self._apply_calibration_check(
            task, best, candidates, baseline, key)
        if calibration_note:
            warnings.append(calibration_note)

        if not beats:
            warnings.insert(0,
                "No candidate beat the trivial baseline by a meaningful margin. "
                "The features available do not predict this target — report that, "
                "rather than shipping a model that looks precise and is not.")

        suspects = self._leakage(task, X_train, y_train, X_test, y_test,
                                 preprocess, features)
        if suspects:
            warnings.insert(0,
                f"Possible target leakage: {', '.join(s['feature'] for s in suspects)} "
                "predicts the outcome almost perfectly on its own. A feature that "
                "good is usually populated because the outcome happened.")

        importances = (self._importances(fitted[best.name], X_test, y_test, task)
                       if beats else [])

        result = TrainingResult(
            task=task, target=target, n_train=len(X_train), n_test=len(X_test),
            split=split,
            base_rate=float(y.mean()) if task == "binary_classification" else None,
            baseline=baseline, best=best if beats else None,
            candidates=candidates, importances=importances,
            beats_baseline=beats, useful=beats and not suspects,
            warnings=warnings, leakage_suspects=suspects)
        result.run_id = self.recorder.record(result)
        log.info("automl.trained task=%s best=%s useful=%s", task, best.name,
                 result.useful)
        return result

    # --- internals --------------------------------------------------------
    @staticmethod
    def _auc_standard_error(auc: float, n_pos: int, n_neg: int) -> float:
        """Hanley & McNeil (1982) standard error of the AUC.

        Needed because a fixed margin is the wrong test. On 500 held-out rows
        with an 11% base rate, chance AUC varies by roughly +/-0.04, so a flat
        0.02 threshold passes pure noise as a working model -- which it did,
        until this test replaced it.
        """
        if n_pos < 1 or n_neg < 1:
            return 1.0
        q1 = auc / (2.0 - auc)
        q2 = 2.0 * auc * auc / (1.0 + auc)
        variance = ((auc * (1 - auc)
                     + (n_pos - 1) * (q1 - auc * auc)
                     + (n_neg - 1) * (q2 - auc * auc))
                    / (n_pos * n_neg))
        return float(np.sqrt(max(variance, 0.0)))

    def _beats_baseline(self, task, best, baseline, key, y_test) -> bool:
        """The improvement must exceed the uncertainty in measuring it."""
        score = best.metrics.get(key, 0.0)
        floor = baseline.get(key, 0.0)
        if task != "binary_classification":
            return score > floor + MIN_MARGIN
        y = np.asarray(y_test)
        n_pos, n_neg = int(y.sum()), int(len(y) - y.sum())
        margin = max(MIN_MARGIN,
                     BASELINE_SIGMAS * self._auc_standard_error(score, n_pos, n_neg))
        return score > floor + margin

    @staticmethod
    def _apply_calibration_check(task, best, candidates, baseline, key
                                 ) -> tuple[Candidate, str]:
        """Prefer a model that is both discriminating and calibrated.

        Returns the selected candidate and a note when the choice was changed or
        when no candidate is well calibrated.
        """
        if task != "binary_classification":
            return best, ""
        baseline_brier = baseline.get("brier")
        if baseline_brier is None or best.metrics.get("brier", 1.0) <= baseline_brier:
            return best, ""

        # The AUC winner is worse-calibrated than predicting the base rate.
        # Look for a candidate that beats the baseline on both.
        alternatives = [
            c for c in candidates
            if c.metrics.get("brier", 1.0) <= baseline_brier
            and c.metrics.get(key, 0.0) > baseline.get(key, 0.0) + 0.02
        ]
        if alternatives:
            chosen = max(alternatives, key=lambda c: c.metrics.get(key, 0.0))
            return chosen, (
                f"Selected {chosen.name} over {best.name}, which ranked higher "
                f"(AUC {best.metrics[key]:.3f} vs {chosen.metrics[key]:.3f}) but "
                f"produced probabilities worse calibrated than the base rate "
                f"(Brier {best.metrics['brier']:.3f} vs baseline "
                f"{baseline_brier:.3f}). Its ordering is useful; its numbers are "
                "not, and anything that multiplies a probability by a value needs "
                "the numbers.")
        return best, (
            f"No candidate is well calibrated: the best, {best.name}, has a Brier "
            f"score of {best.metrics['brier']:.3f} against {baseline_brier:.3f} for "
            "simply predicting the base rate. Use it to rank cases, not to "
            "estimate probabilities, and calibrate before using the scores in an "
            "expected-value calculation.")

    def _split(self, X, y, data, date_column, warnings):
        """Temporal where possible. A random split on time-ordered data trains
        on the future to predict the past."""
        if date_column and date_column in data.columns:
            order = pd.to_datetime(data[date_column], errors="coerce")
            if order.notna().mean() > 0.9:
                ranked = order.rank(method="first", na_option="bottom")
                cutoff = ranked.quantile(1 - self.test_fraction)
                train_mask = ranked <= cutoff
                if train_mask.sum() >= MIN_ROWS // 2 and (~train_mask).sum() >= 30:
                    return (X[train_mask], X[~train_mask],
                            y[train_mask], y[~train_mask], "temporal")
                warnings.append(
                    "Not enough rows on one side of the date cutoff for a "
                    "temporal split; fell back to random. Scores may be "
                    "optimistic if the data is time-ordered.")

        from sklearn.model_selection import train_test_split

        stratify = y if y.nunique() == 2 else None
        if date_column is None:
            warnings.append(
                "No date column was given, so the split is random. If these rows "
                "are time-ordered, the scores below are optimistic.")
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=self.test_fraction, random_state=self.random_state,
            stratify=stratify)
        return X_train, X_test, y_train, y_test, "random"

    @staticmethod
    def _baseline(task, y_train, y_test) -> dict[str, float]:
        """What you get for free. Predicting the majority class scores 97%
        accuracy on a 3% churn rate, which is why accuracy is not the headline."""
        if task == "binary_classification":
            prior = float(np.mean(y_train))
            predicted = np.full(len(y_test), prior)
            majority = float(max(prior, 1 - prior))
            return {"strategy": 0.0, "accuracy": majority, "roc_auc": 0.5,
                    "brier": float(np.mean((y_test - predicted) ** 2))}
        mean = float(np.mean(y_train))
        residual = np.asarray(y_test, dtype=float) - mean
        return {"r2": 0.0, "mae": float(np.mean(np.abs(residual))),
                "rmse": float(np.sqrt(np.mean(residual ** 2)))}

    @staticmethod
    def _evaluate(task, pipeline, X_test, y_test) -> dict[str, float]:
        from sklearn.metrics import (accuracy_score, average_precision_score,
                                     brier_score_loss, mean_absolute_error,
                                     r2_score, roc_auc_score)

        if task == "binary_classification":
            proba = pipeline.predict_proba(X_test)[:, 1]
            return {
                "roc_auc": float(roc_auc_score(y_test, proba)),
                # PR-AUC is the honest headline under class imbalance; ROC-AUC
                # stays high on rare-event problems where the model is useless.
                "pr_auc": float(average_precision_score(y_test, proba)),
                "brier": float(brier_score_loss(y_test, proba)),
                "accuracy": float(accuracy_score(y_test, (proba >= 0.5).astype(int))),
            }
        predicted = pipeline.predict(X_test)
        return {
            "r2": float(r2_score(y_test, predicted)),
            "mae": float(mean_absolute_error(y_test, predicted)),
            "rmse": float(np.sqrt(np.mean((np.asarray(y_test) - predicted) ** 2))),
        }

    def _leakage(self, task, X_train, y_train, X_test, y_test, preprocess,
                 features) -> list[dict]:
        """Fit each feature alone. One that predicts the target almost perfectly
        by itself is nearly always populated because the outcome happened."""
        if task != "binary_classification":
            return []
        from sklearn.metrics import roc_auc_score

        suspects: list[dict] = []
        for feature in features:
            column = X_train[[feature]]
            if not pd.api.types.is_numeric_dtype(column[feature]):
                continue
            values_train = column[feature].fillna(column[feature].median())
            values_test = X_test[feature].fillna(column[feature].median())
            if values_train.nunique() < 2:
                continue
            try:
                score = roc_auc_score(y_test, values_test)
            except ValueError:
                continue
            score = max(score, 1 - score)      # direction does not matter
            if score >= LEAKAGE_AUC:
                suspects.append({"feature": feature, "solo_auc": round(float(score), 4)})
        return suspects

    def _importances(self, pipeline, X_test, y_test, task) -> list[dict]:
        """Permutation importance on held-out data.

        Measured on the test set, not the training set: importance computed
        where the model was fitted rewards memorisation. Model-agnostic, so the
        numbers are comparable across candidates.
        """
        from sklearn.inspection import permutation_importance

        scoring = "roc_auc" if task == "binary_classification" else "r2"
        try:
            computed = permutation_importance(
                pipeline, X_test, y_test, n_repeats=5,
                random_state=self.random_state, scoring=scoring)
        except Exception as exc:
            log.warning("automl.importance_failed error=%s", exc)
            return []
        ranked = sorted(
            ({"feature": name,
              "importance": round(float(mean), 5),
              "std": round(float(std), 5)}
             for name, mean, std in zip(X_test.columns,
                                        computed.importances_mean,
                                        computed.importances_std, strict=True)),
            key=lambda d: d["importance"], reverse=True)
        return ranked[:15]
