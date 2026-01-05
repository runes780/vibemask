"""
Unified Document Processing Factory.
Provides a single entry point for processing any supported document format.
"""

from pathlib import Path
from typing import Dict, Optional, List, Any

from .processor import DocumentProcessor, get_processor, supported_formats


# Import all processors to register them
def _register_all_processors():
    """Import all processor modules to trigger registration."""
    try:
        from . import plaintext
    except ImportError:
        pass
    
    try:
        from . import ooxml_adapter
    except ImportError:
        pass
    
    try:
        from . import structured
    except ImportError:
        pass
    
    try:
        from . import pdf
    except ImportError:
        pass

    try:
        from . import legacy_office
    except ImportError:
        pass


# Register on import
_register_all_processors()


def process_file(
    file_path: Path,
    replacements: Dict[str, str],
    output_path: Optional[Path] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Process a file with the appropriate processor.
    
    Args:
        file_path: Path to input file
        replacements: Dict of {original -> masked}
        output_path: Optional output path
        dry_run: If True, only count replacements
        
    Returns:
        Dict with processing results
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        return {
            "success": False,
            "error": f"File not found: {file_path}",
        }
    
    try:
        processor = get_processor(file_path)
    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "supported_formats": supported_formats(),
        }
    
    # Load and extract
    processor.load()
    processor.extract_segments()
    
    # Count or apply replacements
    count = processor.count_potential_replacements(replacements)
    
    if count == 0:
        return {
            "success": True,
            "file": str(file_path),
            "changes": 0,
            "message": "No replacements needed",
        }
    
    if dry_run:
        return {
            "success": True,
            "file": str(file_path),
            "changes": count,
            "dry_run": True,
        }
    
    # Apply replacements
    actual_count = processor.replace_text(replacements)
    
    # Save
    if output_path is None:
        output_path = file_path.with_stem(file_path.stem + "_masked")
    
    saved_path = processor.save(output_path)
    
    return {
        "success": True,
        "file": str(file_path),
        "output": str(saved_path),
        "changes": actual_count,
    }


def extract_text(file_path: Path) -> str:
    """
    Extract all text from a file.
    
    Args:
        file_path: Path to input file
        
    Returns:
        Combined text from the file
    """
    file_path = Path(file_path)
    
    processor = get_processor(file_path)
    processor.load()
    processor.extract_segments()
    
    return processor.get_combined_text()


def get_supported_formats() -> Dict[str, List[str]]:
    """
    Get all supported formats grouped by category.
    
    Returns:
        Dict of {category: [extensions]}
    """
    return {
        "office": [".docx", ".xlsx", ".pptx"],
        "text": [".txt", ".md", ".rst", ".log"],
        "structured": [".json", ".yaml", ".yml", ".csv", ".tsv"],
        "pdf": [".pdf"],
    }


def is_supported(file_path: Path) -> bool:
    """Check if a file format is supported."""
    ext = Path(file_path).suffix.lower()
    return ext in supported_formats()
