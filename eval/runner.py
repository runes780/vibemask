"""
Evaluation runner: drive VibeMask detectors over the golden dataset and report
Precision / Recall / F1 with full false-positive / false-negative detail.

Engines map to HybridDetector configurations so we can measure each layer in
isolation and the combined pipeline::

    regex          schema=F  regex=T  privacy=F  names=F
    schema         schema=T  regex=F  privacy=F  names=F
    det            schema=T  regex=T  privacy=F  names=T   (no model)
    hybrid         schema=T  regex=T  privacy=T  names=T   (full, MLX model)

The detector instance is constructed once and reused across all samples so the
model loads only once (~seconds), not per sample.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")  # silence jieba pkg_resources noise during import

# Allow ``python eval/runner.py`` from repo root without installation gymnastics.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from vibemask.core.span import Span  # noqa: E402
from vibemask.detector.hybrid import HybridDetector  # noqa: E402

from eval.golden import load_golden  # noqa: E402
from eval.metrics import Entity, EvalReport, evaluate  # noqa: E402


ENGINE_FLAGS: dict[str, dict] = {
    "regex":   dict(schema_enabled=False, regex_enabled=True,  privacy_enabled=False, chinese_names_enabled=False),
    "schema":  dict(schema_enabled=True,  regex_enabled=False, privacy_enabled=False, chinese_names_enabled=False),
    "det":     dict(schema_enabled=True,  regex_enabled=True,  privacy_enabled=False, chinese_names_enabled=True),
    # Model in isolation — no deterministic layers. Use with --privacy-backend mlx|opf
    # to compare the MLX port against native OpenAI Privacy Filter.
    "model":   dict(schema_enabled=False, regex_enabled=False, privacy_enabled=True,  chinese_names_enabled=False),
    "hybrid":  dict(schema_enabled=True,  regex_enabled=True,  privacy_enabled=True,  chinese_names_enabled=True),
}


def build_detector(engine: str, privacy_backend: str) -> HybridDetector:
    if engine not in ENGINE_FLAGS:
        raise ValueError(f"unknown engine {engine!r}; choose from {sorted(ENGINE_FLAGS)}")
    flags = ENGINE_FLAGS[engine]
    return HybridDetector(privacy_backend=privacy_backend, **flags)


def span_to_entity(span: Span) -> Entity:
    return Entity(start=span.start, end=span.end, type=span.type.value, text=span.text)


def run_engine(engine: str, privacy_backend: str, iou_threshold: float) -> tuple[EvalReport, dict]:
    """Run one engine over the full golden dataset; return report + timing."""
    golden = load_golden()
    detector = build_detector(engine, privacy_backend)

    t0 = time.time()
    samples = []
    for sid, text, gold_entities in golden:
        spans = detector.detect(text)
        pred_entities = [span_to_entity(s) for s in spans]
        samples.append((sid, text, gold_entities, pred_entities))
    elapsed = time.time() - t0

    report = evaluate(samples, iou_threshold=iou_threshold)
    timing = {
        "engine": engine,
        "samples": len(golden),
        "wall_seconds": round(elapsed, 3),
        "per_sample_ms": round(elapsed / max(1, len(golden)) * 1000, 2),
    }
    return report, timing


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_table(report: EvalReport) -> str:
    header = f"{'TYPE':<18}{'SUP':>5}{'TP':>5}{'FP':>5}{'FN':>5}{'P':>8}{'R':>8}{'F1':>8}{'BND':>7}"
    lines = [header, "-" * len(header)]
    # Order by support desc, then name.
    rows = sorted(report.per_type.values(), key=lambda r: (-r.support, r.type))
    for r in rows:
        lines.append(
            f"{r.type:<18}{r.support:>5}{r.tp:>5}{r.fp:>5}{r.fn:>5}"
            f"{r.precision * 100:>7.1f}%{r.recall * 100:>7.1f}%{r.f1 * 100:>7.1f}%"
            f"{r.boundary_accuracy * 100:>6.1f}%"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'MICRO':<18}{report.total_tp + report.total_fn:>5}{report.total_tp:>5}"
        f"{report.total_fp:>5}{report.total_fn:>5}"
        f"{report.micro_precision * 100:>7.1f}%{report.micro_recall * 100:>7.1f}%"
        f"{report.micro_f1 * 100:>7.1f}%"
    )
    lines.append(f"{'MACRO-F1':<18}{'':>5}{'':>5}{'':>5}{'':>5}{'':>8}{'':>8}{report.macro_f1 * 100:>7.1f}%")
    lines.append(f"{'WEIGHTED-F1':<18}{'':>5}{'':>5}{'':>5}{'':>5}{'':>8}{'':>8}{report.weighted_f1 * 100:>7.1f}%")
    return "\n".join(lines)


def format_errors(report: EvalReport, max_per_kind: int = 12) -> str:
    """Render false positives and false negatives with context for inspection."""
    lines: list[str] = []

    lines.append("\n=== FALSE NEGATIVES (missed PII) ===")
    fns = []
    for s in report.samples:
        for g in s.false_negatives:
            fns.append((s.sample_id, s.text, g))
    for sid, text, g in fns[:max_per_kind]:
        lines.append(f"  [{sid}] {g.type}: {g.text!r}")
    if len(fns) > max_per_kind:
        lines.append(f"  ... {len(fns) - max_per_kind} more")

    lines.append("\n=== FALSE POSITIVES (over-detected) ===")
    fps = []
    for s in report.samples:
        for p in s.false_positives:
            fps.append((s.sample_id, s.text, p))
    for sid, text, p in fps[:max_per_kind]:
        lines.append(f"  [{sid}] {p.type}: {p.text!r}")
    if len(fps) > max_per_kind:
        lines.append(f"  ... {len(fps) - max_per_kind} more")

    lines.append("\n=== BOUNDARY DRIFT (correct type+area, wrong edges) ===")
    drifted = []
    for s in report.samples:
        for g, p, _iou in s.matched:
            if g.start != p.start or g.end != p.end:
                drifted.append((s.sample_id, g, p))
    for sid, g, p in drifted[:max_per_kind]:
        lines.append(f"  [{sid}] gold={g.text!r} pred={p.text!r}")
    if len(drifted) > max_per_kind:
        lines.append(f"  ... {len(drifted) - max_per_kind} more")

    return "\n".join(lines)


def serialize_report(report: EvalReport, timing: dict, engine: str) -> dict:
    return {
        "engine": engine,
        "timing": timing,
        "micro": {
            "precision": round(report.micro_precision, 4),
            "recall": round(report.micro_recall, 4),
            "f1": round(report.micro_f1, 4),
            "tp": report.total_tp, "fp": report.total_fp, "fn": report.total_fn,
        },
        "macro_f1": round(report.macro_f1, 4),
        "weighted_f1": round(report.weighted_f1, 4),
        "per_type": {
            r.type: {
                "support": r.support, "tp": r.tp, "fp": r.fp, "fn": r.fn,
                "precision": round(r.precision, 4),
                "recall": round(r.recall, 4),
                "f1": round(r.f1, 4),
                "boundary_accuracy": round(r.boundary_accuracy, 4),
            }
            for r in sorted(report.per_type.values(), key=lambda r: -r.support)
        },
        "false_negatives": [
            {"sample": s.sample_id, "type": g.type, "text": g.text}
            for s in report.samples for g in s.false_negatives
        ],
        "false_positives": [
            {"sample": s.sample_id, "type": p.type, "text": p.text}
            for s in report.samples for p in s.false_positives
        ],
        "boundary_drift": [
            {"sample": s.sample_id, "gold": g.text, "pred": p.text}
            for s in report.samples for g, p, _ in s.matched
            if g.start != p.start or g.end != p.end
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run VibeMask detection accuracy evaluation.")
    parser.add_argument("--engine", default="hybrid", choices=sorted(ENGINE_FLAGS),
                        help="which detector configuration to evaluate")
    parser.add_argument("--privacy-backend", default="mlx", choices=["mlx", "opf"],
                        help="model backend used when engine includes privacy_filter")
    parser.add_argument("--iou", type=float, default=0.5,
                        help="IoU threshold for a type-matched span to count as a TP")
    parser.add_argument("--report", default=None,
                        help="optional path to write the full JSON report")
    parser.add_argument("--quiet", action="store_true", help="suppress error-detail output")
    args = parser.parse_args(argv)

    print(f"Loading golden dataset...", file=sys.stderr)
    report, timing = run_engine(args.engine, args.privacy_backend, args.iou)

    print(f"\nEngine: {args.engine}  (backend={args.privacy_backend}, IoU>={args.iou})")
    print(f"Timing: {timing['wall_seconds']}s wall, {timing['per_sample_ms']}ms/sample "
          f"over {timing['samples']} samples\n")
    print(format_table(report))
    if not args.quiet:
        print(format_errors(report))

    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(serialize_report(report, timing, args.engine), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nReport written to {path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
