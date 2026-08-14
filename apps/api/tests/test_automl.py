"""AutoML: the checks that decide whether a score should be believed."""
import numpy as np
import pandas as pd
import pytest

from app.ml.automl import AutoML, NotEnoughData, RunRecorder, detect_task


@pytest.fixture
def recorder(tmp_path):
    return RunRecorder(tmp_path / "runs")


def churn_frame(n=2000, seed=3, leak=False, signal=True):
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 60, n)
    support = rng.poisson(2, n)
    region = rng.choice(["North", "South", "East", "West"], n)
    if signal:
        logit = -2.2 - 0.045 * tenure + 0.42 * support + 0.9 * (region == "South")
    else:
        logit = np.full(n, -2.0)
    churned = rng.binomial(1, 1 / (1 + np.exp(-logit)))
    frame = pd.DataFrame({
        "signup_date": pd.date_range("2024-01-01", periods=n, freq="6h"),
        "tenure_months": tenure, "support_tickets": support,
        "region": region, "churned": churned})
    if leak:
        # A column populated *because* the outcome happened.
        frame["cancellation_survey_score"] = np.where(
            churned == 1, rng.normal(9, 0.4, n), rng.normal(1, 0.4, n))
    return frame


# --- task detection ----------------------------------------------------------

def test_binary_target_is_classification():
    assert detect_task(pd.Series([0, 1, 1, 0])) == "binary_classification"


def test_continuous_target_is_regression():
    assert detect_task(pd.Series(np.linspace(0, 100, 200))) == "regression"


def test_a_constant_target_is_refused():
    with pytest.raises(NotEnoughData, match="single value"):
        detect_task(pd.Series([1] * 50))


def test_multiclass_is_refused_rather_than_guessed():
    """Guessing the task produces confident nonsense."""
    with pytest.raises(NotEnoughData, match="Multi-class"):
        detect_task(pd.Series(list("abcde") * 20))


# --- guards ------------------------------------------------------------------

def test_too_few_rows_is_refused(recorder):
    with pytest.raises(NotEnoughData, match="usable rows"):
        AutoML(recorder=recorder).train(churn_frame(n=50), target="churned")


def test_too_few_positives_is_refused(recorder):
    """A score computed on four positive cases is noise with a decimal point."""
    frame = churn_frame(n=600)
    frame.loc[frame.index[10:], "churned"] = 0
    with pytest.raises(NotEnoughData, match="positive"):
        AutoML(recorder=recorder).train(frame, target="churned")


def test_a_missing_target_is_named(recorder):
    with pytest.raises(NotEnoughData, match="not in the data"):
        AutoML(recorder=recorder).train(churn_frame(), target="nope")


# --- splitting ---------------------------------------------------------------

def test_a_date_column_produces_a_temporal_split(recorder):
    """A random split on time-ordered data trains on the future to predict the
    past, which is the most common reason a churn model fails in production."""
    result = AutoML(recorder=recorder).train(
        churn_frame(), target="churned", date_column="signup_date")
    assert result.split == "temporal"


def test_no_date_column_warns_that_the_split_is_random(recorder):
    result = AutoML(recorder=recorder).train(churn_frame(), target="churned")
    assert result.split == "random"
    assert any("optimistic" in w for w in result.warnings)


# --- baseline gating ---------------------------------------------------------

def test_a_real_signal_is_found_and_ranked(recorder):
    result = AutoML(recorder=recorder).train(
        churn_frame(), target="churned", date_column="signup_date")
    assert result.beats_baseline and result.useful
    top = [i["feature"] for i in result.importances[:3]]
    assert "tenure_months" in top and "support_tickets" in top


def test_noise_does_not_produce_a_model(recorder):
    """The features do not predict the target. Saying so is the right output;
    shipping something that looks precise is not."""
    result = AutoML(recorder=recorder).train(
        churn_frame(signal=False), target="churned", date_column="signup_date")
    assert not result.beats_baseline and not result.useful
    assert result.best is None
    assert "baseline" in result.verdict().lower()


def test_the_baseline_is_always_reported(recorder):
    """Predicting the majority class scores ~89% accuracy here, which is why
    accuracy is not the headline."""
    result = AutoML(recorder=recorder).train(
        churn_frame(), target="churned", date_column="signup_date")
    assert result.baseline["accuracy"] > 0.85
    assert result.baseline["roc_auc"] == 0.5


# --- calibration -------------------------------------------------------------

def test_selection_prefers_a_calibrated_model_over_a_better_ranker(recorder):
    """The AUC winner here is a class-weighted model whose probabilities are
    worse than the base rate. Its ordering is useful; its numbers are not."""
    result = AutoML(recorder=recorder).train(
        churn_frame(), target="churned", date_column="signup_date")
    assert result.best is not None
    assert result.best.metrics["brier"] <= result.baseline["brier"]
    ranked_higher = [c for c in result.candidates
                     if c.metrics["roc_auc"] > result.best.metrics["roc_auc"]]
    if ranked_higher:
        assert any("calibrated" in w for w in result.warnings)


def test_pr_auc_is_reported_alongside_roc_auc(recorder):
    """ROC-AUC stays high on rare-event problems where the model is useless."""
    result = AutoML(recorder=recorder).train(
        churn_frame(), target="churned", date_column="signup_date")
    assert all("pr_auc" in c.metrics for c in result.candidates)


# --- leakage -----------------------------------------------------------------

def test_target_leakage_is_flagged_not_celebrated(recorder):
    """A feature that alone predicts the outcome almost perfectly is nearly
    always populated because the outcome happened."""
    result = AutoML(recorder=recorder).train(
        churn_frame(leak=True), target="churned", date_column="signup_date")
    assert result.leakage_suspects
    assert result.leakage_suspects[0]["feature"] == "cancellation_survey_score"
    assert not result.useful
    assert any("leakage" in w for w in result.warnings)


def test_an_excluded_column_is_not_used(recorder):
    result = AutoML(recorder=recorder).train(
        churn_frame(leak=True), target="churned", date_column="signup_date",
        exclude=["cancellation_survey_score"])
    assert not result.leakage_suspects
    assert result.useful


# --- regression --------------------------------------------------------------

def test_regression_runs_and_reports_r2(recorder):
    rng = np.random.default_rng(11)
    n = 800
    x = rng.normal(size=n)
    frame = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "x": x, "noise": rng.normal(size=n),
        "value": 3 * x + rng.normal(scale=0.5, size=n)})
    result = AutoML(recorder=recorder).train(frame, target="value",
                                             date_column="date")
    assert result.task == "regression"
    assert result.best is not None and result.best.metrics["r2"] > 0.8


# --- experiment tracking -----------------------------------------------------

def test_a_run_record_is_written(recorder, tmp_path):
    result = AutoML(recorder=recorder).train(
        churn_frame(), target="churned", date_column="signup_date")
    written = list((tmp_path / "runs").glob("*.json"))
    assert len(written) == 1 and result.run_id in written[0].name


def test_an_unwritable_run_log_does_not_lose_the_model(tmp_path):
    """Losing the record must not lose the result, but it must be said."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    result = AutoML(recorder=RunRecorder(blocked)).train(
        churn_frame(), target="churned", date_column="signup_date")
    assert result.best is not None
    assert any("experiment log" in w for w in result.warnings)


def test_high_cardinality_text_is_dropped_with_a_note(recorder):
    frame = churn_frame()
    frame["customer_ref"] = [f"CUST-{i}" for i in range(len(frame))]
    result = AutoML(recorder=recorder).train(
        frame, target="churned", date_column="signup_date")
    assert any("high-cardinality" in w for w in result.warnings)


def test_the_baseline_margin_scales_with_uncertainty():
    """A flat 0.02 margin passed pure noise: on 500 held-out rows at an 11% base
    rate, chance AUC varies by roughly ±0.04 on its own."""
    from app.ml.automl import AutoML

    automl = AutoML()
    se = automl._auc_standard_error(0.53, n_pos=57, n_neg=443)
    assert 0.02 < se < 0.10
    # A large, balanced sample tightens the requirement.
    tight = automl._auc_standard_error(0.53, n_pos=5000, n_neg=5000)
    assert tight < se


@pytest.mark.parametrize("seed", [3, 17, 29, 41])
def test_noise_is_rejected_across_seeds(recorder, seed):
    """One lucky seed is not evidence. This is the check that caught the flat
    margin, so it runs on several."""
    result = AutoML(recorder=recorder).train(
        churn_frame(signal=False, seed=seed), target="churned",
        date_column="signup_date")
    assert not result.useful, result.verdict()
