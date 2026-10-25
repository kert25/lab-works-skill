# -*- coding: utf-8 -*-
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import docx_text  # noqa: E402
import make_docx  # noqa: E402


def write_png(path, width=640, height=360):
    stripe = b"\x00" + b"".join(
        (b"\x00\x00\x00" if (x // 40) % 2 else b"\xff\xff\xff") for x in range(width)
    )
    raw = stripe * height

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


class CaptionNumberingTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def build(self, content, name="doc.docx"):
        output = self.root / name
        make_docx.build(str(output), 1, "Topic", content, str(self.root), None, title=False)
        return docx_text.extract_docx_text(output)

    def test_figure_captions_are_numbered_in_order(self):
        write_png(self.root / "one.png")
        write_png(self.root / "two.png")
        text = self.build([
            {"h": "Result"},
            {"img": "one.png", "caption": "Первый график"},
            {"img": "two.png", "caption": "Второй график"},
        ])
        self.assertIn("Рисунок 1 — Первый график", text)
        self.assertIn("Рисунок 2 — Второй график", text)

    def test_explicit_figure_number_is_kept(self):
        write_png(self.root / "one.png")
        text = self.build([{"img": "one.png", "caption": "Рисунок 7 — Схема"}])
        self.assertIn("Рисунок 7 — Схема", text)
        self.assertNotIn("Рисунок 1 — Рисунок 7", text)

    def test_table_caption_precedes_table(self):
        text = self.build([
            {"table": {"rows": [["Параметр", "Значение"], ["A1", "B1"]], "caption": "Результаты"}},
        ])
        self.assertIn("Таблица 1 — Результаты", text)
        self.assertLess(text.index("Таблица 1"), text.index("A1"))

    def test_table_without_caption_stays_plain(self):
        text = self.build([{"table": {"rows": [["Параметр", "Значение"]]}}])
        self.assertNotIn("Таблица", text)

    def test_blank_caption_is_ignored(self):
        write_png(self.root / "one.png")
        text = self.build([{"img": "one.png", "caption": "   "}])
        self.assertNotIn("Рисунок", text)


class TableSpanTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def build(self, table):
        output = self.root / "doc.docx"
        make_docx.build(str(output), 1, "Topic", [{"table": table}], str(self.root), None, title=False)
        with zipfile.ZipFile(output) as archive:
            return archive.read("word/document.xml").decode("utf-8")

    def test_colspan_widens_grid(self):
        xml = self.build({"rows": [[{"text": "Header", "colspan": 2}], ["A", "B"]]})
        self.assertIn('w:gridSpan w:val="2"', xml)
        self.assertEqual(xml.count("<w:gridCol "), 2)

    def test_rowspan_emits_continuation_cell(self):
        xml = self.build({"rows": [[{"text": "A", "rowspan": 2}, "B"], ["C"]]})
        self.assertIn('w:vMerge w:val="restart"', xml)
        self.assertIn("<w:vMerge/>", xml)

    def test_combined_spans_fill_three_columns(self):
        xml = self.build({"rows": [[{"text": "X", "colspan": 2, "rowspan": 2}, "Y"], ["Z"]]})
        self.assertEqual(xml.count("<w:gridCol "), 3)
        self.assertIn('w:gridSpan w:val="2"', xml)
        self.assertIn('w:vMerge w:val="restart"', xml)

    def test_rowspan_out_of_bounds_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "rowspan"):
            self.build({"rows": [[{"text": "A", "rowspan": 3}]]})

    def test_widths_must_match_grid(self):
        with self.assertRaisesRegex(ValueError, "widths"):
            self.build({"rows": [[{"text": "A", "colspan": 2}]], "widths": [1000]})

    def test_row_must_fill_the_grid(self):
        with self.assertRaisesRegex(ValueError, "не заполняет"):
            self.build({"rows": [["A"], []]})


class FormulaTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def build(self, content):
        output = self.root / "doc.docx"
        make_docx.build(str(output), 1, "Topic", content, str(self.root), None, title=False)
        with zipfile.ZipFile(output) as archive:
            return archive.read("word/document.xml").decode("utf-8")

    def test_simple_formula_is_numbered(self):
        xml = self.build([{"formula": "E = mc^2"}])
        self.assertIn("<m:oMath>", xml)
        self.assertIn("<m:sSup>", xml)
        self.assertIn("(1)", xml)

    def test_fraction_and_square_root(self):
        xml = self.build([{"formula": "\\frac{a}{b} + \\sqrt{x}"}])
        self.assertIn("<m:f>", xml)
        self.assertIn("<m:rad>", xml)
        self.assertIn('m:degHide', xml)

    def test_formulas_are_numbered_sequentially(self):
        xml = self.build([{"formula": "a + b"}, {"formula": "c - d"}])
        self.assertIn("(1)", xml)
        self.assertIn("(2)", xml)

    def test_unnumbered_formula_has_no_number(self):
        xml = self.build([{"formula": "a + b", "numbered": False}])
        self.assertIn("<m:oMath>", xml)
        self.assertNotIn("(1)", xml)

    def test_invalid_formula_is_rejected(self):
        for value in ("", "^2", "\\frac{a}"):
            with self.assertRaises(ValueError):
                self.build([{"formula": value}])


class CodeStyleTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def build(self, content):
        output = self.root / "doc.docx"
        make_docx.build(str(output), 1, "Topic", content, str(self.root), None, title=False)
        with zipfile.ZipFile(output) as archive:
            return archive.read("word/document.xml").decode("utf-8")

    def test_shaded_code_has_fill_border_and_indent(self):
        xml = self.build([{"code": "print(1)\nprint(2)", "shaded": True}])
        self.assertIn('w:fill="F2F2F2"', xml)
        self.assertIn("<w:pBdr>", xml)
        self.assertEqual(xml.count("<w:top "), 1)
        self.assertEqual(xml.count("<w:bottom "), 1)
        self.assertEqual(xml.count("<w:left "), 2)
        self.assertIn('<w:ind w:left="120"', xml)

    def test_plain_code_has_no_fill_or_border(self):
        xml = self.build([{"code": "print(1)"}])
        self.assertNotIn("F2F2F2", xml)
        self.assertNotIn("<w:pBdr>", xml)

    def test_shaded_codefile_is_supported(self):
        (self.root / "app.py").write_text("print('ok')\n", encoding="utf-8")
        xml = self.build([{"codefile": "app.py", "shaded": True}])
        self.assertIn('w:fill="F2F2F2"', xml)
        self.assertIn("print('ok')", xml)


class StructureElementsTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def build(self, content):
        output = self.root / "doc.docx"
        make_docx.build(str(output), 1, "Topic", content, str(self.root), None, title=False)
        with zipfile.ZipFile(output) as archive:
            return {
                "document": archive.read("word/document.xml").decode("utf-8"),
                "styles": archive.read("word/styles.xml").decode("utf-8"),
                "rels": archive.read("word/_rels/document.xml.rels").decode("utf-8"),
                "text": docx_text.extract_docx_text(output),
            }

    def test_h1_and_h2_use_heading_styles(self):
        parts = self.build([{"h": "Section"}, {"h2": "Subsection"}])
        self.assertIn('w:pStyle w:val="Heading1"', parts["document"])
        self.assertIn('w:pStyle w:val="Heading2"', parts["document"])
        self.assertIn('w:styleId="Heading1"', parts["styles"])
        self.assertIn('w:styleId="Heading2"', parts["styles"])

    def test_unordered_list_has_dash_and_hanging_indent(self):
        parts = self.build([{"ul": ["First", "Second"]}])
        self.assertIn("– First", parts["text"])
        self.assertIn("– Second", parts["text"])
        self.assertIn('w:hanging="360"', parts["document"])

    def test_ordered_list_is_numbered(self):
        parts = self.build([{"ol": ["First", "Second"]}])
        self.assertIn("1. First", parts["text"])
        self.assertIn("2. Second", parts["text"])

    def test_hyperlink_creates_external_relationship(self):
        parts = self.build([{"link": "https://example.com/a?x=1&y=2", "text": "Example"}])
        self.assertIn("<w:hyperlink r:id=", parts["document"])
        self.assertIn("Example", parts["text"])
        self.assertIn("TargetMode=\"External\"", parts["rels"])
        self.assertIn("https://example.com/a?x=1&amp;y=2", parts["rels"])

    def test_empty_list_and_link_are_rejected(self):
        for content in ([{"ul": []}], [{"ol": []}], [{"link": ""}], [{"ul": [""]}]):
            with self.assertRaises(ValueError):
                self.build(content)


if __name__ == "__main__":
    unittest.main()
