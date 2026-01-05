"""
Structured Data Processor.
Handles: JSON, YAML, CSV files.
"""

import json
import csv
from pathlib import Path
from typing import List, Dict, Optional, Any
from io import StringIO

from .processor import DocumentProcessor, TextSegment, register_processor, sort_replacements


@register_processor
class JSONProcessor(DocumentProcessor):
    """
    Processor for JSON files.
    
    Features:
    - Preserves structure and formatting
    - Extracts all string values for detection
    - Supports nested objects and arrays
    """
    
    def __init__(self, file_path: Path):
        super().__init__(file_path)
        self._data: Any = None
        self._raw_content: str = ""
        self._indent: int = 2
    
    @property
    def supported_extensions(self) -> set:
        return {'.json'}
    
    def load(self) -> None:
        """Load the JSON file."""
        if self._loaded:
            return
        
        self._raw_content = self.file_path.read_text(encoding='utf-8')
        self._data = json.loads(self._raw_content)
        
        # Detect indentation
        lines = self._raw_content.split('\n')
        for line in lines:
            stripped = line.lstrip()
            if stripped and stripped != line:
                self._indent = len(line) - len(stripped)
                break
        
        self._loaded = True
    
    def _extract_strings(self, obj: Any, path: str = "") -> List[tuple]:
        """Recursively extract all string values with their paths."""
        results = []
        
        if isinstance(obj, str):
            if obj.strip():
                results.append((path, obj))
        elif isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                results.extend(self._extract_strings(value, new_path))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                new_path = f"{path}[{i}]"
                results.extend(self._extract_strings(item, new_path))
        
        return results
    
    def extract_segments(self) -> List[TextSegment]:
        """Extract all string values as segments."""
        if not self._loaded:
            self.load()
        
        strings = self._extract_strings(self._data)
        self._segments = []
        
        current_offset = 0
        for path, text in strings:
            segment = TextSegment(
                text=text,
                location=f"json:{path}",
                start_offset=current_offset,
                metadata={"path": path}
            )
            segment.end_offset = current_offset + len(text)
            self._segments.append(segment)
            current_offset = segment.end_offset + 1
        
        return self._segments
    
    def _replace_in_object(self, obj: Any, replacements: List[tuple]) -> Any:
        """Recursively replace strings in object."""
        if isinstance(obj, str):
            result = obj
            for original, masked in replacements:
                result = result.replace(original, masked)
            return result
        elif isinstance(obj, dict):
            return {k: self._replace_in_object(v, replacements) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._replace_in_object(item, replacements) for item in obj]
        else:
            return obj
    
    def replace_text(self, replacements: Dict[str, str]) -> int:
        """Apply replacements to all string values."""
        if not self._loaded:
            self.load()
        
        # Count occurrences
        count = 0
        old_json = json.dumps(self._data, ensure_ascii=False)
        for original in replacements:
            count += old_json.count(original)
        
        # Apply replacements
        sorted_repls = sort_replacements(replacements)
        self._data = self._replace_in_object(self._data, sorted_repls)
        
        return count
    
    def save(self, output_path: Optional[Path] = None) -> Path:
        """Save the JSON file."""
        if output_path is None:
            output_path = self.file_path
        
        output_path = Path(output_path)
        content = json.dumps(self._data, ensure_ascii=False, indent=self._indent)
        output_path.write_text(content, encoding='utf-8')
        
        return output_path


@register_processor
class YAMLProcessor(DocumentProcessor):
    """
    Processor for YAML files.
    
    Note: Requires PyYAML (optional dependency).
    Falls back to JSON-like processing if unavailable.
    """
    
    def __init__(self, file_path: Path):
        super().__init__(file_path)
        self._data: Any = None
        self._raw_content: str = ""
        self._yaml_available: bool = False
        
        try:
            import yaml
            self._yaml = yaml
            self._yaml_available = True
        except ImportError:
            pass
    
    @property
    def supported_extensions(self) -> set:
        return {'.yaml', '.yml'}
    
    def load(self) -> None:
        """Load the YAML file."""
        if self._loaded:
            return
        
        self._raw_content = self.file_path.read_text(encoding='utf-8')
        
        if self._yaml_available:
            self._data = self._yaml.safe_load(self._raw_content)
        else:
            # Fallback: treat as plain text
            self._data = self._raw_content
        
        self._loaded = True
    
    def extract_segments(self) -> List[TextSegment]:
        """Extract text segments."""
        if not self._loaded:
            self.load()
        
        if self._yaml_available and isinstance(self._data, (dict, list)):
            # Use JSON processor logic for structured data
            return self._extract_structured()
        else:
            # Fallback: single text segment
            self._segments = [TextSegment(
                text=self._raw_content,
                location="yaml:file",
                start_offset=0,
                end_offset=len(self._raw_content),
            )]
            return self._segments
    
    def _extract_strings(self, obj: Any, path: str = "") -> List[tuple]:
        """Recursively extract strings."""
        results = []
        if isinstance(obj, str) and obj.strip():
            results.append((path, obj))
        elif isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else str(key)
                results.extend(self._extract_strings(value, new_path))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                results.extend(self._extract_strings(item, f"{path}[{i}]"))
        return results
    
    def _extract_structured(self) -> List[TextSegment]:
        """Extract from structured YAML."""
        strings = self._extract_strings(self._data)
        self._segments = []
        
        current_offset = 0
        for path, text in strings:
            segment = TextSegment(
                text=text,
                location=f"yaml:{path}",
                start_offset=current_offset,
            )
            segment.end_offset = current_offset + len(text)
            self._segments.append(segment)
            current_offset = segment.end_offset + 1
        
        return self._segments
    
    def replace_text(self, replacements: Dict[str, str]) -> int:
        """Apply replacements."""
        if not self._loaded:
            self.load()
        
        sorted_repls = sort_replacements(replacements)
        count = 0
        
        if self._yaml_available and isinstance(self._data, (dict, list)):
            old_yaml = self._yaml.dump(self._data, allow_unicode=True)
            for original in replacements:
                count += old_yaml.count(original)
            self._data = self._replace_in_object(self._data, sorted_repls)
        else:
            for original in replacements:
                count += self._raw_content.count(original)
            for original, masked in sorted_repls:
                self._raw_content = self._raw_content.replace(original, masked)
        
        return count
    
    def _replace_in_object(self, obj: Any, replacements: List[tuple]) -> Any:
        """Recursively replace strings."""
        if isinstance(obj, str):
            result = obj
            for original, masked in replacements:
                result = result.replace(original, masked)
            return result
        elif isinstance(obj, dict):
            return {k: self._replace_in_object(v, replacements) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._replace_in_object(item, replacements) for item in obj]
        return obj
    
    def save(self, output_path: Optional[Path] = None) -> Path:
        """Save the YAML file."""
        if output_path is None:
            output_path = self.file_path
        
        output_path = Path(output_path)
        
        if self._yaml_available and isinstance(self._data, (dict, list)):
            content = self._yaml.dump(
                self._data, 
                allow_unicode=True, 
                default_flow_style=False,
                sort_keys=False
            )
        else:
            content = self._raw_content
        
        output_path.write_text(content, encoding='utf-8')
        return output_path


@register_processor
class CSVProcessor(DocumentProcessor):
    """
    Processor for CSV files.
    
    Features:
    - Preserves structure (headers, rows)
    - Extracts all cell values for detection
    - Maintains original delimiter and quoting
    """
    
    def __init__(self, file_path: Path):
        super().__init__(file_path)
        self._rows: List[List[str]] = []
        self._dialect: Any = None
        self._has_header: bool = True
    
    @property
    def supported_extensions(self) -> set:
        return {'.csv', '.tsv'}
    
    def load(self) -> None:
        """Load the CSV file."""
        if self._loaded:
            return
        
        content = self.file_path.read_text(encoding='utf-8')
        
        # Detect dialect
        try:
            sniffer = csv.Sniffer()
            sample = content[:4096]
            self._dialect = sniffer.sniff(sample)
            self._has_header = sniffer.has_header(sample)
        except csv.Error:
            self._dialect = csv.excel
            self._has_header = True
        
        # Parse CSV
        reader = csv.reader(StringIO(content), dialect=self._dialect)
        self._rows = list(reader)
        
        self._loaded = True
    
    def extract_segments(self) -> List[TextSegment]:
        """Extract all cell values as segments."""
        if not self._loaded:
            self.load()
        
        self._segments = []
        current_offset = 0
        
        for row_idx, row in enumerate(self._rows):
            for col_idx, cell in enumerate(row):
                if cell and cell.strip():
                    segment = TextSegment(
                        text=cell,
                        location=f"csv:row{row_idx}:col{col_idx}",
                        start_offset=current_offset,
                        metadata={"row": row_idx, "col": col_idx}
                    )
                    segment.end_offset = current_offset + len(cell)
                    self._segments.append(segment)
                    current_offset = segment.end_offset + 1
        
        return self._segments
    
    def replace_text(self, replacements: Dict[str, str]) -> int:
        """Apply replacements to all cells."""
        if not self._loaded:
            self.load()
        
        sorted_repls = sort_replacements(replacements)
        count = 0
        
        for row_idx, row in enumerate(self._rows):
            for col_idx, cell in enumerate(row):
                new_cell = cell
                for original, masked in sorted_repls:
                    if original in new_cell:
                        count += new_cell.count(original)
                        new_cell = new_cell.replace(original, masked)
                self._rows[row_idx][col_idx] = new_cell
        
        return count
    
    def save(self, output_path: Optional[Path] = None) -> Path:
        """Save the CSV file."""
        if output_path is None:
            output_path = self.file_path
        
        output_path = Path(output_path)
        
        output = StringIO()
        writer = csv.writer(output, dialect=self._dialect)
        writer.writerows(self._rows)
        
        output_path.write_text(output.getvalue(), encoding='utf-8')
        return output_path
