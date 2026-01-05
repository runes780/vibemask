"""
Regex-based detector for structured sensitive data.
L1 detection layer - high confidence, deterministic rules.
"""

import re
from typing import List, Pattern
from ..core.span import Span, EntityType, SourceType


# ============================================================
# Regex Patterns for Sensitive Data
# ============================================================

PATTERNS: dict[EntityType, Pattern] = {
    # Chinese mobile phone numbers (11 digits starting with 1[3-9])
    EntityType.PHONE: re.compile(
        r'(?<![0-9])'  # Not preceded by digit
        r'1[3-9]\d{9}'  # 11-digit mobile number
        r'(?![0-9])',   # Not followed by digit
    ),
    
    # Email addresses
    EntityType.EMAIL: re.compile(
        r'[a-zA-Z0-9._%+-]+'
        r'@'
        r'[a-zA-Z0-9.-]+'
        r'\.[a-zA-Z]{2,}',
        re.IGNORECASE,
    ),
    
    # Chinese ID card numbers (18 digits, last can be X)
    EntityType.IDCN: re.compile(
        r'(?<![0-9Xx])'
        r'[1-9]\d{5}'           # Region code (6 digits)
        r'(?:19|20)\d{2}'       # Birth year
        r'(?:0[1-9]|1[0-2])'    # Birth month
        r'(?:0[1-9]|[12]\d|3[01])'  # Birth day
        r'\d{3}'                # Sequence
        r'[\dXx]'               # Check digit
        r'(?![0-9Xx])',
    ),
}

# URL patterns for personal pages
URL_PATTERN = re.compile(
    r'https?://'                        # Protocol
    r'(?:www\.)?'                       # Optional www
    r'[a-zA-Z0-9]+'                    # Domain start
    r'(?:[.-][a-zA-Z0-9]+)*'           # Domain parts
    r'\.[a-zA-Z]{2,}'                   # TLD
    r'(?:/[^\s<>"\']*)?',              # Path (optional)
    re.IGNORECASE,
)

# Personal/academic page patterns (high priority)
PERSONAL_PAGE_DOMAINS = {
    'person.zju.edu.cn', 'homepage.zju.edu.cn',
    'github.com', 'linkedin.com', 'researchgate.net',
    'scholar.google.com', 'orcid.org',
    'weibo.com', 'zhihu.com',
}

# Additional patterns for common formats
PHONE_WITH_SEPARATORS = re.compile(
    r'(?<![0-9])'
    r'1[3-9]\d'
    r'[-\s]?'
    r'\d{4}'
    r'[-\s]?'
    r'\d{4}'
    r'(?![0-9])',
)

# Landline with area code
LANDLINE_PATTERN = re.compile(
    r'(?<![0-9])'
    r'0\d{2,3}'     # Area code
    r'[-\s]?'
    r'\d{7,8}'      # Number
    r'(?![0-9])',
)


def detect_by_regex(text: str) -> List[Span]:
    """
    Detect sensitive entities using regex patterns.
    
    This is the L1 detection layer with high confidence.
    
    Args:
        text: Input text to scan
        
    Returns:
        List of detected Span objects
    """
    spans = []
    
    # Apply main patterns
    for entity_type, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            spans.append(Span(
                start=match.start(),
                end=match.end(),
                text=match.group(),
                type=entity_type,
                source=SourceType.REGEX,
                confidence=1.0,
                reason=f"regex:{entity_type.value.lower()}",
            ))
    
    # Apply phone with separators (normalize before adding)
    for match in PHONE_WITH_SEPARATORS.finditer(text):
        matched_text = match.group()
        # Check if this overlaps with existing phone match
        normalized = re.sub(r'[-\s]', '', matched_text)
        if len(normalized) == 11:
            # Check for overlap with existing spans
            overlaps = any(
                s.type == EntityType.PHONE and 
                max(s.start, match.start()) < min(s.end, match.end())
                for s in spans
            )
            if not overlaps:
                spans.append(Span(
                    start=match.start(),
                    end=match.end(),
                    text=matched_text,
                    type=EntityType.PHONE,
                    source=SourceType.REGEX,
                    confidence=0.95,
                    reason="regex:phone_with_sep",
                ))
    
    # Detect URLs (especially personal/academic pages)
    for match in URL_PATTERN.finditer(text):
        url = match.group()
        # Check if it's a personal/sensitive URL
        is_personal = any(domain in url.lower() for domain in PERSONAL_PAGE_DOMAINS)
        # Also check for patterns like /~username or /username
        has_user_path = '/~' in url or re.search(r'/[a-z]+[0-9]*$', url.lower())
        
        if is_personal or has_user_path:
            spans.append(Span(
                start=match.start(),
                end=match.end(),
                text=url,
                type=EntityType.URL,
                source=SourceType.REGEX,
                confidence=0.95 if is_personal else 0.8,
                reason="regex:personal_url" if is_personal else "regex:url",
            ))
    
    return spans


def detect_phone(text: str) -> List[Span]:
    """Detect phone numbers only."""
    spans = []
    
    for match in PATTERNS[EntityType.PHONE].finditer(text):
        spans.append(Span(
            start=match.start(),
            end=match.end(),
            text=match.group(),
            type=EntityType.PHONE,
            source=SourceType.REGEX,
            confidence=1.0,
        ))
    
    return spans


def detect_email(text: str) -> List[Span]:
    """Detect email addresses only."""
    spans = []
    
    for match in PATTERNS[EntityType.EMAIL].finditer(text):
        spans.append(Span(
            start=match.start(),
            end=match.end(),
            text=match.group(),
            type=EntityType.EMAIL,
            source=SourceType.REGEX,
            confidence=1.0,
        ))
    
    return spans


def detect_idcn(text: str) -> List[Span]:
    """Detect Chinese ID card numbers only."""
    spans = []
    
    for match in PATTERNS[EntityType.IDCN].finditer(text):
        # Additional validation for ID card
        id_text = match.group()
        if _validate_idcn(id_text):
            spans.append(Span(
                start=match.start(),
                end=match.end(),
                text=id_text,
                type=EntityType.IDCN,
                source=SourceType.REGEX,
                confidence=1.0,
            ))
        else:
            # Still add but with lower confidence
            spans.append(Span(
                start=match.start(),
                end=match.end(),
                text=id_text,
                type=EntityType.IDCN,
                source=SourceType.REGEX,
                confidence=0.7,
                reason="checksum_failed",
            ))
    
    return spans


def _validate_idcn(id_number: str) -> bool:
    """
    Validate Chinese ID card number using checksum.
    """
    if len(id_number) != 18:
        return False
    
    # Weights for each position
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    # Check digits
    check_chars = "10X98765432"
    
    try:
        total = sum(int(id_number[i]) * weights[i] for i in range(17))
        expected = check_chars[total % 11]
        return id_number[17].upper() == expected
    except (ValueError, IndexError):
        return False
