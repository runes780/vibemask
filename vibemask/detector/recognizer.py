"""
Presidio-compatible Recognizer Interface.
Provides standardized entity detection API.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Set


class EntityType(str, Enum):
    """Supported entity types."""
    PERSON = "PERSON"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    IDCN = "IDCN"           # Chinese ID card
    URL = "URL"
    ORG = "ORG"
    ADDRESS = "ADDRESS"
    CREDIT_CARD = "CREDIT_CARD"
    DATE = "DATE"
    CUSTOM = "CUSTOM"


class SourceType(str, Enum):
    """Detection source types."""
    REGEX = "regex"
    HEURISTIC = "heuristic"
    MODEL = "model"
    LLM = "llm"
    CUSTOM = "custom"


@dataclass
class RecognizerResult:
    """
    Presidio-compatible recognition result.
    
    This is the standard output format for all recognizers,
    ensuring compatibility with Presidio and VibeMask internals.
    """
    entity_type: str        # Entity type (PERSON, PHONE, etc.)
    start: int              # Start position in text
    end: int                # End position in text
    score: float            # Confidence score (0-1)
    
    # VibeMask extensions
    text: str = ""          # The matched text
    source: str = "regex"   # Detection source
    reason: str = ""        # Explanation for detection
    
    def __post_init__(self):
        """Validate result."""
        if not 0 <= self.score <= 1:
            raise ValueError(f"Score must be in [0, 1], got {self.score}")
        if self.end < self.start:
            raise ValueError(f"End ({self.end}) must be >= start ({self.start})")
    
    @property
    def length(self) -> int:
        """Length of matched text."""
        return self.end - self.start
    
    def overlaps(self, other: "RecognizerResult") -> bool:
        """Check if this result overlaps with another."""
        return max(self.start, other.start) < min(self.end, other.end)
    
    def contains(self, other: "RecognizerResult") -> bool:
        """Check if this result fully contains another."""
        return self.start <= other.start and self.end >= other.end
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "entity_type": self.entity_type,
            "start": self.start,
            "end": self.end,
            "score": self.score,
            "text": self.text,
            "source": self.source,
            "reason": self.reason,
        }


class Recognizer(ABC):
    """
    Abstract base class for entity recognizers.
    
    Implements Presidio-compatible interface for entity detection.
    
    Implementations:
    - RegexRecognizer: Pattern-based detection (L1)
    - HeuristicRecognizer: Rule-based detection (L2)
    - ModelRecognizer: ML model-based detection (L3)
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Recognizer name for logging/debugging."""
        pass
    
    @property
    @abstractmethod
    def supported_entities(self) -> Set[str]:
        """Set of entity types this recognizer can detect."""
        pass
    
    @property
    def priority(self) -> int:
        """
        Priority for conflict resolution.
        Higher priority wins when results overlap.
        Default: 50
        """
        return 50
    
    @abstractmethod
    def analyze(
        self, 
        text: str, 
        entities: Optional[List[str]] = None
    ) -> List[RecognizerResult]:
        """
        Analyze text and return recognized entities.
        
        Args:
            text: Text to analyze
            entities: Optional list of entity types to detect.
                      If None, detect all supported entities.
                      
        Returns:
            List of RecognizerResult objects
        """
        pass


# ============================================================
# Recognizer Registry
# ============================================================

_RECOGNIZER_REGISTRY: Dict[str, Recognizer] = {}


def register_recognizer(recognizer: Recognizer) -> None:
    """Register a recognizer by name."""
    _RECOGNIZER_REGISTRY[recognizer.name] = recognizer


def get_recognizer(name: str) -> Optional[Recognizer]:
    """Get a registered recognizer by name."""
    return _RECOGNIZER_REGISTRY.get(name)


def get_all_recognizers() -> List[Recognizer]:
    """Get all registered recognizers, sorted by priority."""
    return sorted(
        _RECOGNIZER_REGISTRY.values(),
        key=lambda r: r.priority,
        reverse=True
    )


# ============================================================
# Result Merging
# ============================================================

def merge_results(
    results: List[RecognizerResult],
    conflict_strategy: str = "highest_score"
) -> List[RecognizerResult]:
    """
    Merge overlapping recognition results.
    
    Strategies:
    - highest_score: Keep result with highest confidence
    - longest: Keep longest match
    - first: Keep first detected result
    
    Args:
        results: List of results to merge
        conflict_strategy: Strategy for resolving conflicts
        
    Returns:
        Merged list with no overlaps
    """
    if not results:
        return []
    
    # Sort by start position, then by score descending
    sorted_results = sorted(
        results,
        key=lambda r: (r.start, -r.score, -r.length)
    )
    
    merged = []
    
    for result in sorted_results:
        # Check for overlap with existing results
        has_conflict = False
        
        for i, existing in enumerate(merged):
            if result.overlaps(existing):
                has_conflict = True
                
                # Decide which to keep
                if conflict_strategy == "highest_score":
                    if result.score > existing.score:
                        merged[i] = result
                elif conflict_strategy == "longest":
                    if result.length > existing.length:
                        merged[i] = result
                # "first" strategy: keep existing
                break
        
        if not has_conflict:
            merged.append(result)
    
    # Sort final results by position
    return sorted(merged, key=lambda r: r.start)
