# -*- coding: utf-8 -*-
import struct
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
