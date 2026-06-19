"""
OOXML Lossless Processor for VibeMask.
Directly operates on Office Open XML structure to preserve formatting.

Supports: DOCX, XLSX, PPTX
"""

import zipfile
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

try:
    from lxml import etree
except ImportError:
    raise ImportError("lxml is required for OOXML processing. Install with: pip install lxml")


# ============================================================
# XML Namespaces for Office Open XML
# ============================================================

NAMESPACES = {
    # Word (DOCX)
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
    
    # Excel (XLSX)
    'spreadsheet': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    
    # PowerPoint (PPTX)
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    
    # Common
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}


@dataclass
class TextNode:
    """Represents a text node in an Office document."""
    xml_part: str        # e.g., 'word/document.xml'
    xpath: str           # XPath to the node
    text: str            # Text content
    element: any = None  # lxml element reference


class OOXMLProcessor:
    """
    Process Office Open XML files with lossless text replacement.
    
    Key advantage over python-docx/openpyxl:
    - Directly operates on XML nodes
    - Preserves all formatting, styles, comments, etc.
    - Handles cross-run text properly
    """
    
    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.file_type = self.file_path.suffix.lower()
        self._zip_data: Dict[str, bytes] = {}
        self._xml_trees: Dict[str, etree._ElementTree] = {}
        self._text_nodes: List[TextNode] = []
        
        if self.file_type not in {'.docx', '.xlsx', '.pptx'}:
            raise ValueError(f"Unsupported file type: {self.file_type}")
        
        self._load()
    
    def _load(self):
        """Load the OOXML file into memory."""
        with zipfile.ZipFile(self.file_path, 'r') as zf:
            for name in zf.namelist():
                self._zip_data[name] = zf.read(name)
    
    def _get_xml_tree(self, part_name: str) -> Optional[etree._ElementTree]:
        """Get or parse an XML tree from a part."""
        if part_name not in self._zip_data:
            return None
        
        if part_name not in self._xml_trees:
            try:
                parser = etree.XMLParser(remove_blank_text=False)
                self._xml_trees[part_name] = etree.fromstring(
                    self._zip_data[part_name], parser
                )
            except etree.XMLSyntaxError:
                return None
        
        return self._xml_trees[part_name]
    
    def _get_text_parts(self) -> List[str]:
        """Get list of XML parts that may contain text."""
        if self.file_type == '.docx':
            return [
                'word/document.xml',
                'word/header1.xml', 'word/header2.xml', 'word/header3.xml',
                'word/footer1.xml', 'word/footer2.xml', 'word/footer3.xml',
                'word/comments.xml',
                'word/footnotes.xml',
                'word/endnotes.xml',
            ]
        elif self.file_type == '.xlsx':
            parts = []
            # 1. sharedStrings.xml - shared string table
            if 'xl/sharedStrings.xml' in self._zip_data:
                parts.append('xl/sharedStrings.xml')
            # 2. All worksheets - for inline strings (<is><t>)
            for name in self._zip_data.keys():
                if name.startswith('xl/worksheets/') and name.endswith('.xml'):
                    parts.append(name)
                # Also check for comments
                if name.startswith('xl/comments') and name.endswith('.xml'):
                    parts.append(name)
            return parts
        elif self.file_type == '.pptx':
            parts = []
            for name in self._zip_data.keys():
                if any(pattern in name for pattern in [
                    'ppt/slides/slide',
                    'ppt/notesSlides/',
                    'ppt/slideMasters/',
                    'ppt/slideLayouts/',
                ]) and name.endswith('.xml'):
                    parts.append(name)
            return parts
        return []
    
    def _get_text_xpath(self) -> str:
        """Get XPath for text nodes based on file type."""
        if self.file_type == '.docx':
            return '//w:t'
        elif self.file_type == '.xlsx':
            return '//spreadsheet:t'
        elif self.file_type == '.pptx':
            return '//a:t'
        return ''
    
    def extract_text_nodes(self) -> List[TextNode]:
        """Extract all text nodes from the document."""
        self._text_nodes = []
        xpath = self._get_text_xpath()
        parts = self._get_text_parts()
        
        for part_name in parts:
            root = self._get_xml_tree(part_name)
            if root is None:
                continue
            
            # Use appropriate namespace
            try:
                nodes = root.xpath(xpath, namespaces=NAMESPACES)
                for i, node in enumerate(nodes):
                    if node.text:
                        self._text_nodes.append(TextNode(
                            xml_part=part_name,
                            xpath=f"{xpath}[{i+1}]",
                            text=node.text,
                            element=node
                        ))
            except etree.XPathEvalError:
                # Try without namespace for fallback
                pass
        
        return self._text_nodes
    
    def get_combined_text(self) -> str:
        """Get all text combined for detection."""
        if not self._text_nodes:
            self.extract_text_nodes()

        # Join by logical containers (paragraphs / cells) for better detection accuracy.
        parts: List[str] = []
        for group in self._iter_text_node_groups():
            combined = "".join((n.element.text or "") if n.element is not None else "" for n in group)
            if combined:
                parts.append(combined)
        return "\n".join(parts)
    
    def replace_in_place(self, replacements: Dict[str, str]):
        """
        Replace text in all XML parts.
        
        IMPORTANT: Replacements are sorted by key length (longest first)
        to prevent substring conflicts.
        """
        if not replacements:
            return

        if not self._text_nodes:
            self.extract_text_nodes()

        # Sort by length descending to prevent substring issues
        sorted_replacements = sorted(replacements.items(), key=lambda x: len(x[0]), reverse=True)

        # Track which parts have been modified
        modified_parts = set()

        # Replace within structural containers so replacements can span multiple <t> nodes
        for group in self._iter_text_node_groups():
            before_texts = [(n.element.text or "") if n.element is not None else "" for n in group]
            combined_before = "".join(before_texts)
            if not combined_before:
                continue

            combined_after = combined_before
            for original, masked in sorted_replacements:
                if original in combined_after:
                    combined_after = combined_after.replace(original, masked)

            if combined_after == combined_before:
                continue

            # Redistribute back to nodes. Prefer keeping each node length stable to preserve formatting.
            lengths = [len(t) for t in before_texts]
            target_total = sum(lengths)
            if target_total <= 0:
                continue

            # If length changed (should be rare with length-preserving masking), keep all but last stable.
            pos = 0
            for i, (node, seg_len) in enumerate(zip(group, lengths)):
                if node.element is None:
                    pos += seg_len
                    continue

                if i < len(group) - 1:
                    node.element.text = combined_after[pos:pos + seg_len]
                    pos += seg_len
                else:
                    node.element.text = combined_after[pos:]

                modified_parts.add(node.xml_part)

        # Update zip data for modified parts
        for part_name in modified_parts:
            if part_name in self._xml_trees:
                self._zip_data[part_name] = etree.tostring(
                    self._xml_trees[part_name],
                    xml_declaration=True,
                    encoding='UTF-8',
                    standalone=True
                )

    def save(self, output_path: Optional[Path] = None):
        """Save the modified document."""
        if output_path is None:
            output_path = self.file_path
        
        output_path = Path(output_path)
        
        # Write to a temporary file first, then move
        temp_path = output_path.with_suffix('.tmp')
        
        with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, data in self._zip_data.items():
                zf.writestr(name, data)
        
        # Move temp to final destination
        shutil.move(str(temp_path), str(output_path))
    
    def count_replacements(self, replacements: Dict[str, str]) -> int:
        """Count how many replacements would be made."""
        if not replacements:
            return 0

        if not self._text_nodes:
            self.extract_text_nodes()

        count = 0
        sorted_replacements = sorted(replacements.items(), key=lambda x: len(x[0]), reverse=True)

        for group in self._iter_text_node_groups():
            combined = "".join((n.element.text or "") if n.element is not None else "" for n in group)
            if not combined:
                continue
            for original, _ in sorted_replacements:
                count += combined.count(original)

        return count

    def _iter_text_node_groups(self) -> List[List[TextNode]]:
        """
        Group text nodes by their logical container (paragraph / cell / string item).

        This prevents false cross-boundary matches while still allowing replacements to span
        multiple text nodes within the same container (e.g., names split across runs).
        """
        groups: List[List[TextNode]] = []
        current_key = None
        current_group: List[TextNode] = []

        for node in self._text_nodes:
            if node.element is None:
                continue
            container = self._find_container(node.element)
            key = (node.xml_part, container)
            if current_key is None:
                current_key = key
            if key != current_key:
                if current_group:
                    groups.append(current_group)
                current_group = []
                current_key = key
            current_group.append(node)

        if current_group:
            groups.append(current_group)

        return groups

    def _find_container(self, element: any) -> any:
        """Find the closest structural container element for grouping."""
        if element is None:
            return None

        if self.file_type == ".docx":
            container_tags = {f"{{{NAMESPACES['w']}}}p"}
        elif self.file_type == ".pptx":
            container_tags = {f"{{{NAMESPACES['a']}}}p"}
        elif self.file_type == ".xlsx":
            container_tags = {
                f"{{{NAMESPACES['spreadsheet']}}}si",
                f"{{{NAMESPACES['spreadsheet']}}}is",
            }
        else:
            container_tags = set()

        for ancestor in element.iterancestors():
            if ancestor.tag in container_tags:
                return ancestor

        # Fallback: group by immediate parent
        return element.getparent()


# ============================================================
# High-level API functions
# ============================================================

def mask_ooxml_file(
    input_path: Path,
    output_path: Optional[Path] = None,
    dry_run: bool = False,
    use_llm: bool = False,  # New parameter
    llm_stream: bool = True
) -> dict:
    """
    Mask sensitive information in an Office file using OOXML processing.
    
    Args:
        input_path: Path to input file
        output_path: Path for output (default: input_masked.ext)
        dry_run: If True, only return detection results
        use_llm: If True, use LLM for smart detection and rewriting
        llm_stream: If True, use streaming for LLM (faster start)
        
    Returns:
        Dict with masking results
    """
    from ..core.merger import merge_spans
    from ..detector.regex import detect_by_regex
    from ..masker.placeholder import PlaceholderGenerator
    from ..vault.storage import VaultStorage
    
    # Initialize replacements dictionary
    replacements = {}
    mapping_candidates = {}
    stats = {}
    
    input_path = Path(input_path)
    
    # Initialize processor and get text
    processor = OOXMLProcessor(input_path)
    processor.extract_text_nodes()
    combined_text = processor.get_combined_text()

    # Vault for stable, unique mappings within project.
    vault = VaultStorage(str(input_path.parent))
    
    # 1. LLM Scan (Primary)
    if use_llm:
        try:
            from ..detector.llm_scanner import get_llm_scanner
            scanner = get_llm_scanner()
            llm_result = scanner.scan_and_rewrite(combined_text, stream=llm_stream)
            
            # Use LLM mappings directly
            for orig, proposed in llm_result.mappings.items():
                pii_type = llm_result.pii_types.get(orig, "UNKNOWN")
                mapping_candidates[orig] = (pii_type, proposed, "llm_scan", 1.0)
                replacements[orig] = proposed
            
            # Convert types for stats
            for orig, pii_type in llm_result.pii_types.items():
                stats[pii_type] = stats.get(pii_type, 0) + 1
                
        except Exception as e:
            print(f"LLM Scan failed, falling back to heuristic: {e}")
            # Fallback will be handled by regular detection below
    
    # 2. Heuristic/Smart Detection (Auxiliary/Fallback)
    # Only run for parts not already covered by LLM? 
    # Or run fully and merge? Running fully produces better safety.
    
    # Try smart detection first, fallback to heuristic
    try:
        from ..detector.smart_detector import detect_chinese_names_smart, is_smart_detection_available
        use_smart = is_smart_detection_available()
    except ImportError:
        use_smart = False
    
    if not use_smart:
        from ..detector.heuristic import detect_chinese_names

    # Detect entities
    spans = []
    spans.extend(detect_by_regex(combined_text))
    
    if use_smart:
        # Don't use LLM verification inside smart detector if we already used global LLM scan
        # unless global LLM scan failed
        # If global LLM was used (use_llm=True) and didn't fail (implied by execution flow), 
        # we should SKIP heuristic name detection to avoid false positives (e.g. "项目")
        if not use_llm:
            spans.extend(detect_chinese_names_smart(combined_text))
    else:
        if not use_llm:
            spans.extend(detect_chinese_names(combined_text))
    
    # Merge spans
    final_spans = merge_spans(spans, combined_text)
    
    # Generate replacements for heuristic matches
    generator = PlaceholderGenerator()
    
    for span in final_spans:
        # CRITICAL: Only add if not already handled by LLM
        # Refinement: Check if the exact text is already in replacements
        if span.text not in replacements:
            proposed = generator.generate(span.text, span.type)
            mapping_candidates[span.text] = (
                span.type.value,
                proposed,
                span.source.value,
                span.confidence,
            )
            replacements[span.text] = proposed
            stats[span.type.value] = stats.get(span.type.value, 0) + 1
    
    result = {
        "success": True,
        "file": str(input_path),
        "entities_found": sum(stats.values()),  # Count all entities including LLM's
        "stats": stats,
    }
    
    if dry_run:
        result["replacements"] = replacements
        return result
    
    with vault.batch("mask-session"):
        for original, candidate in mapping_candidates.items():
            entity_type, proposed, source, confidence = candidate
            replacements[original] = vault.get_or_create_mapping(
                original=original,
                entity_type=entity_type,
                masked=proposed,
                source=source,
                confidence=confidence,
            )

        processor.replace_in_place(replacements)

        if output_path is None:
            output_path = input_path.with_stem(input_path.stem + "_masked")
        processor.save(output_path)

        mappings = {v: k for k, v in replacements.items()}
        session_id = vault.create_session(
            [str(input_path)], mappings, stats, output_files=[str(output_path)]
        )
    
    result["session_id"] = session_id
    result["output"] = str(output_path)
    
    return result


def restore_ooxml_file(
    input_path: Path,
    mappings: Dict[str, str],
    output_path: Optional[Path] = None
) -> dict:
    """
    Restore masked Office file to original.
    
    Args:
        input_path: Path to masked file
        mappings: Dict of {masked -> original}
        output_path: Output path (default: overwrite input)
        
    Returns:
        Dict with restoration results
    """
    input_path = Path(input_path)
    
    processor = OOXMLProcessor(input_path)
    processor.extract_text_nodes()
    
    # Count changes
    changes_count = processor.count_replacements(mappings)
    
    if changes_count == 0:
        return {
            "success": True,
            "file": str(input_path),
            "changes": 0,
        }
    
    # Apply restoration
    processor.replace_in_place(mappings)
    
    # Save
    if output_path is None:
        output_path = input_path
    processor.save(output_path)
    
    return {
        "success": True,
        "file": str(input_path),
        "output": str(output_path),
        "restored_count": changes_count,  # Renamed for clarity
    }


def is_ooxml_file(file_path: Path) -> bool:
    """Check if file is an Office Open XML document."""
    return Path(file_path).suffix.lower() in {'.docx', '.xlsx', '.pptx'}
