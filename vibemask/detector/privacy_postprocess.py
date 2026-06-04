"""
Post-processing for OpenAI Privacy Filter spans.

The model can occasionally attach surrounding school/work-history text to
private_date spans in Chinese resumes. This module trims that overreach into a
more useful institution span when the institution is explicit in the text.
"""

from __future__ import annotations

import re

from ..core.span import EntityType, SourceType, Span


INSTITUTION_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{2,30}(?:学校|大学|学院|幼儿园|中学|小学|师范学校)"
)


def refine_privacy_filter_spans(spans: list[Span], text: str) -> list[Span]:
    refined: list[Span] = []
    for span in spans:
        replacement = _refine_private_date_overreach(span, text)
        if replacement is None:
            refined.append(span)
        else:
            refined.append(replacement)
    return refined


def _refine_private_date_overreach(span: Span, text: str) -> Span | None:
    if span.type != EntityType.DATE_TIME:
        return None
    if "privacy_filter:private_date" not in (span.reason or ""):
        return None

    # Keep plain dates intact. Only intervene when the span contains narrative text.
    if len(span.text) <= 16 and not any("\u4e00" <= char <= "\u9fff" for char in span.text):
        return None
    if not any(marker in span.text for marker in ("学习", "毕业", "结业", "专业")):
        return None

    match = INSTITUTION_PATTERN.search(span.text)
    if not match:
        return None

    start = span.start + match.start()
    end = span.start + match.end()
    institution = text[start:end]
    return Span(
        start=start,
        end=end,
        text=institution,
        type=EntityType.ORG,
        source=SourceType.PRIVACY_FILTER,
        confidence=span.confidence,
        reason="privacy_filter:private_date_refined_org",
    )
