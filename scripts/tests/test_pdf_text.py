# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import pdf_text  # noqa: E402


def write_pdf(path, pages=("Page one text", "Page two text")):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids [%s] /Count %d >>" % (
            " ".join("%d 0 R" % (3 + index) for index in range(len(pages))), len(pages)
        )).encode(),
    ]
    font_number = 3 + len(pages) * 2
    for index in range(len(pages)):
        objects.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                        "/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>" %
                        (font_number, 3 + len(pages) + index)).encode())
    for text in pages:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = ("BT /F1 12 Tf 50 800 Td (%s) Tj ET" % escaped).encode("ascii")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    body = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += ("%d 0 obj\n" % number).encode() + obj + b"\nendobj\n"
    xref_at = len(body)
    body += ("xref\n0 %d\n" % (len(objects) + 1)).encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        body += ("%010d 00000 n \n" % offset).encode()
    body += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" %
             (len(objects) + 1, xref_at)).encode()
    path.write_bytes(bytes(body))


class PdfTextTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "report.pdf"
        write_pdf(self.path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_page_count_and_texts(self):
        self.assertEqual(pdf_text.page_count(self.path), 2)
        pages = list(pdf_text.page_texts(self.path))
        self.assertEqual([number for number, _ in pages], [1, 2])
        self.assertIn("Page one", pages[0][1])
        self.assertIn("Page two", pages[1][1])

    def test_page_range_is_one_based_and_clipped(self):
        self.assertEqual(list(pdf_text.parse_pages("2", 2)), [1])
        with self.assertRaises(ValueError):
            pdf_text.parse_pages("5", 2)
        with self.assertRaises(ValueError):
            pdf_text.parse_pages("0-2", 2)

    def test_missing_backend_fails_clearly(self):
        with patch.object(pdf_text, "_backend", return_value=(None, None)):
            self.assertIsNone(pdf_text.page_count(self.path))
            with self.assertRaises(SystemExit):
                list(pdf_text.page_texts(self.path))

    def test_pdftotext_backend(self):
        with patch.object(pdf_text, "_backend", return_value=("pdftotext", None)), \
                patch.object(pdf_text, "_pdftotext", return_value="First\fSecond\f"):
            self.assertEqual(pdf_text.page_count(self.path), 2)
            self.assertEqual(
                list(pdf_text.page_texts(self.path)),
                [(1, "First"), (2, "Second")],
            )


if __name__ == "__main__":
    unittest.main()
