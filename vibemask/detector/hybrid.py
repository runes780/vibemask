"""
Hybrid detector that combines deterministic rules with OpenAI Privacy Filter.

Privacy Filter is used for contextual natural-language PII. Regex and schema
layers remain in front for high-recall structured data in Office files.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Literal, Optional

from ..core.merger import merge_spans
from ..core.span import EntityType, Span
from .privacy_postprocess import is_false_person, is_field_label
from .regex import detect_by_regex
from .schema_context import detect_schema_context


class HybridDetector:
    """Run VibeMask's recommended layered detection pipeline."""

    def __init__(
        self,
        *,
        privacy_enabled: bool = True,
        regex_enabled: bool = True,
        schema_enabled: bool = True,
        chinese_names_enabled: bool = True,
        privacy_device: Literal["cpu", "cuda"] = "cpu",
        privacy_checkpoint: Optional[str | Path] = None,
        privacy_context_window: Optional[int] = None,
        privacy_decode_mode: Literal["viterbi", "argmax"] = "viterbi",
        privacy_backend: Literal["opf", "mlx"] = "mlx",
    ) -> None:
        self.privacy_enabled = privacy_enabled
        self.regex_enabled = regex_enabled
        self.schema_enabled = schema_enabled
        self.chinese_names_enabled = chinese_names_enabled
        self.privacy_device = privacy_device
        self.privacy_checkpoint = privacy_checkpoint
        self.privacy_context_window = privacy_context_window
        self.privacy_decode_mode = privacy_decode_mode
        self.privacy_backend = privacy_backend
        self._cached_privacy_detector = None

    def detect(self, text: str) -> list[Span]:
        if not text:
            return []

        spans: list[Span] = []
        if self.schema_enabled:
            spans.extend(detect_schema_context(text))
        if self.regex_enabled:
            spans.extend(detect_by_regex(text))
        if self.privacy_enabled:
            spans.extend(self._detect_privacy_filter(text))
        if self.chinese_names_enabled and _contains_cjk(text):
            spans.extend(self._detect_chinese_names(text))

        # Drop field-label words (headers like 姓名/手机号) and obvious non-name
        # PERSON spans (万元/单位/single CJK char) before merging — these are
        # column labels or financial/form vocabulary, never real PII values.
        spans = [
            s for s in spans
            if not is_field_label(s.text)
            and not (s.type == EntityType.PERSON and is_false_person(s.text))
        ]
        return merge_spans(spans, text)

    def detect_document(self, processor: object, text: str) -> list[Span]:
        """Run hybrid detection with optional document-structure context."""
        if not text:
            return []

        spans: list[Span] = []
        if self.regex_enabled:
            spans.extend(detect_by_regex(text))
        if self.privacy_enabled:
            privacy_detector = self._privacy_detector()
            spans.extend(self._detect_document_privacy(processor, text, privacy_detector))
        if self.schema_enabled:
            # Schema spans are kept as a fallback layer, below Privacy Filter usage in default docs.
            spans.extend(detect_schema_context(text))
        if self.chinese_names_enabled and _contains_cjk(text):
            spans.extend(self._detect_chinese_names(text))

        # Drop field-label words (headers like 姓名/手机号) and obvious non-name
        # PERSON spans (万元/单位/single CJK char) before merging — these are
        # column labels or financial/form vocabulary, never real PII values.
        spans = [
            s for s in spans
            if not is_field_label(s.text)
            and not (s.type == EntityType.PERSON and is_false_person(s.text))
        ]
        return merge_spans(spans, text)

    def _detect_privacy_filter(self, text: str) -> list[Span]:
        return self._privacy_detector().detect(text)

    def _privacy_detector(self):
        if self._cached_privacy_detector is None:
            if self.privacy_backend == "mlx":
                privacy_filter_mlx = import_module("vibemask.detector.privacy_filter_mlx")
                self._cached_privacy_detector = privacy_filter_mlx.PrivacyFilterMLXDetector(
                    checkpoint=self.privacy_checkpoint,
                    decode_mode=self.privacy_decode_mode,
                    max_tokens=self.privacy_context_window,
                )
            else:
                privacy_filter = import_module("vibemask.detector.privacy_filter")
                self._cached_privacy_detector = privacy_filter.PrivacyFilterDetector(
                    device=self.privacy_device,
                    checkpoint=self.privacy_checkpoint,
                    context_window_length=self.privacy_context_window,
                    decode_mode=self.privacy_decode_mode,
                )
        return self._cached_privacy_detector

    def _detect_document_privacy(self, processor: object, text: str, privacy_detector: object) -> list[Span]:
        # Unified structured serialization: any privacy table (XLSX / DOCX /
        # legacy .xls) is reconstructed into ``"header: value"`` rows via one
        # format-agnostic serializer, replacing the previous per-format
        # context builders. The model runs once on the serialized table text
        # (context path) and once on the flat text with table cells excluded
        # (narrative path). The two passes cover disjoint regions, so no manual
        # overlap dedup is needed between them — merge_spans handles the rest.
        table_serializer = import_module("vibemask.core.table_serializer")
        privacy_context = import_module("vibemask.detector.privacy_context")

        segments = list(getattr(processor, "_segments", []) or [])
        context_text, mappings = table_serializer.serialize_tables(segments)

        spans: list[Span] = []
        if context_text and mappings:
            # Context path: model sees headers-as-labels next to each value.
            detected = privacy_detector.detect(context_text)
            spans.extend(table_serializer.map_context_spans(detected, mappings))
            # Structural fallback: one typed span per privacy-column value whose
            # format matches its header — fills the model's recall gap on
            # identifiers (学号/工号/手机…) without emitting garbage on messy
            # tables. Reuses the privacy_context helper unchanged for now.
            spans.extend(privacy_context.structural_spans_from_mappings(mappings))

            # Narrative path: model on the flat text, but only outside the
            # table cells the structured path already owns. This keeps the
            # model from re-flagging table cells (position-code / run-split
            # false positives) while still catching PII in surrounding prose.
            table_cell_ranges = privacy_context.detect_table_cell_ranges(processor)
        else:
            # No privacy table: plain flat-text detection.
            table_cell_ranges = None

        flat_spans = privacy_detector.detect(text)
        if table_cell_ranges:
            flat_spans = [
                s for s in flat_spans
                if not any(r0 <= s.start < r1 or r0 < s.end <= r1 for r0, r1 in table_cell_ranges)
            ]
        spans.extend(flat_spans)
        return spans

    def _detect_chinese_names(self, text: str) -> list[Span]:
        smart_detector = import_module("vibemask.detector.smart_detector")
        if not smart_detector.is_smart_detection_available():
            return []
        return smart_detector.detect_chinese_names_smart(text, strict=True, use_llm=False)


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)
