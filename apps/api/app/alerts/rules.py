"""
Natural-language alert rules compiled to a structured, inspectable form
(spec section 25).

The compiler is deterministic and narrow on purpose. An alert that fires at
3am must be explainable without reference to a model's mood, so the rule is
compiled once, shown back to the author in structured form, and stored. If the
text is ambiguous the compiler raises ``AmbiguousRule`` naming exactly what it
could not determine rather than guessing a threshold -- a wrong guess here
produces either silent misses or pager fatigue, both worse than a question.

An LLM may be used upstream to *rephrase* a request into this grammar, but it
never emits the rule directly: ``compile_rule`` is the only path to an
``AlertRule``.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum

_NUMBER = r"(\d+(?:[.,]\d+)?)"

# Direction is resolved from verbs first. "drops by more than 10%" contains both
# "drops" and "more than"; the verb carries the direction and the comparator is
# only quantifying the threshold, so verbs are checked before comparators.
_DOWN_VERBS = ("drop", "drops", "dropped", "fall", "falls", "fell", "decline",
               "declines", "declined", "decrease", "decreases", "decreased",
               "shrink", "shrinks", "shrank", "worsen", "worsens", "worsened",
               "slump", "slumps", "slumped", "degrade", "degrades", "degraded")
_UP_VERBS = ("rise", "rises", "rose", "increase", "increases", "increased",
             "grow", "grows", "grew", "spike", "spikes", "spiked", "jump",
             "jumps", "jumped", "climb", "climbs", "climbed", "exceed",
             "exceeds", "exceeded", "surge", "surges", "surged")
_DOWN_COMPARATORS = ("below", "under", "less than", "lower than", "beneath")
_UP_COMPARATORS = ("above", "over", "more than", "greater than", "higher than",
                   "at least")

_ANOMALY_WORDS = ("anomaly", "anomalous", "unusual", "unusually", "abnormal",
                  "out of the ordinary", "weird", "strange")

_WINDOWS = {
    "day": 1, "daily": 1, "24 hours": 1, "today": 1,
    "week": 7, "weekly": 7, "7 days": 7,
    "fortnight": 14, "14 days": 14,
    "month": 30, "monthly": 30, "30 days": 30,
    "quarter": 90, "90 days": 90,
}


class Condition(StrEnum):
    THRESHOLD = "threshold"   # metric value crosses an absolute level
    CHANGE = "change"         # metric moves by more than X% vs the prior window
    ANOMALY = "anomaly"       # detector flags a point at/above a severity


class AmbiguousRule(ValueError):
    """Raised when the text does not determine a rule. Carries the missing parts."""

    def __init__(self, message: str, *, missing: list[str]) -> None:
        super().__init__(message)
        self.missing = missing


@dataclass(slots=True)
class AlertRule:
    metric_key: str
    condition: Condition
    operator: str = "lt"                 # "lt" | "gt"
    threshold: float = 0.0               # absolute value, or fraction for CHANGE
    window_days: int = 1
    comparison_days: int = 7
    min_severity: str = "high"           # ANOMALY only
    segment: dict[str, str] = field(default_factory=dict)
    cooldown_hours: int = 24
    source_text: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["condition"] = str(self.condition)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AlertRule":
        data = dict(data)
        data["condition"] = Condition(data["condition"])
        allowed = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def describe(self) -> str:
        """Plain-English readback. Shown to the author before the rule is saved."""
        where = ""
        if self.segment:
            where = " in " + ", ".join(f"{k}={v}" for k, v in self.segment.items())
        if self.condition is Condition.THRESHOLD:
            direction = "falls below" if self.operator == "lt" else "rises above"
            return (f"Alert when {self.metric_key}{where} {direction} "
                    f"{self.threshold:g}, measured over {self.window_days}d.")
        if self.condition is Condition.CHANGE:
            direction = "falls" if self.operator == "lt" else "rises"
            return (f"Alert when {self.metric_key}{where} {direction} by more than "
                    f"{self.threshold * 100:g}% over {self.window_days}d "
                    f"versus the prior {self.comparison_days}d.")
        return (f"Alert on {self.min_severity}-or-worse anomalies in "
                f"{self.metric_key}{where}, checked over {self.window_days}d.")


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _direction(text: str) -> tuple[bool, bool]:
    """(is_down, is_up). Verbs win over comparators; ties go to neither, which
    surfaces as an AmbiguousRule rather than a coin flip."""
    down_verb, up_verb = _has_word(text, _DOWN_VERBS), _has_word(text, _UP_VERBS)
    if down_verb != up_verb:
        return down_verb, up_verb
    if down_verb and up_verb:
        return False, False          # e.g. "rises or falls" -- genuinely ambiguous
    down_cmp, up_cmp = _has_word(text, _DOWN_COMPARATORS), _has_word(text, _UP_COMPARATORS)
    if down_cmp != up_cmp:
        return down_cmp, up_cmp
    return False, False


def _find_metric(text: str, known_metrics: dict[str, str]) -> str | None:
    """known_metrics maps key -> human label. Longest match wins so
    'average order value' is not shadowed by 'order'."""
    haystack = text.lower()
    candidates: list[tuple[int, str]] = []
    for key, label in known_metrics.items():
        for needle in (key.replace("_", " "), key, label.lower()):
            if needle and needle in haystack:
                candidates.append((len(needle), key))
    if not candidates:
        return None
    return max(candidates)[1]


def _find_window(text: str, default: int) -> int:
    haystack = text.lower()
    explicit = re.search(rf"{_NUMBER}\s*(day|days|week|weeks|month|months)", haystack)
    if explicit:
        n = int(float(explicit.group(1).replace(",", "")))
        unit = explicit.group(2)
        return n * {"day": 1, "days": 1, "week": 7, "weeks": 7,
                    "month": 30, "months": 30}[unit]
    for phrase, days in sorted(_WINDOWS.items(), key=lambda kv: -len(kv[0])):
        if phrase in haystack:
            return days
    return default


def compile_rule(text: str, known_metrics: dict[str, str],
                 *, segment: dict[str, str] | None = None) -> AlertRule:
    """Compile alert text into a structured rule, or explain why it cannot."""
    raw = text.strip()
    if not raw:
        raise AmbiguousRule("Alert text is empty.", missing=["metric", "condition"])
    lowered = raw.lower()

    metric = _find_metric(lowered, known_metrics)
    if metric is None:
        raise AmbiguousRule(
            "No known metric was named. Alerts must reference a metric from the "
            f"semantic layer, one of: {', '.join(sorted(known_metrics))}.",
            missing=["metric"])

    is_down, is_up = _direction(lowered)

    if any(w in lowered for w in _ANOMALY_WORDS):
        severity = "critical" if "critical" in lowered else "high"
        return AlertRule(metric_key=metric, condition=Condition.ANOMALY,
                         min_severity=severity,
                         window_days=_find_window(lowered, 30),
                         segment=segment or {}, source_text=raw)

    pct = re.search(rf"{_NUMBER}\s*(?:%|percent)", lowered)
    absolute = re.search(rf"(?:below|above|under|over|than|reaches|hits)\s*"
                         rf"[$€£]?\s*{_NUMBER}", lowered)

    if pct is None and absolute is None:
        raise AmbiguousRule(
            "No threshold found. Say how much, for example "
            "'more than 10%' or 'below 500'.",
            missing=["threshold"])

    if not (is_down or is_up):
        raise AmbiguousRule(
            "Direction is unclear. Say whether the alert is for the metric "
            "falling or rising.",
            missing=["direction"])

    operator = "lt" if is_down else "gt"

    if pct is not None:
        value = float(pct.group(1).replace(",", "")) / 100.0
        return AlertRule(metric_key=metric, condition=Condition.CHANGE,
                         operator=operator, threshold=value,
                         window_days=_find_window(lowered, 7),
                         comparison_days=_find_window(lowered, 7),
                         segment=segment or {}, source_text=raw)

    value = float(absolute.group(1).replace(",", ""))
    return AlertRule(metric_key=metric, condition=Condition.THRESHOLD,
                     operator=operator, threshold=value,
                     window_days=_find_window(lowered, 1),
                     segment=segment or {}, source_text=raw)
