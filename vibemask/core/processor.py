"""
Unified Document Processor Interface.
Provides consistent API for processing different document formats.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from .replacer import Replacement


@dataclass
class TextSegment:
    """
    Unified representation of a text segment from any document.
    
    Attributes:
        text: The text content
        location: Location identifier (e.g., "slide:1:shape:2", "cell:A1")
        start_offset: Start offset in combined text (for span mapping)
        end_offset: End offset in combined text
        metadata: Additional format-specific metadata
    """
    text: str
    location: str
    start_offset: int = 0
    end_offset: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class DocumentProcessor(ABC):
    """
    Abstract base class for document processors.
    
    Design principles:
    1. Format-agnostic interface
    2. Lossless processing (preserve formatting)
    3. Consistent text extraction and replacement
    
    Implementations:
    - OOXMLProcessor: docx, xlsx, pptx
    - PDFProcessor: pdf
    - PlainTextProcessor: txt, md
    - StructuredProcessor: json, yaml, csv
    """
    
    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self._segments: List[TextSegment] = []
        self._loaded = False
    
    @property
    @abstractmethod
    def supported_extensions(self) -> set:
        """Return set of supported file extensions (e.g., {'.docx', '.xlsx'})."""
        pass
    
    @abstractmethod
    def load(self) -> None:
        """Load the document into memory."""
        pass
    
    @abstractmethod
    def extract_segments(self) -> List[TextSegment]:
        """
        Extract all text segments from the document.
        
        Returns:
            List of TextSegment objects with text and location info
        """
        pass
    
    def get_combined_text(self) -> str:
        """
        Get all text combined into a single string for detection.
        Also updates segment offsets for span mapping.
        """
        if not self._segments:
            self._segments = self.extract_segments()
        
        combined = []
        current_offset = 0
        
        for segment in self._segments:
            segment.start_offset = current_offset
            combined.append(segment.text)
            current_offset += len(segment.text) + 1  # +1 for newline
            segment.end_offset = current_offset - 1
        
        return '\n'.join(s.text for s in self._segments)
    
    @abstractmethod
    def replace_text(self, replacements: Dict[str, str]) -> int:
        """
        Apply text replacements to the document.
        
        Args:
            replacements: Dict of {original -> masked}
            
        Returns:
            Number of replacements made
        """
        pass

    def replace_spans(self, replacements: List["Replacement"]) -> int:
        """
        Apply position-specific replacements to the document.

        Processors that cannot map combined-text offsets back to their native
        representation should keep the default and let callers fall back to
        replace_text().
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support span replacement")
    
    @abstractmethod
    def save(self, output_path: Optional[Path] = None) -> Path:
        """
        Save the document.
        
        Args:
            output_path: Optional output path. If None, overwrites original.
            
        Returns:
            Path to saved file
        """
        pass
    
    def count_potential_replacements(self, replacements: Dict[str, str]) -> int:
        """Count how many replacements would be made without applying them."""
        count = 0
        # Sort by length descending
        sorted_repls = sorted(replacements.items(), key=lambda x: -len(x[0]))
        
        for segment in self._segments:
            for original, _ in sorted_repls:
                count += segment.text.count(original)
        
        return count


# ============================================================
# Processor Registry
# ============================================================

_PROCESSOR_REGISTRY: Dict[str, type] = {}


def register_processor(processor_class: type) -> type:
    """Decorator to register a processor for its supported extensions."""
    instance = processor_class.__new__(processor_class)
    for ext in processor_class.supported_extensions.fget(instance):
        _PROCESSOR_REGISTRY[ext] = processor_class
    return processor_class


def get_processor(file_path: Path) -> DocumentProcessor:
    """
    Get the appropriate processor for a file.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Appropriate DocumentProcessor instance
        
    Raises:
        ValueError: If no processor found for file type
    """
    ext = Path(file_path).suffix.lower()
    
    if ext not in _PROCESSOR_REGISTRY:
        raise ValueError(f"No processor available for {ext} files")
    
    return _PROCESSOR_REGISTRY[ext](file_path)


def supported_formats() -> List[str]:
    """Return list of all supported file extensions."""
    return list(_PROCESSOR_REGISTRY.keys())


# ============================================================
# Helper Functions
# ============================================================

def sort_replacements(replacements: Dict[str, str]) -> List[tuple]:
    """
    Sort replacements by key length descending.
    This prevents substring conflicts (e.g., "江华通" before "李四").
    """
    return sorted(replacements.items(), key=lambda x: len(x[0]), reverse=True)


def apply_sorted_replacements(text: str, replacements: Dict[str, str]) -> str:
    """Apply replacements in length-sorted order."""
    result = text
    for original, masked in sort_replacements(replacements):
        result = result.replace(original, masked)
    return result
