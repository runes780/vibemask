"""
Office Format Converter.
Uses LibreOffice or Microsoft Office for format conversion.
Enables lossless processing of legacy formats (.doc, .xls, .ppt).
"""

import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple
import os


def find_libreoffice(explicit_path: Optional[str] = None) -> Optional[str]:
    """Find LibreOffice `soffice` binary.

    Args:
        explicit_path: Optional explicit path (or command name) to use.
    """
    if explicit_path:
        expanded = os.path.expanduser(str(explicit_path))
        candidate = Path(expanded)
        if candidate.exists():
            return str(candidate)

        found = shutil.which(expanded)
        if found:
            return found
        return None

    candidates = [
        # macOS
        '/Applications/LibreOffice.app/Contents/MacOS/soffice',
        # Linux
        '/usr/bin/soffice',
        '/usr/bin/libreoffice',
        # Windows (common paths)
        'C:/Program Files/LibreOffice/program/soffice.exe',
        'C:/Program Files (x86)/LibreOffice/program/soffice.exe',
    ]
    
    for path in candidates:
        if os.path.exists(path):
            return path
    
    # Try PATH
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    
    return None


def is_libreoffice_available(soffice_path: Optional[str] = None) -> bool:
    """Check if LibreOffice is available."""
    return find_libreoffice(soffice_path) is not None


def convert_to_ooxml(
    input_path: Path,
    output_dir: Optional[Path] = None,
    soffice_path: Optional[str] = None,
) -> Path:
    """
    Convert legacy Office format to OOXML format.
    
    Conversions:
    - .doc -> .docx
    - .xls -> .xlsx
    - .ppt -> .pptx
    
    Args:
        input_path: Path to legacy format file
        output_dir: Optional output directory (default: same as input)
        
    Returns:
        Path to converted file
        
    Raises:
        RuntimeError: If LibreOffice is not available or conversion fails
    """
    soffice = find_libreoffice(soffice_path)
    if not soffice:
        raise RuntimeError(
            "LibreOffice is not installed. "
            "Install from: https://www.libreoffice.org/download/"
        )
    
    input_path = Path(input_path)
    if output_dir is None:
        output_dir = input_path.parent
    output_dir = Path(output_dir)
    
    # Determine output format
    ext_map = {
        '.doc': 'docx',
        '.xls': 'xlsx', 
        '.ppt': 'pptx',
    }
    
    input_ext = input_path.suffix.lower()
    if input_ext not in ext_map:
        raise ValueError(f"Unsupported input format: {input_ext}")
    
    output_format = ext_map[input_ext]
    
    # Run LibreOffice conversion
    try:
        result = subprocess.run(
            [
                soffice,
                '--headless',
                '--convert-to', output_format,
                '--outdir', str(output_dir),
                str(input_path)
            ],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Conversion failed: {result.stderr}")
        
    except subprocess.TimeoutExpired:
        raise RuntimeError("Conversion timed out after 120 seconds")
    
    # Find output file
    output_path = output_dir / f"{input_path.stem}.{output_format}"
    
    if not output_path.exists():
        raise RuntimeError(f"Conversion output not found: {output_path}")
    
    return output_path


def convert_back_to_legacy(
    input_path: Path,
    target_format: str,
    output_path: Optional[Path] = None,
    soffice_path: Optional[str] = None,
) -> Path:
    """
    Convert OOXML back to legacy format (if needed).
    
    Note: This may lose some formatting.
    
    Args:
        input_path: Path to OOXML file
        target_format: Target format ('doc', 'xls', 'ppt')
        output_path: Optional output path
        
    Returns:
        Path to converted file
    """
    soffice = find_libreoffice(soffice_path)
    if not soffice:
        raise RuntimeError("LibreOffice is not installed")
    
    input_path = Path(input_path)
    output_dir = output_path.parent if output_path else input_path.parent
    
    # Map to LibreOffice format names
    format_map = {
        'doc': 'doc',  # MS Word 97-2003
        'xls': 'xls',  # MS Excel 97-2003
        'ppt': 'ppt',  # MS PowerPoint 97-2003
    }
    
    if target_format not in format_map:
        raise ValueError(f"Unsupported target format: {target_format}")
    
    result = subprocess.run(
        [
            soffice,
            '--headless',
            '--convert-to', format_map[target_format],
            '--outdir', str(output_dir),
            str(input_path)
        ],
        capture_output=True,
        text=True,
        timeout=120
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"Conversion failed: {result.stderr}")
    
    converted = output_dir / f"{input_path.stem}.{target_format}"
    
    if output_path and converted != output_path:
        shutil.move(str(converted), str(output_path))
        return output_path
    
    return converted


class LegacyFormatProcessor:
    """
    Process legacy Office formats by converting to OOXML first.
    This preserves formatting during masking/restoration.
    
    Workflow:
    1. Convert .doc/.xls/.ppt to .docx/.xlsx/.pptx
    2. Process with OOXML processor (lossless)
    3. Optionally convert back to legacy format
    """
    
    def __init__(
        self,
        file_path: Path,
        convert_to_ooxml: bool = True,
        soffice_path: Optional[str] = None,
    ):
        self.original_path = Path(file_path)
        self.original_format = self.original_path.suffix.lower()
        self.converted_path: Optional[Path] = None
        self._temp_dir: Optional[Path] = None
        self._processor = None
        self._soffice_path = soffice_path
        
        if convert_to_ooxml and self.original_format in {'.doc', '.xls', '.ppt'}:
            self._convert()
    
    def _convert(self):
        """Convert to OOXML format."""
        import tempfile
        self._temp_dir = Path(tempfile.mkdtemp(prefix='vibemask_'))
        self.converted_path = convert_to_ooxml(
            self.original_path, self._temp_dir, soffice_path=self._soffice_path
        )
    
    def get_processor(self):
        """Get the OOXML processor for the converted file."""
        if self._processor is None:
            from .ooxml import OOXMLProcessor
            target = self.converted_path or self.original_path
            self._processor = OOXMLProcessor(target)
        return self._processor
    
    def save(self, output_path: Path, keep_legacy_format: bool = False) -> Path:
        """
        Save the processed file.
        
        Args:
            output_path: Output path
            keep_legacy_format: If True, convert back to original legacy format
            
        Returns:
            Path to saved file
        """
        processor = self.get_processor()
        
        # Determine OOXML output path
        ooxml_ext = {'.doc': '.docx', '.xls': '.xlsx', '.ppt': '.pptx'}
        ooxml_output = output_path
        if output_path.suffix in ooxml_ext:
            ooxml_output = output_path.with_suffix(ooxml_ext[output_path.suffix])
        
        processor.save(ooxml_output)
        
        if keep_legacy_format and self.original_format in {'.doc', '.xls', '.ppt'}:
            legacy_format = self.original_format[1:]  # Remove dot
            return convert_back_to_legacy(
                ooxml_output,
                legacy_format,
                output_path,
                soffice_path=self._soffice_path,
            )
        
        return ooxml_output
    
    def cleanup(self):
        """Clean up temporary files."""
        if self._temp_dir and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir)
    
    def __del__(self):
        self.cleanup()
