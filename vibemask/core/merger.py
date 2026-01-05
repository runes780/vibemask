"""
Span merging and conflict resolution algorithm.
"""

from typing import List
from .span import Span, EntityType, SourceType, TYPE_PRIORITY


def validate_span(span: Span, original_text: str) -> bool:
    """
    Validate a span against the original text.
    
    Returns True if span is valid, False otherwise.
    """
    # Check bounds
    if span.start < 0 or span.end > len(original_text):
        return False
    
    # Check text match
    if original_text[span.start:span.end] != span.text:
        return False
    
    # Check reasonable length
    if span.length == 0 or span.length > 256:
        return False
    
    return True


def merge_spans(spans: List[Span], original_text: str) -> List[Span]:
    """
    Merge overlapping spans, resolving conflicts using priority rules.
    
    Algorithm:
    1. Validate all spans against original text
    2. Sort by start position, then priority (desc), then length (desc)
    3. Greedily select non-overlapping spans, preferring higher priority
    
    Args:
        spans: List of candidate spans
        original_text: The original text for validation
        
    Returns:
        List of non-overlapping accepted spans, sorted by position
    """
    # Step 0: Validate spans
    valid_spans = [s for s in spans if validate_span(s, original_text)]
    
    if not valid_spans:
        return []
    
    # Step 1: Sort by start (asc), priority (desc), length (desc)
    valid_spans.sort(key=lambda s: (s.start, -s.priority, -s.length))
    
    # Step 2: Greedy selection with overlap resolution
    accepted: List[Span] = []
    
    for span in valid_spans:
        # Find all overlapping accepted spans
        overlapping = [a for a in accepted if span.overlaps(a)]
        
        if not overlapping:
            # No overlap, accept directly
            accepted.append(span)
        else:
            # Handle overlap based on priority and containment
            should_accept = True  # Default to accept unless rejected
            to_remove = []
            
            for existing in overlapping:
                # CRITICAL: If existing span CONTAINS new span and has higher type priority,
                # reject the new span (e.g., URL contains PERSON -> reject PERSON)
                if existing.contains(span):
                    existing_type_priority = TYPE_PRIORITY.get(existing.type, 0)
                    span_type_priority = TYPE_PRIORITY.get(span.type, 0)
                    if existing_type_priority >= span_type_priority:
                        should_accept = False
                        break
                
                # Check if new span should replace existing
                if _should_replace(span, existing):
                    to_remove.append(existing)
                elif _should_replace(existing, span):
                    # Existing wins, skip new span
                    should_accept = False
                    break
            
            if should_accept:
                for r in to_remove:
                    accepted.remove(r)
                accepted.append(span)
    
    # Sort by position for output
    return sorted(accepted, key=lambda s: s.start)


def _should_replace(new: Span, existing: Span) -> bool:
    """
    Determine if new span should replace existing span.
    
    Rules:
    1. If new contains existing and has higher priority -> replace
    2. If new has significantly higher priority (specific types) -> replace
    3. PHONE/EMAIL/IDCN always beat PERSON/ORG/ADDRESS
    """
    # Rule 1: Containment + higher priority
    if new.contains(existing) and new.priority > existing.priority:
        return True
    
    # Rule 2: Type priority (specific types beat general)
    new_type_priority = TYPE_PRIORITY.get(new.type, 0)
    existing_type_priority = TYPE_PRIORITY.get(existing.type, 0)
    
    if new_type_priority > existing_type_priority + 20:
        return True
    
    # Rule 3: Same position, higher source priority
    if new.start == existing.start and new.end == existing.end:
        if new.source == SourceType.SCHEMA:
            return True
        if new.source == SourceType.REGEX and existing.source not in (SourceType.SCHEMA, SourceType.REGEX):
            return True
    
    return False


def deduplicate_spans(spans: List[Span]) -> List[Span]:
    """
    Remove duplicate spans (same start, end, text, type).
    """
    seen = set()
    result = []
    
    for span in spans:
        key = (span.start, span.end, span.text, span.type)
        if key not in seen:
            seen.add(key)
            result.append(span)
    
    return result
