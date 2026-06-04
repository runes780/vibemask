"""
OOXML Document Processor Adapter.
Wraps OOXMLProcessor to implement the unified DocumentProcessor interface.
"""

from pathlib import Path
from copy import deepcopy
from typing import List, Dict, Optional

from .processor import DocumentProcessor, TextSegment, register_processor
from .replacer import Replacement, apply_replacements
from .ooxml import OOXMLProcessor as BaseOOXMLProcessor, etree


SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
SPREADSHEET_NAMESPACES = {"spreadsheet": SPREADSHEET_NS}


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

        if self.file_path.suffix.lower() == ".xlsx":
            return self._extract_xlsx_segments()
        
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
                container_tags = {"{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"}
            elif file_type == ".pptx":
                container_tags = {"{http://schemas.openxmlformats.org/drawingml/2006/main}p"}
            elif file_type == ".xlsx":
                container_tags = {
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si",
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is",
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
        buffer_nodes: List[any] = []

        def flush(location: str):
            nonlocal current_offset, buffer_parts, buffer_nodes
            if not buffer_parts:
                return
            combined = "".join(buffer_parts)
            segment = TextSegment(
                text=combined,
                location=location,
                start_offset=current_offset,
                metadata={"text_nodes": list(buffer_nodes)},
            )
            segment.end_offset = current_offset + len(combined)
            self._segments.append(segment)
            current_offset = segment.end_offset + 1
            buffer_parts = []
            buffer_nodes = []

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
            buffer_nodes.append(node)

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

    def replace_spans(self, replacements: List[Replacement]) -> int:
        """Apply position-specific replacements inside OOXML text segments."""
        if not self._loaded:
            self.load()

        if self.file_path.suffix.lower() == ".xlsx":
            return self._replace_xlsx_spans(replacements)

        if not self._segments:
            self.extract_segments()

        modified_parts = set()
        applied = 0

        for segment in self._segments:
            segment_replacements = [
                replacement
                for replacement in replacements
                if segment.start_offset <= replacement.start
                and replacement.end <= segment.end_offset
                and segment.text[
                    replacement.start - segment.start_offset:
                    replacement.end - segment.start_offset
                ] == replacement.original
            ]
            if not segment_replacements:
                continue

            local_replacements = [
                Replacement(
                    start=replacement.start - segment.start_offset,
                    end=replacement.end - segment.start_offset,
                    original=replacement.original,
                    masked=replacement.masked,
                )
                for replacement in segment_replacements
            ]
            updated_text = apply_replacements(segment.text, local_replacements)
            if updated_text == segment.text:
                continue

            text_nodes = segment.metadata.get("text_nodes", [])
            if not text_nodes:
                continue

            before_texts = [
                (node.element.text or "") if node.element is not None else ""
                for node in text_nodes
            ]
            lengths = [len(text) for text in before_texts]
            position = 0
            for index, (node, length) in enumerate(zip(text_nodes, lengths)):
                if node.element is None:
                    position += length
                    continue

                if index < len(text_nodes) - 1:
                    node.element.text = updated_text[position:position + length]
                    position += length
                else:
                    node.element.text = updated_text[position:]
                modified_parts.add(node.xml_part)

            segment.text = updated_text
            applied += len(local_replacements)

        for part_name in modified_parts:
            if part_name in self._processor._xml_trees:
                self._processor._zip_data[part_name] = etree.tostring(
                    self._processor._xml_trees[part_name],
                    xml_declaration=True,
                    encoding="UTF-8",
                    standalone=True,
                )

        return applied

    def _extract_xlsx_segments(self) -> List[TextSegment]:
        """Extract XLSX text as one segment per visible text cell."""
        self._processor.extract_text_nodes()
        shared_strings = self._load_shared_strings()
        self._segments = []
        current_offset = 0

        for part_name in self._processor._get_text_parts():
            if not part_name.startswith("xl/worksheets/"):
                continue
            root = self._processor._get_xml_tree(part_name)
            if root is None:
                continue

            cells = root.xpath(".//spreadsheet:c", namespaces=SPREADSHEET_NAMESPACES)
            for cell in cells:
                cell_ref = cell.get("r", "")
                cell_type = cell.get("t")

                if cell_type == "s":
                    value_node = cell.find(f"{{{SPREADSHEET_NS}}}v")
                    if value_node is None or value_node.text is None:
                        continue
                    try:
                        shared_index = int(value_node.text)
                    except ValueError:
                        continue
                    if shared_index < 0 or shared_index >= len(shared_strings):
                        continue

                    shared_entry = shared_strings[shared_index]
                    text = shared_entry["text"]
                    metadata = {
                        "xlsx_kind": "shared",
                        "sheet_part": part_name,
                        "cell": cell,
                        "cell_ref": cell_ref,
                        "value_node": value_node,
                        "shared_index": shared_index,
                        "shared_si": shared_entry["si"],
                        "text_elements": shared_entry["text_elements"],
                    }
                else:
                    text_elements = cell.xpath(".//spreadsheet:t", namespaces=SPREADSHEET_NAMESPACES)
                    if not text_elements:
                        continue
                    text = "".join(element.text or "" for element in text_elements)
                    metadata = {
                        "xlsx_kind": "inline",
                        "sheet_part": part_name,
                        "cell": cell,
                        "cell_ref": cell_ref,
                        "text_elements": text_elements,
                    }

                if not text:
                    continue

                segment = TextSegment(
                    text=text,
                    location=f"{part_name}:{cell_ref}",
                    start_offset=current_offset,
                    metadata=metadata,
                )
                segment.end_offset = current_offset + len(text)
                self._segments.append(segment)
                current_offset = segment.end_offset + 1

        return self._segments

    def _replace_xlsx_spans(self, replacements: List[Replacement]) -> int:
        """Apply position-specific replacements to XLSX cells."""
        if not self._segments:
            self.extract_segments()

        modified_parts = set()
        applied = 0

        for segment in self._segments:
            segment_replacements = [
                replacement
                for replacement in replacements
                if segment.start_offset <= replacement.start
                and replacement.end <= segment.end_offset
                and segment.text[
                    replacement.start - segment.start_offset:
                    replacement.end - segment.start_offset
                ] == replacement.original
            ]
            if not segment_replacements:
                continue

            local_replacements = [
                Replacement(
                    start=replacement.start - segment.start_offset,
                    end=replacement.end - segment.start_offset,
                    original=replacement.original,
                    masked=replacement.masked,
                )
                for replacement in segment_replacements
            ]
            updated_text = apply_replacements(segment.text, local_replacements)
            if updated_text == segment.text:
                continue

            kind = segment.metadata.get("xlsx_kind")
            if kind == "shared":
                if self._replace_shared_string_cell(segment, updated_text):
                    modified_parts.add("xl/sharedStrings.xml")
                    modified_parts.add(segment.metadata["sheet_part"])
                    applied += len(local_replacements)
                    segment.text = updated_text
            elif kind == "inline":
                self._redistribute_text(segment.metadata["text_elements"], updated_text)
                modified_parts.add(segment.metadata["sheet_part"])
                applied += len(local_replacements)
                segment.text = updated_text

        for part_name in modified_parts:
            if part_name in self._processor._xml_trees:
                self._processor._zip_data[part_name] = etree.tostring(
                    self._processor._xml_trees[part_name],
                    xml_declaration=True,
                    encoding="UTF-8",
                    standalone=True,
                )

        return applied

    def _replace_shared_string_cell(self, segment: TextSegment, updated_text: str) -> bool:
        shared_root = self._processor._get_xml_tree("xl/sharedStrings.xml")
        if shared_root is None:
            return False

        original_si = segment.metadata.get("shared_si")
        value_node = segment.metadata.get("value_node")
        if original_si is None or value_node is None:
            return False

        cloned_si = deepcopy(original_si)
        text_elements = cloned_si.xpath(".//spreadsheet:t", namespaces=SPREADSHEET_NAMESPACES)
        if not text_elements:
            text_element = etree.SubElement(cloned_si, f"{{{SPREADSHEET_NS}}}t")
            text_elements = [text_element]
        self._redistribute_text(text_elements, updated_text)

        shared_root.append(cloned_si)
        new_index = len(shared_root.findall(f"{{{SPREADSHEET_NS}}}si")) - 1
        value_node.text = str(new_index)
        _increment_int_attr(shared_root, "count", 1)
        _increment_int_attr(shared_root, "uniqueCount", 1)
        return True

    def _load_shared_strings(self) -> list[dict]:
        root = self._processor._get_xml_tree("xl/sharedStrings.xml")
        if root is None:
            return []

        entries = []
        for si in root.xpath(".//spreadsheet:si", namespaces=SPREADSHEET_NAMESPACES):
            text_elements = si.xpath(".//spreadsheet:t", namespaces=SPREADSHEET_NAMESPACES)
            entries.append(
                {
                    "si": si,
                    "text_elements": text_elements,
                    "text": "".join(element.text or "" for element in text_elements),
                }
            )
        return entries

    def _redistribute_text(self, text_elements: list, updated_text: str) -> None:
        lengths = [len(element.text or "") for element in text_elements]
        if not text_elements:
            return

        position = 0
        for index, element in enumerate(text_elements):
            length = lengths[index]
            if index < len(text_elements) - 1:
                element.text = updated_text[position:position + length]
                position += length
            else:
                element.text = updated_text[position:]
    
    def save(self, output_path: Optional[Path] = None) -> Path:
        """Save the document."""
        if output_path is None:
            output_path = self.file_path
        
        output_path = Path(output_path)
        self._processor.save(output_path)
        
        return output_path


def _increment_int_attr(element: any, attr_name: str, amount: int) -> None:
    try:
        value = int(element.get(attr_name, "0"))
    except ValueError:
        value = 0
    element.set(attr_name, str(value + amount))
