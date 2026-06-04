"""
Span data structure for representing detected entities.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EntityType(Enum):
    """Types of sensitive entities."""
    PERSON = "PERSON"
    PHONE = "PHONE"
    PHONE_NUMBER = "PHONE_NUMBER"  # Presidio standard
    EMAIL = "EMAIL"
    EMAIL_ADDRESS = "EMAIL_ADDRESS"  # Presidio standard
    IDCN = "IDCN"  # Chinese ID card
    CN_ID_CARD = "CN_ID_CARD"  # Presidio custom
    ORG = "ORG"
    ORGANIZATION = "ORGANIZATION"  # Presidio standard
    ADDRESS = "ADDRESS"
    LOCATION = "LOCATION"  # Presidio standard
    URL = "URL"
    ACCOUNT_NUMBER = "ACCOUNT_NUMBER"
    SECRET = "SECRET"
    
    # Financial
    CREDIT_CARD = "CREDIT_CARD"
    CRYPTO = "CRYPTO"
    IBAN_CODE = "IBAN_CODE"
    US_BANK_NUMBER = "US_BANK_NUMBER"
    
    # Personal
    US_SSN = "US_SSN"
    US_DRIVER_LICENSE = "US_DRIVER_LICENSE"
    US_PASSPORT = "US_PASSPORT"
    IP_ADDRESS = "IP_ADDRESS"
    DATE_TIME = "DATE_TIME"
    NRP = "NRP" # Nationality/Religion/Political
    
    UNKNOWN = "UNKNOWN"


class SourceType(Enum):
    """Source of entity detection."""
    REGEX = "regex"
    SCHEMA = "schema"
    PRIVACY_FILTER = "privacy_filter"
    HEURISTIC = "heuristic"
    LLM_JUDGE = "llm_judge"
    LLM_SCAN = "llm_scan"


# Base priority scores for each source type
SOURCE_PRIORITY = {
    SourceType.SCHEMA: 100,
    SourceType.REGEX: 90,
    SourceType.PRIVACY_FILTER: 80,
    SourceType.LLM_JUDGE: 70,
    SourceType.LLM_SCAN: 60,
    SourceType.HEURISTIC: 50,
}

# Type priority for conflict resolution (higher = more specific)
TYPE_PRIORITY = {
    EntityType.SECRET: 110,
    EntityType.ACCOUNT_NUMBER: 105,
    EntityType.PHONE: 100,
    EntityType.EMAIL: 100,
    EntityType.IDCN: 100,
    EntityType.URL: 95,
    EntityType.ADDRESS: 50,
    EntityType.ORG: 40,
    EntityType.PERSON: 30,
}


@dataclass
class Span:
    """
    Represents a detected sensitive entity in text.
    
    Attributes:
        start: Start offset (inclusive, 0-indexed)
        end: End offset (exclusive, Python slicing style)
        text: Original text content
        type: Entity type (PERSON, PHONE, etc.)
        source: Detection source (regex, llm, etc.)
        confidence: Confidence score (0-1)
        reason: Optional debug reason
    """
    start: int
    end: int
    text: str
    type: EntityType
    source: SourceType
    confidence: float = 1.0
    reason: Optional[str] = None
    replacement: Optional[str] = None  # Custom replacement text (e.g. from LLM)
    
    def __post_init__(self):
        """Validate span data."""
        if self.start < 0:
            raise ValueError(f"start must be >= 0, got {self.start}")
        if self.end < self.start:
            raise ValueError(f"end ({self.end}) must be >= start ({self.start})")
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
    
    @property
    def length(self) -> int:
        """Length of the span."""
        return self.end - self.start
    
    @property
    def priority(self) -> float:
        """
        Calculate priority score for conflict resolution.
        Higher priority wins in overlap situations.
        
        Formula: base_source_priority + length_bonus + confidence
        """
        base = SOURCE_PRIORITY.get(self.source, 0)
        length_bonus = self.length / 100  # Longer spans get slight bonus
        return base + length_bonus + self.confidence
    
    def overlaps(self, other: "Span") -> bool:
        """Check if this span overlaps with another."""
        return max(self.start, other.start) < min(self.end, other.end)
    
    def contains(self, other: "Span") -> bool:
        """Check if this span fully contains another."""
        return self.start <= other.start and self.end >= other.end
    
    def is_adjacent(self, other: "Span") -> bool:
        """Check if this span is adjacent to another (touching but not overlapping)."""
        return self.end == other.start or other.end == self.start
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "type": self.type.value,
            "source": self.source.value,
            "confidence": self.confidence,
            "reason": self.reason,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Span":
        """Create Span from dictionary."""
        return cls(
            start=data["start"],
            end=data["end"],
            text=data["text"],
            type=EntityType(data["type"]),
            source=SourceType(data["source"]),
            confidence=data.get("confidence", 1.0),
            reason=data.get("reason"),
        )
    
    def __repr__(self) -> str:
        return (
            f"Span({self.start}:{self.end}, '{self.text[:20]}...', "
            f"{self.type.value}, {self.source.value}, conf={self.confidence:.2f})"
        )
