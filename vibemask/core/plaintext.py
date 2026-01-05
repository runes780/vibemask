"""
Plain Text Document Processor.
Handles: .txt, .md, .rst, .log
"""

from pathlib import Path
from typing import List, Dict, Optional

from .processor import DocumentProcessor, TextSegment, register_processor, sort_replacements


@register_processor
class PlainTextProcessor(DocumentProcessor):
    """
    Processor for plain text files.
    
    Supports: txt, md, rst, log, and other text files.
    """
    
    def __init__(self, file_path: Path):
        super().__init__(file_path)
        self._content: str = ""
        self._encoding: str = "utf-8"
    
    @property
    def supported_extensions(self) -> set:
        return {'.txt', '.md', '.rst', '.log', '.text'}
    
    def load(self) -> None:
        """Load the text file."""
        if self._loaded:
            return
        
        # Try different encodings
        for encoding in ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'latin-1']:
            try:
                self._content = self.file_path.read_text(encoding=encoding)
                self._encoding = encoding
                self._loaded = True
                return
            except UnicodeDecodeError:
                continue
        
        raise ValueError(f"Could not decode file: {self.file_path}")
    
    def extract_segments(self) -> List[TextSegment]:
        """Extract text content as a single segment."""
        if not self._loaded:
            self.load()
        
        self._segments = [TextSegment(
            text=self._content,
            location="file",
            start_offset=0,
            end_offset=len(self._content),
        )]
        
        return self._segments
    
    def replace_text(self, replacements: Dict[str, str]) -> int:
        """Apply replacements to the content."""
        if not self._loaded:
            self.load()
        
        count = 0
        new_content = self._content
        
        for original, masked in sort_replacements(replacements):
            occurrences = new_content.count(original)
            if occurrences > 0:
                new_content = new_content.replace(original, masked)
                count += occurrences
        
        self._content = new_content
        
        # Update segments
        if self._segments:
            self._segments[0].text = new_content
        
        return count
    
    def save(self, output_path: Optional[Path] = None) -> Path:
        """Save the text file."""
        if output_path is None:
            output_path = self.file_path
        
        output_path = Path(output_path)
        output_path.write_text(self._content, encoding=self._encoding)
        
        return output_path


@register_processor  
class MarkdownProcessor(PlainTextProcessor):
    """
    Processor for Markdown files with special handling.
    
    Features:
    - Skip code blocks (``` ... ```)
    - Skip inline code (`...`)
    - Handle frontmatter
    """
    
    @property
    def supported_extensions(self) -> set:
        return {'.md', '.markdown'}
    
    def extract_segments(self) -> List[TextSegment]:
        """Extract text, skipping code blocks."""
        if not self._loaded:
            self.load()
        
        import re
        
        # Remove code blocks for detection (but keep them in content)
        code_block_pattern = r'```[\s\S]*?```'
        inline_code_pattern = r'`[^`]+`'
        
        # Get positions to skip
        skip_ranges = []
        for match in re.finditer(code_block_pattern, self._content):
            skip_ranges.append((match.start(), match.end()))
        for match in re.finditer(inline_code_pattern, self._content):
            skip_ranges.append((match.start(), match.end()))
        
        # Extract non-code segments
        self._segments = []
        current_pos = 0
        
        for start, end in sorted(skip_ranges):
            if current_pos < start:
                text = self._content[current_pos:start]
                if text.strip():
                    self._segments.append(TextSegment(
                        text=text,
                        location=f"text:{current_pos}-{start}",
                        start_offset=current_pos,
                        end_offset=start,
                    ))
            current_pos = end
        
        # Add remaining text
        if current_pos < len(self._content):
            text = self._content[current_pos:]
            if text.strip():
                self._segments.append(TextSegment(
                    text=text,
                    location=f"text:{current_pos}-{len(self._content)}",
                    start_offset=current_pos,
                    end_offset=len(self._content),
                ))
        
        # If no code blocks, use full content
        if not self._segments:
            self._segments = [TextSegment(
                text=self._content,
                location="file",
                start_offset=0,
                end_offset=len(self._content),
            )]
        
        return self._segments
