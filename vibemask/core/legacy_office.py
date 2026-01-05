"""
Legacy Office Format Processor.
Handles: .doc (Word 97-2003), .xls (Excel 97-2003)

Note: These are binary formats, not OOXML. 
Text replacement in these formats is lossy - we extract text, mask, and save to new format.
"""

import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import shutil

from .processor import DocumentProcessor, TextSegment, register_processor, sort_replacements


# Check for xlrd
try:
    import xlrd
    XLRD_AVAILABLE = True
except ImportError:
    XLRD_AVAILABLE = False


@register_processor
class LegacyExcelProcessor(DocumentProcessor):
    """
    Processor for legacy .xls files (Excel 97-2003).
    
    Note: Cannot preserve formatting when modifying.
    Exports to .xlsx for masked output.
    """
    
    def __init__(self, file_path: Path):
        super().__init__(file_path)
        self._workbook = None
        self._data: List[List[List[str]]] = []  # [sheet][row][cell]
        self._sheet_names: List[str] = []
        
        if not XLRD_AVAILABLE:
            raise ImportError("xlrd is required for .xls processing. Install with: pip install xlrd")
    
    @property
    def supported_extensions(self) -> set:
        return {'.xls'}
    
    def load(self) -> None:
        """Load the .xls file."""
        if self._loaded:
            return
        
        self._workbook = xlrd.open_workbook(self.file_path)
        self._sheet_names = self._workbook.sheet_names()
        
        # Extract all data
        self._data = []
        for sheet_idx in range(self._workbook.nsheets):
            sheet = self._workbook.sheet_by_index(sheet_idx)
            sheet_data = []
            for row_idx in range(sheet.nrows):
                row_data = []
                for col_idx in range(sheet.ncols):
                    cell = sheet.cell(row_idx, col_idx)
                    if cell.ctype == xlrd.XL_CELL_TEXT:
                        row_data.append(cell.value)
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        row_data.append(str(cell.value))
                    else:
                        row_data.append(str(cell.value) if cell.value else "")
                sheet_data.append(row_data)
            self._data.append(sheet_data)
        
        self._loaded = True
    
    def extract_segments(self) -> List[TextSegment]:
        """Extract all cell values as segments."""
        if not self._loaded:
            self.load()
        
        self._segments = []
        current_offset = 0
        
        for sheet_idx, sheet_data in enumerate(self._data):
            for row_idx, row in enumerate(sheet_data):
                for col_idx, cell in enumerate(row):
                    if cell and cell.strip():
                        segment = TextSegment(
                            text=cell,
                            location=f"sheet{sheet_idx}:row{row_idx}:col{col_idx}",
                            start_offset=current_offset,
                            metadata={
                                "sheet": sheet_idx,
                                "row": row_idx,
                                "col": col_idx,
                            }
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
        
        for sheet_idx, sheet_data in enumerate(self._data):
            for row_idx, row in enumerate(sheet_data):
                for col_idx, cell in enumerate(row):
                    new_cell = cell
                    for original, masked in sorted_repls:
                        if original in new_cell:
                            count += new_cell.count(original)
                            new_cell = new_cell.replace(original, masked)
                    self._data[sheet_idx][row_idx][col_idx] = new_cell
        
        return count
    
    def save(self, output_path: Optional[Path] = None) -> Path:
        """
        Save the file. 
        Note: Saves as .xlsx since we can't write .xls format.
        """
        if output_path is None:
            output_path = self.file_path.with_suffix('.xlsx')
        
        output_path = Path(output_path)
        if output_path.suffix == '.xls':
            output_path = output_path.with_suffix('.xlsx')
        
        # Use openpyxl to write xlsx
        try:
            from openpyxl import Workbook
        except ImportError:
            raise ImportError("openpyxl is required to save .xlsx files")
        
        wb = Workbook()
        
        for sheet_idx, sheet_data in enumerate(self._data):
            if sheet_idx == 0:
                ws = wb.active
                ws.title = self._sheet_names[sheet_idx] if sheet_idx < len(self._sheet_names) else f"Sheet{sheet_idx+1}"
            else:
                ws = wb.create_sheet(
                    title=self._sheet_names[sheet_idx] if sheet_idx < len(self._sheet_names) else f"Sheet{sheet_idx+1}"
                )
            
            for row_idx, row in enumerate(sheet_data):
                for col_idx, cell in enumerate(row):
                    ws.cell(row=row_idx+1, column=col_idx+1, value=cell)
        
        wb.save(output_path)
        return output_path


@register_processor
class LegacyWordProcessor(DocumentProcessor):
    """
    Processor for legacy .doc files (Word 97-2003).
    
    Uses textract or antiword for text extraction.
    Note: Cannot preserve formatting - extracts text only.
    """
    
    def __init__(self, file_path: Path):
        super().__init__(file_path)
        self._content: str = ""
        self._extraction_method: str = ""
    
    @property
    def supported_extensions(self) -> set:
        return {'.doc'}
    
    def _extract_with_textract(self) -> str:
        """Try textract for extraction."""
        try:
            import textract
            return textract.process(str(self.file_path)).decode('utf-8')
        except ImportError:
            return None
        except Exception:
            return None
    
    def _extract_with_antiword(self) -> str:
        """Try antiword command line tool."""
        try:
            result = subprocess.run(
                ['antiword', str(self.file_path)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None
    
    def _extract_with_catdoc(self) -> str:
        """Try catdoc command line tool."""
        try:
            result = subprocess.run(
                ['catdoc', '-w', str(self.file_path)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None
    
    def _extract_with_python_docx(self) -> str:
        """Try python-docx (works for some .doc files)."""
        try:
            from docx import Document
            doc = Document(str(self.file_path))
            return '\n'.join([p.text for p in doc.paragraphs])
        except Exception:
            return None
    
    def load(self) -> None:
        """Load the .doc file using available methods."""
        if self._loaded:
            return
        
        # Try multiple extraction methods
        methods = [
            ("python-docx", self._extract_with_python_docx),
            ("antiword", self._extract_with_antiword),
            ("catdoc", self._extract_with_catdoc),
            ("textract", self._extract_with_textract),
        ]
        
        for method_name, method_func in methods:
            content = method_func()
            if content and content.strip():
                self._content = content
                self._extraction_method = method_name
                self._loaded = True
                return
        
        raise RuntimeError(
            f"Could not extract text from {self.file_path}. "
            "Try installing: pip install textract, or brew install antiword"
        )
    
    def extract_segments(self) -> List[TextSegment]:
        """Extract text content as segments."""
        if not self._loaded:
            self.load()
        
        # Split by paragraphs
        paragraphs = self._content.split('\n')
        self._segments = []
        current_offset = 0
        
        for para_idx, para in enumerate(paragraphs):
            if para.strip():
                segment = TextSegment(
                    text=para,
                    location=f"para:{para_idx}",
                    start_offset=current_offset,
                )
                segment.end_offset = current_offset + len(para)
                self._segments.append(segment)
            current_offset += len(para) + 1
        
        return self._segments
    
    def replace_text(self, replacements: Dict[str, str]) -> int:
        """Apply replacements to content."""
        if not self._loaded:
            self.load()
        
        sorted_repls = sort_replacements(replacements)
        count = 0
        
        new_content = self._content
        for original, masked in sorted_repls:
            occurrences = new_content.count(original)
            if occurrences > 0:
                new_content = new_content.replace(original, masked)
                count += occurrences
        
        self._content = new_content
        return count
    
    def save(self, output_path: Optional[Path] = None) -> Path:
        """
        Save the file.
        Note: Saves as .txt since we can't write .doc format reliably.
        Or saves as .docx if openpyxl available.
        """
        if output_path is None:
            output_path = self.file_path.with_suffix('.docx')
        
        output_path = Path(output_path)
        
        # Try to save as docx
        if output_path.suffix in {'.docx', '.doc'}:
            try:
                from docx import Document
                doc = Document()
                for para in self._content.split('\n'):
                    if para.strip():
                        doc.add_paragraph(para)
                
                docx_path = output_path.with_suffix('.docx')
                doc.save(docx_path)
                return docx_path
            except ImportError:
                pass
        
        # Fallback: save as txt
        txt_path = output_path.with_suffix('.txt')
        txt_path.write_text(self._content, encoding='utf-8')
        return txt_path
    
    def get_extraction_method(self) -> str:
        """Return which method was used for extraction."""
        return self._extraction_method
