"""
Detection accuracy metrics for VibeMask.

Pure functions — no detector or I/O dependencies. Given a set of gold
(ground-truth) entities and a set of predicted entities for the same text,
compute per-type and aggregate Precision / Recall / F1.

Matching convention
-------------------
A prediction is a True Positive when it matches a gold entity of the SAME type
and their spans overlap with IoU >= ``iou_threshold``. This is the standard NER
entity-level convention and tolerates minor boundary drift (the model returning
a slightly larger span). Exact-boundary accuracy is tracked separately so
over-reach / under-reach stays visible (see :func:`evaluate`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Entity:
    """A typed span of text. Offsets are Python-slice style [start, end)."""

    start: int
    end: int
    type: str
    text: str = ""

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Entity") -> bool:
        return max(self.start, other.start) < min(self.end, other.end)


def iou(a: Entity, b: Entity) -> float:
    """Intersection-over-union of two spans (0..1)."""
    inter = max(0, min(a.end, b.end) - max(a.start, b.start))
    union = max(a.end, b.end) - min(a.start, b.start)
    return inter / union if union > 0 else 0.0


@dataclass
class TypeResult:
    type: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    exact_tp: int = 0  # TPs whose boundaries match gold exactly

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def support(self) -> int:
        """Number of gold entities of this type (recall denominator)."""
        return self.tp + self.fn

    @property
    def boundary_accuracy(self) -> float:
        """Of the matched TPs, fraction with exact boundaries."""
        return self.exact_tp / self.tp if self.tp else 0.0


@dataclass
class SampleReport:
    sample_id: str
    text: str
    gold: list[Entity]
    pred: list[Entity]
    # Matched pairs (gold, pred, iou). Pred may be boundary-drifted.
    matched: list[tuple[Entity, Entity, float]] = field(default_factory=list)
    false_positives: list[Entity] = field(default_factory=list)  # preds with no gold
    false_negatives: list[Entity] = field(default_factory=list)  # golds with no pred


@dataclass
class EvalReport:
    per_type: dict[str, TypeResult] = field(default_factory=dict)
    samples: list[SampleReport] = field(default_factory=list)

    def _bucket(self, type_name: str) -> TypeResult:
        if type_name not in self.per_type:
            self.per_type[type_name] = TypeResult(type=type_name)
        return self.per_type[type_name]

    @property
    def total_tp(self) -> int:
        return sum(r.tp for r in self.per_type.values())

    @property
    def total_fp(self) -> int:
        return sum(r.fp for r in self.per_type.values())

    @property
    def total_fn(self) -> int:
        return sum(r.fn for r in self.per_type.values())

    @property
    def micro_precision(self) -> float:
        tp, fp = self.total_tp, self.total_fp
        return tp / (tp + fp) if (tp + fp) else 0.0

    @property
    def micro_recall(self) -> float:
        tp, fn = self.total_tp, self.total_fn
        return tp / (tp + fn) if (tp + fn) else 0.0

    @property
    def micro_f1(self) -> float:
        p, r = self.micro_precision, self.micro_recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def macro_f1(self) -> float:
        types = [r for r in self.per_type.values() if r.support > 0]
        return sum(r.f1 for r in types) / len(types) if types else 0.0

    @property
    def weighted_f1(self) -> float:
        total_support = sum(r.support for r in self.per_type.values())
        if total_support == 0:
            return 0.0
        return sum(r.f1 * r.support for r in self.per_type.values()) / total_support


def evaluate_sample(
    sample_id: str,
    text: str,
    gold: Iterable[Entity],
    pred: Iterable[Entity],
    iou_threshold: float = 0.5,
) -> SampleReport:
    """Match predicted entities against gold for a single sample.

    Greedy best-IoU matching within the same type. A gold entity consumes at
    most one prediction and vice-versa. Cross-type overlaps are counted as both
    a false positive (for the predicted type) and a false negative (for the gold
    type) — this is what surfaces type-confusion bugs (e.g. an ID card detected
    as ACCOUNT_NUMBER).
    """
    gold_list = list(gold)
    pred_list = list(pred)
    report = SampleReport(sample_id=sample_id, text=text, gold=gold_list, pred=pred_list)

    used_pred: set[int] = set()

    for g in gold_list:
        best_idx, best_iou = -1, 0.0
        for i, p in enumerate(pred_list):
            if i in used_pred:
                continue
            if p.type != g.type:
                continue
            score = iou(g, p)
            if score > best_iou:
                best_iou, best_idx = score, i
        if best_idx >= 0 and best_iou >= iou_threshold:
            used_pred.add(best_idx)
            p = pred_list[best_idx]
            report.matched.append((g, p, best_iou))
        else:
            report.false_negatives.append(g)

    for i, p in enumerate(pred_list):
        if i not in used_pred:
            report.false_positives.append(p)

    return report


def accumulate(report: EvalReport, sample: SampleReport) -> None:
    """Fold a sample's matches into the per-type aggregate buckets.

    Matched pairs always share a type (enforced by :func:`evaluate_sample`), so
    each match increments its single type bucket once. Cross-type overlaps show
    up as unmatched FP + FN on their respective types.
    """
    for g, p, score in sample.matched:  # g.type == p.type guaranteed
        exact = g.start == p.start and g.end == p.end
        bucket = report._bucket(g.type)
        bucket.tp += 1
        bucket.exact_tp += 1 if exact else 0

    for g in sample.false_negatives:
        report._bucket(g.type).fn += 1

    for p in sample.false_positives:
        report._bucket(p.type).fp += 1


def evaluate(
    samples: Iterable[tuple[str, str, Iterable[Entity], Iterable[Entity]]],
    iou_threshold: float = 0.5,
) -> EvalReport:
    """Evaluate a batch of (sample_id, text, gold, pred) tuples.

    Returns an :class:`EvalReport` with per-type and aggregate metrics plus
    per-sample error detail (every FP / FN) for inspection.
    """
    report = EvalReport()
    for sample_id, text, gold, pred in samples:
        sr = evaluate_sample(sample_id, text, gold, pred, iou_threshold)
        report.samples.append(sr)
        accumulate(report, sr)
    return report
