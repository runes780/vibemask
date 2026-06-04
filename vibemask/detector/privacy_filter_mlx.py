"""
MLX backend for OpenAI Privacy Filter.

This uses the community MLX-converted Privacy Filter weights on Apple Silicon
and maps token labels into the same VibeMask Span objects as the official OPF
backend.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any, Optional

from ..core.span import EntityType, SourceType, Span
from .privacy_filter import PRIVACY_FILTER_LABELS
from .privacy_postprocess import refine_privacy_filter_spans


DEFAULT_MLX_MODEL = "mlx-community/openai-privacy-filter-8bit"


class PrivacyFilterMLXDetector:
    """Detect sensitive spans with the MLX Privacy Filter conversion."""

    def __init__(self, *, checkpoint: Optional[str | Path] = None) -> None:
        self.checkpoint = str(checkpoint) if checkpoint is not None else DEFAULT_MLX_MODEL
        self._model = None
        self._tokenizer = None
        self._mx = None

    def detect(self, text: str) -> list[Span]:
        if not text:
            return []

        model, tokenizer, mx = self._get_model()
        encoded = tokenizer(text, return_offsets_mapping=True)
        input_ids = encoded.get("input_ids", [])
        if not input_ids:
            return []

        model_inputs: dict[str, Any] = {"input_ids": mx.array([input_ids])}
        attention_mask = encoded.get("attention_mask")
        if attention_mask is not None:
            model_inputs["attention_mask"] = mx.array([attention_mask])

        outputs = model(**model_inputs)
        logits = getattr(outputs, "logits", outputs)
        mx.eval(logits)

        predicted_ids = mx.argmax(logits, axis=-1)[0].tolist()
        id2label = getattr(getattr(model, "config", None), "id2label", {})
        labels = [_label_for_id(id2label, label_id) for label_id in predicted_ids]

        spans = _spans_from_token_labels(text, labels, encoded.get("offset_mapping", []))
        return refine_privacy_filter_spans(spans, text)

    def _get_model(self):
        if self._model is None or self._tokenizer is None or self._mx is None:
            try:
                mx = import_module("mlx.core")
                mlx_utils = import_module("mlx_embeddings.utils")
            except ImportError as exc:
                raise RuntimeError(
                    "MLX Privacy Filter support requires optional MLX dependencies. "
                    'Install them with: pip install "vibemask[privacy-filter-mlx]"'
                ) from exc

            model, tokenizer = mlx_utils.load(self.checkpoint)
            mx.eval(model.parameters())
            self._model = model
            self._tokenizer = tokenizer
            self._mx = mx

        return self._model, self._tokenizer, self._mx


def _label_for_id(id2label: Any, label_id: int) -> str:
    if isinstance(id2label, dict):
        return str(id2label.get(str(label_id), id2label.get(label_id, "O")))
    try:
        return str(id2label[label_id])
    except (IndexError, KeyError, TypeError):
        return "O"


def _spans_from_token_labels(text: str, labels: list[str], offsets: list[tuple[int, int]]) -> list[Span]:
    spans: list[Span] = []
    active_label: str | None = None
    active_start: int | None = None
    active_end: int | None = None

    def flush() -> None:
        nonlocal active_label, active_start, active_end
        if active_label is not None and active_start is not None and active_end is not None:
            _append_span(spans, text, active_label, active_start, active_end)
        active_label = None
        active_start = None
        active_end = None

    for raw_label, offset in zip(labels, offsets):
        start, end = int(offset[0]), int(offset[1])
        if start == end:
            continue

        prefix, label = _split_bioes_label(raw_label)
        if label is None:
            flush()
            continue

        if prefix == "S":
            flush()
            _append_span(spans, text, label, start, end)
            continue

        if prefix == "B":
            flush()
            active_label = label
            active_start = start
            active_end = end
            continue

        if active_label != label or active_start is None:
            flush()
            active_label = label
            active_start = start

        active_end = end
        if prefix == "E":
            flush()

    flush()
    return spans


def _split_bioes_label(raw_label: str) -> tuple[str, str | None]:
    if not raw_label or raw_label == "O":
        return "O", None
    if "-" not in raw_label:
        return "S", raw_label
    prefix, label = raw_label.split("-", 1)
    if prefix not in {"B", "I", "E", "S"}:
        return "S", label
    return prefix, label


def _append_span(spans: list[Span], text: str, label: str, start: int, end: int) -> None:
    start, end = _trim_offsets(text, start, end)
    if start >= end:
        return
    span_text = text[start:end]
    spans.append(
        Span(
            start=start,
            end=end,
            text=span_text,
            type=PRIVACY_FILTER_LABELS.get(label, EntityType.UNKNOWN),
            source=SourceType.PRIVACY_FILTER,
            confidence=1.0,
            reason=f"privacy_filter_mlx:{label or 'unknown'}",
        )
    )


def _trim_offsets(text: str, start: int, end: int) -> tuple[int, int]:
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end
