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

# Inference windowing. The Privacy Filter is a YaRN-RoPE decoder whose config
# reports ``max_position_embeddings = 131072`` and ``initial_context_length =
# 4096`` (the length it was trained around) — it is NOT a 512-position BERT.
# We target the trained length (4096) so the model sees near-complete documents
# in a single window, and align the overlap with ``sliding_window = 128`` so an
# entity straddling a boundary is recovered whole from the neighbour window.
#
# The binding constraint is unified-memory allocation on Apple Silicon, not the
# model architecture: the logits tensor is ``[1, seq_len, num_labels]`` plus an
# 8-layer MoE KV cache. On a 16 GB machine 4096 is comfortable, but we still
# auto-downgrade on Metal allocation failure rather than crash (see
# ``_infer_labels``).
_DEFAULT_MAX_TOKENS = 4096
_MIN_MAX_TOKENS = 512  # never go below the legacy window
_OVERLAP_TOKENS = 128  # matches config sliding_window


class PrivacyFilterMLXDetector:
    """Detect sensitive spans with the MLX Privacy Filter conversion."""

    def __init__(
        self,
        *,
        checkpoint: Optional[str | Path] = None,
        decode_mode: str = "viterbi",
        max_tokens: Optional[int] = None,
    ) -> None:
        self.checkpoint = str(checkpoint) if checkpoint is not None else DEFAULT_MLX_MODEL
        # ``viterbi`` enforces valid BIOES sequences (fixes the type-flipping
        # fragmentation naive argmax produces); ``argmax`` is the legacy path,
        # kept for comparison/regression checks.
        self.decode_mode = decode_mode
        # Inference window. Defaults to the model's trained context length;
        # callers (e.g. HybridDetector.privacy_context_window) can override.
        # Auto-downgrades at runtime if Metal cannot allocate the window.
        self.max_tokens = max_tokens or _DEFAULT_MAX_TOKENS
        self._effective_max_tokens: Optional[int] = None
        self._model = None
        self._tokenizer = None
        self._mx = None

    def detect(self, text: str) -> list[Span]:
        if not text:
            return []

        model, tokenizer, mx = self._get_model()
        encoded = tokenizer(text, return_offsets_mapping=True)
        input_ids = encoded.get("input_ids", [])
        offsets = encoded.get("offset_mapping", [])
        if not input_ids:
            return []

        id2label = getattr(getattr(model, "config", None), "id2label", {})

        # Run inference in fixed-size windows so large documents (tens of
        # thousands of tokens) don't blow up the logits tensor and OOM. The
        # tokenizer's offset_mapping already maps each token to the *original*
        # text, so spans built per window carry global offsets — we just dedup
        # the overlap between adjacent windows.
        all_spans: list[Span] = []
        seen: set = set()
        n = len(input_ids)
        start = 0
        while start < n:
            window = self._effective_window()
            end = min(start + window, n)
            labels = self._infer_windowed(mx, model, id2label, input_ids[start:end])
            if not labels:
                raise RuntimeError("MLX Privacy Filter returned no labels for a non-empty window.")
            # An OOM retry may have truncated the submitted chunk to a smaller
            # effective window. Advance from what was actually processed, not
            # from the pre-OOM end, or the gap would never be scanned.
            processed_end = min(start + len(labels), n)
            for span in _spans_from_token_labels(text, labels, offsets[start:processed_end]):
                key = (span.start, span.end, span.type, span.text)
                if key not in seen:
                    seen.add(key)
                    all_spans.append(span)
            if processed_end >= n:
                break
            current_window = self._effective_window()
            overlap = min(_OVERLAP_TOKENS, current_window // 4)
            start = max(start + 1, processed_end - overlap)

        return refine_privacy_filter_spans(all_spans, text)

    def _effective_window(self) -> int:
        """The currently-active inference window (possibly downgraded)."""
        return self._effective_max_tokens or self.max_tokens

    def _infer_windowed(self, mx, model, id2label, chunk_ids: list[int]) -> list[str]:
        """Run inference on one window, auto-downgrading the window size on
        Metal allocation failure.

        On the first OOM we halve ``_effective_max_tokens`` (down to
        ``_MIN_MAX_TOKENS``) and retry the *same* chunk truncated to the new
        size. The downgrade is cached so subsequent windows reuse it. This lets
        us target the trained 4096 context on roomy machines while degrading
        gracefully on memory-constrained ones, instead of crashing.
        """
        while True:
            try:
                return self._infer_labels(mx, model, id2label, chunk_ids)
            except MemoryError:
                raise  # Python-level memory errors are not recoverable here.
            except Exception as exc:  # Metal allocation / runtime failures
                if not self._is_allocation_failure(exc):
                    raise
                new_size = max(_MIN_MAX_TOKENS, self._effective_window() // 2)
                if new_size >= self._effective_window():
                    raise  # cannot downgrade further
                self._effective_max_tokens = new_size
                chunk_ids = chunk_ids[:new_size]
                if not chunk_ids:
                    return []

    @staticmethod
    def _is_allocation_failure(exc: Exception) -> bool:
        """Heuristic: did the MLX/Metal backend fail to allocate memory?"""
        msg = str(exc).lower()
        return any(s in msg for s in ("out of memory", "oom", "allocation", "metal"))

    def _infer_labels(self, mx, model, id2label, chunk_ids: list[int]) -> list[str]:
        """Run the model on one token window and return the decoded label sequence."""
        model_inputs: dict[str, Any] = {
            "input_ids": mx.array([chunk_ids]),
            "attention_mask": mx.array([[1] * len(chunk_ids)]),
        }
        outputs = model(**model_inputs)
        logits = getattr(outputs, "logits", outputs)
        mx.eval(logits)

        if self.decode_mode == "argmax":
            predicted_ids = mx.argmax(logits, axis=-1)[0].tolist()
            return [_label_for_id(id2label, i) for i in predicted_ids]
        # Constrained Viterbi over the per-token logits — collapses the invalid
        # BIOES sequences (I-without-B, mid-entity type flips) that naive argmax
        # emits and that fragment into garbage spans.
        num_labels = len(id2label)
        label_list = [_label_for_id(id2label, i) for i in range(num_labels)]
        return _viterbi_decode(logits[0].tolist(), label_list)

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


def _split_label(raw_label: str) -> tuple[str, str | None]:
    """Split a BIOES label into (prefix, type). ``O`` -> ("O", None)."""
    if not raw_label or raw_label == "O":
        return "O", None
    if "-" in raw_label:
        prefix, label = raw_label.split("-", 1)
        if prefix in {"B", "I", "E", "S"}:
            return prefix, label
        return "S", label
    return "S", raw_label


def _viterbi_decode(logit_rows: list[list[float]], label_list: list[str]) -> list[str]:
    """Constrained Viterbi over BIOES label sequences.

    The MLX port exposes a plain token-classification head (no CRF), so naive
    per-token argmax can emit invalid sequences — e.g. ``I-X`` with no preceding
    ``B-X``, or a type flip mid-entity (``I-address, I-person, I-address``),
    which the span decoder then fragments into garbage single-char spans.

    This Viterbi finds the highest-total-logit sequence that respects hard
    BIOES transition constraints:

      * position 0 may not be ``I-`` or ``E-``
      * ``I-X`` / ``E-X`` may only follow ``B-X`` / ``I-X`` of the SAME type

    No learned transition weights — just structural validity. This collapses
    the type-flipping fragmentation while otherwise tracking argmax closely.
    """
    n = len(logit_rows)
    if n == 0:
        return []
    num_labels = len(label_list)
    if num_labels == 0:
        return ["O"] * n
    NEG = -1e18

    parts = [_split_label(lbl) for lbl in label_list]

    # allowed[i][j]: may label i be followed by label j?
    # Only constrained when target j is I-/E- (must continue same type).
    allowed: list[list[bool]] = [[True] * num_labels for _ in range(num_labels)]
    for j in range(num_labels):
        pj, tj = parts[j]
        if pj in ("I", "E"):
            for i in range(num_labels):
                pi, ti = parts[i]
                allowed[i][j] = pi in ("B", "I") and ti == tj

    start_ok = [parts[j][0] not in ("I", "E") for j in range(num_labels)]

    row0 = logit_rows[0]
    score = [row0[j] if start_ok[j] else NEG for j in range(num_labels)]
    back: list[list[int]] = [[0] * num_labels for _ in range(n)]

    for t in range(1, n):
        row = logit_rows[t]
        new_score = [NEG] * num_labels
        for j in range(num_labels):
            pj, _ = parts[j]
            best, best_i = NEG, 0
            for i in range(num_labels):
                if not allowed[i][j]:
                    continue
                s = score[i]
                if s > best:
                    best, best_i = s, i
            new_score[j] = (best + row[j]) if best > NEG else NEG
            back[t][j] = best_i
        score = new_score

    best_j = max(range(num_labels), key=lambda j: score[j])
    seq = [best_j]
    for t in range(n - 1, 0, -1):
        best_j = back[t][best_j]
        seq.append(best_j)
    seq.reverse()
    return [label_list[j] for j in seq]


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
