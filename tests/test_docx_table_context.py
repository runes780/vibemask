"""
Tests for DOCX table-structure extraction and field-label suppression.

These cover the document-parsing layer (table cell coordinates + header/value
reconstruction + header-as-PII suppression) without needing the model.
"""

import re
from pathlib import Path

import pytest

docx = pytest.importorskip("docx")


def _make_table_docx(path: Path) -> None:
    doc = docx.Document()
    doc.add_paragraph("员工信息如下。")
    t = doc.add_table(rows=3, cols=3)
    for j, h in enumerate(["姓名", "手机号", "身份证号"]):
        t.rows[0].cells[j].text = h
    rows = [["王晓明", "13812345678", "110101198801011234"]]
    for i, r in enumerate(rows, start=1):
        for j, v in enumerate(r):
            t.rows[i].cells[j].text = v
    doc.save(str(path))


def test_docx_table_cells_carry_coordinates(tmp_path: Path):
    from vibemask.core import factory

    p = tmp_path / "t.docx"
    _make_table_docx(p)
    proc = factory.get_processor(str(p))
    proc.load()
    segments = proc.extract_segments()

    coord_re = re.compile(r":tbl\d+\.r\d+\.c\d+$")
    table_cells = [s for s in segments if coord_re.search(s.location or "")]
    # Header row + 1 data row = 6 table cells.
    assert len(table_cells) == 6
    texts_by_loc = {s.location: s.text for s in table_cells}
    # Header row (r0): the three labels.
    labels = [s.text for s in table_cells if ".r0." in s.location]
    assert set(labels) == {"姓名", "手机号", "身份证号"}
    # Non-table paragraph keeps the legacy index location.
    intro = [s for s in segments if s.text == "员工信息如下。"]
    assert intro and not coord_re.search(intro[0].location)


def test_build_docx_row_context_reconstructs_header_value(tmp_path: Path):
    from vibemask.core import factory
    from vibemask.detector.privacy_context import DOCX_CELL_RE, _build_docx_row_context

    p = tmp_path / "t.docx"
    _make_table_docx(p)
    proc = factory.get_processor(str(p))
    proc.load()
    proc.extract_segments()

    rows = {}
    for s in proc._segments:
        m = DOCX_CELL_RE.match(s.location or "")
        if m:
            key = (m.group("part"), m.group("tbl"))
            rows.setdefault(key, {}).setdefault(int(m.group("row")), {})[int(m.group("col"))] = s
    context, mappings = _build_docx_row_context(rows)

    # Values paired with their header labels; headers themselves are NOT values.
    assert "姓名: 王晓明" in context
    assert "手机号: 13812345678" in context
    assert "身份证号: 110101198801011234" in context
    # Only the 3 value cells are mapped (headers are labels, not detectable values).
    assert len(mappings) == 3


def test_field_label_suppression():
    from vibemask.detector.privacy_postprocess import is_field_label

    assert is_field_label("手机号")
    assert is_field_label("身份证号")
    assert is_field_label("姓名")
    assert is_field_label("联系电话")
    assert is_field_label("办公室电话")
    # Real values are not field labels.
    assert not is_field_label("王晓明")
    assert not is_field_label("13812345678")
    assert not is_field_label("test@example.com")


def test_false_person_suppression():
    """Financial/form vocabulary the model mis-flags as PERSON must be dropped."""
    from vibemask.detector.privacy_postprocess import is_false_person

    assert is_false_person("万元")
    assert is_false_person("单位")
    assert is_false_person("利润")
    assert is_false_person("金额")
    # Bare single CJK char = fragmentation, never a complete name.
    assert is_false_person("万")
    assert is_false_person("李")
    # Real names are kept.
    assert not is_false_person("王晓明")
    assert not is_false_person("欧阳小雪")
    assert not is_false_person("张三")
