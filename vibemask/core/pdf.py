"""
PDF Document Processor.
Handles: PDF files using PyMuPDF (optional dependency).
"""

from pathlib import Path
from typing import List, Dict, Optional

from .processor import DocumentProcessor, TextSegment, register_processor, sort_replacements


# Check if PyMuPDF is available
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False


@register_processor
class PDFProcessor(DocumentProcessor):
    """
    Processor for PDF files.
    
    Features:
    - Extracts text from all pages
    - Supports text replacement via redaction + insertion
    - Preserves document structure (images, layout)
    
    Note: Requires PyMuPDF (pip install pymupdf).
    PDF text replacement is approximate - may not preserve exact formatting.
    """
    
    def __init__(self, file_path: Path):
        super().__init__(file_path)
        self._doc = None
        self._text_blocks: List[Dict] = []
        
        if not PYMUPDF_AVAILABLE:
            raise ImportError(
                "PyMuPDF is required for PDF processing. "
                "Install with: pip install pymupdf"
            )
    
    @property
    def supported_extensions(self) -> set:
        return {'.pdf'}
    
    def load(self) -> None:
        """Load the PDF file."""
        if self._loaded:
            return
        
        self._doc = fitz.open(self.file_path)
        self._loaded = True
    
    def extract_segments(self) -> List[TextSegment]:
        """Extract all text blocks from the PDF."""
        if not self._loaded:
            self.load()
        
        self._segments = []
        self._text_blocks = []
        current_offset = 0
        
        for page_num in range(len(self._doc)):
            page = self._doc[page_num]
            
            # Get text blocks with position info
            blocks = page.get_text("dict")["blocks"]
            
            for block_idx, block in enumerate(blocks):
                if block.get("type") == 0:  # Text block
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            text = span.get("text", "")
                            if text.strip():
                                # Store block info for replacement
                                block_info = {
                                    "page": page_num,
                                    "bbox": span.get("bbox"),
                                    "text": text,
                                    "font": span.get("font"),
                                    "size": span.get("size"),
                                    "color": span.get("color"),
                                }
                                self._text_blocks.append(block_info)
                                
                                segment = TextSegment(
                                    text=text,
                                    location=f"pdf:page{page_num}:block{block_idx}",
                                    start_offset=current_offset,
                                    metadata=block_info
                                )
                                segment.end_offset = current_offset + len(text)
                                self._segments.append(segment)
                                current_offset = segment.end_offset + 1
        
        return self._segments
    
    def replace_text(self, replacements: Dict[str, str]) -> int:
        """
        Replace text in the PDF.
        
        Note: PDF text replacement is done via:
        1. Redact original text (white rectangle)
        2. Insert new text at same location
        
        This may not preserve exact formatting.
        """
        if not self._loaded:
            self.load()
        
        if not self._segments:
            self.extract_segments()
        
        sorted_repls = sort_replacements(replacements)
        count = 0
        
        # Group replacements by page
        page_replacements: Dict[int, List[Dict]] = {}
        
        for block_info in self._text_blocks:
            page_num = block_info["page"]
            original_text = block_info["text"]
            new_text = original_text
            
            for orig, masked in sorted_repls:
                if orig in new_text:
                    count += new_text.count(orig)
                    new_text = new_text.replace(orig, masked)
            
            if new_text != original_text:
                if page_num not in page_replacements:
                    page_replacements[page_num] = []
                
                page_replacements[page_num].append({
                    "bbox": block_info["bbox"],
                    "original": original_text,
                    "new": new_text,
                    "font": block_info.get("font", "helv"),
                    "size": block_info.get("size", 11),
                })
        
        # Apply replacements page by page
        for page_num, repls in page_replacements.items():
            page = self._doc[page_num]
            
            for repl in repls:
                bbox = fitz.Rect(repl["bbox"])
                
                # Method 1: Add redaction annotation (cleaner but requires apply)
                page.add_redact_annot(bbox, fill=(1, 1, 1))  # White fill
            
            # Apply all redactions on this page
            page.apply_redactions()
            
            # Insert new text
            for repl in repls:
                bbox = fitz.Rect(repl["bbox"])
                
                # Insert text at the same position
                page.insert_text(
                    (bbox.x0, bbox.y1 - 2),  # Slightly above bottom
                    repl["new"],
                    fontsize=repl["size"],
                    fontname="helv",  # Use Helvetica as fallback
                )
        
        return count
    
    def save(self, output_path: Optional[Path] = None) -> Path:
        """Save the PDF file."""
        if output_path is None:
            output_path = self.file_path
        
        output_path = Path(output_path)
        
        # Save with garbage collection for smaller file
        self._doc.save(
            output_path,
            garbage=4,
            deflate=True,
            clean=True
        )
        
        return output_path
    
    def close(self):
        """Close the PDF document."""
        if self._doc:
            self._doc.close()
            self._doc = None
    
    def __del__(self):
        """Cleanup on deletion."""
        if hasattr(self, '_doc'):
            self.close()


# Fallback processor when PyMuPDF is not available
class PDFTextExtractor:
    """
    Simple PDF text extractor for read-only operations.
    Does not support text replacement.
    """
    
    @staticmethod
    def is_available() -> bool:
        return PYMUPDF_AVAILABLE
    
    @staticmethod
    def extract_text(file_path: Path) -> str:
        """Extract all text from a PDF file."""
        if not PYMUPDF_AVAILABLE:
            raise ImportError("PyMuPDF required for PDF text extraction")
        
        text_parts = []
        doc = fitz.open(file_path)
        
        for page in doc:
            text_parts.append(page.get_text())
        
        doc.close()
        return "\n".join(text_parts)
