"""
Text replacement engine with reverse-order replacement to prevent offset drift.
"""

from dataclasses import dataclass
from typing import List, Dict, Tuple
from .span import Span


@dataclass
class Replacement:
    """A single replacement operation."""
    start: int
    end: int
    original: str
    masked: str
    
    @property
    def length_diff(self) -> int:
        """Difference in length after replacement."""
        return len(self.masked) - len(self.original)


def apply_replacements(text: str, replacements: List[Replacement]) -> str:
    """
    Apply replacements to text using reverse-order to prevent offset drift.
    
    By replacing from end to start, we ensure that each replacement's
    start/end offsets remain valid throughout the process.
    
    Args:
        text: Original text
        replacements: List of replacements to apply
        
    Returns:
        Text with all replacements applied
    """
    if not replacements:
        return text
    
    # Sort by start position descending (reverse order)
    sorted_replacements = sorted(replacements, key=lambda r: r.start, reverse=True)
    
    result = text
    for r in sorted_replacements:
        # Validate
        if r.start < 0 or r.end > len(result):
            continue
        if result[r.start:r.end] != r.original:
            continue  # Text doesn't match, skip
        
        # Apply replacement
        result = result[:r.start] + r.masked + result[r.end:]
    
    return result


def create_replacements(
    spans: List[Span],
    mask_generator: callable
) -> Tuple[List[Replacement], Dict[str, str]]:
    """
    Create replacements for a list of spans.
    
    Args:
        spans: List of detected spans
        mask_generator: Function that takes (text, type) and returns masked text
        
    Returns:
        Tuple of (replacements list, mapping dict {masked -> original})
    """
    replacements = []
    mappings = {}
    
    for span in spans:
        masked = mask_generator(span.text, span.type)
        
        replacements.append(Replacement(
            start=span.start,
            end=span.end,
            original=span.text,
            masked=masked,
        ))
        
        # Store reverse mapping for restoration
        mappings[masked] = span.text
    
    return replacements, mappings


def restore_text(masked_text: str, mappings: Dict[str, str]) -> str:
    """
    Restore masked text to original using mappings.
    
    Replaces in order of token length (longest first) to avoid
    substring issues.
    
    Args:
        masked_text: Text with masked placeholders
        mappings: Dict of {masked -> original}
        
    Returns:
        Restored original text
    """
    if not mappings:
        return masked_text
    
    # Sort by masked token length descending
    sorted_items = sorted(mappings.items(), key=lambda x: len(x[0]), reverse=True)
    
    result = masked_text
    for masked, original in sorted_items:
        result = result.replace(masked, original)
    
    return result


def calculate_restored_count(
    masked_text: str,
    restored_text: str,
    mappings: Dict[str, str]
) -> Dict[str, int]:
    """
    Calculate how many tokens were successfully restored.
    
    Returns:
        Dict with counts: {"restored": N, "failed": M, "tokens": {...}}
    """
    restored_count = 0
    failed_tokens = []
    
    for masked, original in mappings.items():
        if masked in masked_text:
            if original in restored_text:
                restored_count += 1
            else:
                failed_tokens.append(masked)
    
    return {
        "restored": restored_count,
        "failed": len(failed_tokens),
        "failed_tokens": failed_tokens,
    }
