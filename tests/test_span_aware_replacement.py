from pathlib import Path


def test_plaintext_replace_spans_only_replaces_selected_occurrence(tmp_path: Path):
    from vibemask.core.replacer import Replacement
    from vibemask.core.plaintext import PlainTextProcessor

    input_path = tmp_path / "input.txt"
    input_path.write_text("Alice is public. Alice is private.", encoding="utf-8")

    processor = PlainTextProcessor(input_path)
    processor.load()
    processor.extract_segments()

    second = processor.get_combined_text().rindex("Alice")
    count = processor.replace_spans(
        [
            Replacement(
                start=second,
                end=second + len("Alice"),
                original="Alice",
                masked="Carol",
            )
        ]
    )

    output_path = tmp_path / "output.txt"
    processor.save(output_path)

    assert count == 1
    assert output_path.read_text(encoding="utf-8") == "Alice is public. Carol is private."


def test_cli_span_based_engine_does_not_replace_unselected_duplicate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    from vibemask.core.span import EntityType, SourceType, Span
    import vibemask.detector.hybrid as hybrid

    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "masked.txt"
    input_path.write_text("Alice is public. Alice is private.", encoding="utf-8")

    class DummyHybridDetector:
        def __init__(self, **kwargs):
            pass

        def detect_document(self, processor, text):
            start = text.rindex("Alice")
            return [
                Span(
                    start=start,
                    end=start + len("Alice"),
                    text="Alice",
                    type=EntityType.PERSON,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason="test:selected_only",
                )
            ]

    monkeypatch.setattr(hybrid, "HybridDetector", DummyHybridDetector)

    from vibemask import cli

    cli.mask(file=input_path, output=output_path)

    masked_text = output_path.read_text(encoding="utf-8")
    assert masked_text.startswith("Alice is public. ")
    assert "Alice is private" not in masked_text


def test_docx_replace_spans_only_replaces_selected_occurrence(tmp_path: Path):
    import pytest

    docx = pytest.importorskip("docx")

    from vibemask.core import factory
    from vibemask.core.replacer import Replacement

    input_path = tmp_path / "input.docx"
    doc = docx.Document()
    doc.add_paragraph("Alice is public.")
    doc.add_paragraph("Alice is private.")
    doc.save(input_path)

    processor = factory.get_processor(input_path)
    processor.load()
    processor.extract_segments()
    text = processor.get_combined_text()

    second = text.rindex("Alice")
    count = processor.replace_spans(
        [
            Replacement(
                start=second,
                end=second + len("Alice"),
                original="Alice",
                masked="Carol",
            )
        ]
    )

    output_path = tmp_path / "output.docx"
    processor.save(output_path)

    output_text = factory.extract_text(output_path)
    assert count == 1
    assert "Alice is public." in output_text
    assert "Carol is private." in output_text
    assert "Alice is private." not in output_text


def test_xlsx_replace_spans_only_replaces_selected_inline_cell(tmp_path: Path):
    import pytest

    openpyxl = pytest.importorskip("openpyxl")

    from vibemask.core import factory
    from vibemask.core.replacer import Replacement

    input_path = tmp_path / "input.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet["A1"] = "Alice"
    sheet["A2"] = "Alice"
    workbook.save(input_path)

    processor = factory.get_processor(input_path)
    processor.load()
    processor.extract_segments()
    text = processor.get_combined_text()

    second = text.rindex("Alice")
    count = processor.replace_spans(
        [Replacement(start=second, end=second + 5, original="Alice", masked="Carol")]
    )
    output_path = tmp_path / "output.xlsx"
    processor.save(output_path)

    output_workbook = openpyxl.load_workbook(output_path)
    output_sheet = output_workbook.active

    assert count == 1
    assert output_sheet["A1"].value == "Alice"
    assert output_sheet["A2"].value == "Carol"


def test_xlsx_replace_spans_clones_shared_string_for_selected_cell(tmp_path: Path):
    import zipfile
    from xml.etree import ElementTree as ET

    import pytest

    openpyxl = pytest.importorskip("openpyxl")

    from vibemask.core import factory
    from vibemask.core.replacer import Replacement

    input_path = tmp_path / "input.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet["A1"] = "Alice"
    sheet["A2"] = "Alice"
    workbook.save(input_path)

    _convert_inline_alice_cells_to_shared_string(input_path)

    processor = factory.get_processor(input_path)
    processor.load()
    processor.extract_segments()
    text = processor.get_combined_text()

    second = text.rindex("Alice")
    count = processor.replace_spans(
        [Replacement(start=second, end=second + 5, original="Alice", masked="Carol")]
    )
    output_path = tmp_path / "output.xlsx"
    processor.save(output_path)

    output_workbook = openpyxl.load_workbook(output_path)
    output_sheet = output_workbook.active

    assert count == 1
    assert output_sheet["A1"].value == "Alice"
    assert output_sheet["A2"].value == "Carol"

    with zipfile.ZipFile(output_path) as zf:
        shared_xml = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    assert len(shared_xml.findall("s:si", ns)) == 2


def _convert_inline_alice_cells_to_shared_string(path: Path) -> None:
    import zipfile
    from xml.etree import ElementTree as ET

    spreadsheet_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    content_types_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    ET.register_namespace("", spreadsheet_ns)
    ET.register_namespace("", content_types_ns)

    temp_path = path.with_suffix(".shared.tmp")

    with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(data)
                for cell in root.findall(f".//{{{spreadsheet_ns}}}c"):
                    if cell.attrib.get("r") not in {"A1", "A2"}:
                        continue
                    for child in list(cell):
                        cell.remove(child)
                    cell.set("t", "s")
                    value = ET.SubElement(cell, f"{{{spreadsheet_ns}}}v")
                    value.text = "0"
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            elif item.filename == "[Content_Types].xml":
                root = ET.fromstring(data)
                exists = any(
                    child.attrib.get("PartName") == "/xl/sharedStrings.xml"
                    for child in root
                )
                if not exists:
                    override = ET.SubElement(root, f"{{{content_types_ns}}}Override")
                    override.set("PartName", "/xl/sharedStrings.xml")
                    override.set(
                        "ContentType",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
                    )
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            zout.writestr(item, data)

        shared_xml = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<sst xmlns="{spreadsheet_ns}" count="2" uniqueCount="1">'
            f"<si><t>Alice</t></si>"
            f"</sst>"
        )
        zout.writestr("xl/sharedStrings.xml", shared_xml)

    temp_path.replace(path)
