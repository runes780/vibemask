"""
OOXML Document Processor Adapter.
Wraps OOXMLProcessor to implement the unified DocumentProcessor interface.
"""

from pathlib import Path
from typing import List, Dict, Optional

from .processor import DocumentProcessor, TextSegment, register_processor
from .ooxml import OOXMLProcessor as BaseOOXMLProcessor


@register_processor
class OOXMLDocumentProcessor(DocumentProcessor):
    """
    OOXML Document Processor implementing unified interface.
    
    Supports: .docx, .xlsx, .pptx
    Uses lxml for lossless XML-level text replacement.
    """
    
    def __init__(self, file_path: Path):
        super().__init__(file_path)
        self._processor: Optional[BaseOOXMLProcessor] = None
    
    @property
    def supported_extensions(self) -> set:
        return {'.docx', '.xlsx', '.pptx'}
    
    def load(self) -> None:
        """Load the OOXML document."""
        if self._loaded:
            return
        
        self._processor = BaseOOXMLProcessor(self.file_path)
        self._loaded = True
    
    def extract_segments(self) -> List[TextSegment]:
        """Extract all text segments from the document."""
        if not self._loaded:
            self.load()
        
        # Get text nodes from base processor
        text_nodes = self._processor.extract_text_nodes()
        
        # Convert to TextSegment format.
        # Group nodes by logical container so detection sees natural text (no newline between runs).
        self._segments = []

        def find_container(el: any):
            if el is None:
                return None
            file_type = getattr(self._processor, "file_type", self.file_path.suffix.lower())
            # Container tags differ by format.
            if file_type == ".docx":
                container_tags = {f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}p"}
            elif file_type == ".pptx":
                container_tags = {f"{{http://schemas.openxmlformats.org/drawingml/2006/main}}p"}
            elif file_type == ".xlsx":
                container_tags = {
                    f"{{http://schemas.openxmlformats.org/spreadsheetml/2006/main}}si",
                    f"{{http://schemas.openxmlformats.org/spreadsheetml/2006/main}}is",
                }
            else:
                container_tags = set()

            for anc in el.iterancestors():
                if anc.tag in container_tags:
                    return anc
            return el.getparent()

        current_offset = 0
        current_key = None
        buffer_parts: List[str] = []

        def flush(location: str):
            nonlocal current_offset, buffer_parts
            if not buffer_parts:
                return
            combined = "".join(buffer_parts)
            segment = TextSegment(
                text=combined,
                location=location,
                start_offset=current_offset,
            )
            segment.end_offset = current_offset + len(combined)
            self._segments.append(segment)
            current_offset = segment.end_offset + 1
            buffer_parts = []

        for node in text_nodes:
            if not node.text or node.element is None:
                continue
            container = find_container(node.element)
            key = (node.xml_part, container)
            if current_key is None:
                current_key = key
            if key != current_key:
                flush(location=f"{current_key[0]}:{len(self._segments)}")
                current_key = key
            buffer_parts.append(node.text)

        if current_key is not None:
            flush(location=f"{current_key[0]}:{len(self._segments)}")
        
        return self._segments
    
    def replace_text(self, replacements: Dict[str, str]) -> int:
        """Apply replacements using OOXML processor."""
        if not self._loaded:
            self.load()
        
        if not self._segments:
            self.extract_segments()
        
        # Count before applying
        count = self._processor.count_replacements(replacements)
        
        # Apply replacements
        self._processor.replace_in_place(replacements)
        
        return count
    
    def save(self, output_path: Optional[Path] = None) -> Path:
        """Save the document."""
        if output_path is None:
            output_path = self.file_path
        
        output_path = Path(output_path)
        self._processor.save(output_path)
        
        return output_path
