"""
OpenAI Privacy Filter detector integration.

This adapter uses the native OPF Python API for detection only, then maps
detected spans into VibeMask's reversible masking pipeline.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Literal, Optional

from ..core.span import EntityType, SourceType, Span
from .privacy_postprocess import refine_privacy_filter_spans


PRIVACY_FILTER_LABELS: dict[str, EntityType] = {
    "private_person": EntityType.PERSON,
    "private_address": EntityType.ADDRESS,
    "private_email": EntityType.EMAIL,
    "private_phone": EntityType.PHONE,
    "private_url": EntityType.URL,
    "private_date": EntityType.DATE_TIME,
    "account_number": EntityType.ACCOUNT_NUMBER,
    "secret": EntityType.SECRET,
}


class PrivacyFilterDetector:
    """Detect sensitive spans with OpenAI Privacy Filter."""

    def __init__(
        self,
        *,
        device: Literal["cpu", "cuda"] = "cpu",
        checkpoint: Optional[str | Path] = None,
        context_window_length: Optional[int] = None,
        decode_mode: Literal["viterbi", "argmax"] = "viterbi",
    ) -> None:
        self.device = device
        self.checkpoint = str(checkpoint) if checkpoint is not None else None
        self.context_window_length = context_window_length
        self.decode_mode = decode_mode
        self._opf = None

    def detect(self, text: str) -> list[Span]:
        """Return VibeMask spans from OPF's native structured output."""
        if not text:
            return []

        result = self._get_opf().redact(text)
        detected_spans = getattr(result, "detected_spans", ())

        spans: list[Span] = []
        for detected in detected_spans:
            label = str(getattr(detected, "label", "")).strip()
            start = int(getattr(detected, "start"))
            end = int(getattr(detected, "end"))
            span_text = str(getattr(detected, "text", text[start:end]))
            entity_type = PRIVACY_FILTER_LABELS.get(label, EntityType.UNKNOWN)

            spans.append(
                Span(
                    start=start,
                    end=end,
                    text=span_text,
                    type=entity_type,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason=f"privacy_filter:{label or 'unknown'}",
                )
            )

        return refine_privacy_filter_spans(spans, text)

    def _get_opf(self):
        if self._opf is None:
            try:
                opf_module = import_module("opf")
            except ImportError as exc:
                raise RuntimeError(
                    "OpenAI Privacy Filter support requires the optional OPF package. "
                    'Install it with: pip install "vibemask[privacy-filter]" '
                    'or pip install -e ".[privacy-filter]"'
                ) from exc

            kwargs = {
                "device": self.device,
                "output_mode": "typed",
                "decode_mode": self.decode_mode,
            }
            if self.checkpoint is not None:
                kwargs["model"] = self.checkpoint
            if self.context_window_length is not None:
                kwargs["context_window_length"] = self.context_window_length

            self._opf = opf_module.OPF(**kwargs)

        return self._opf
