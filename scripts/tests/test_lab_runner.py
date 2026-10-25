# -*- coding: utf-8 -*-
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import docx_text  # noqa: E402
import lab_runner  # noqa: E402


def base_context():
    return {
        "university": ["Test University"],
        "discipline": "Web technologies",
        "student": "Ivanov I.I.",
        "group": "435",
        "teacher": "Petrov P.P.",
        "teacher_title": "Docent",
        "city": "Moscow",
        "year": "2026",
    }


def report_content():
    return [
        {"h": "Goal"},
        {"p": "Describe the purpose of the laboratory work in a complete sentence."},
        {"h": "Task"},
        {"p": "Describe the assignment and the variant used for the work."},
        {"h": "Result"},
        {"p": "Describe the produced artifacts and the observed result."},
    ]


def notes_content():
    filler = "This concise defence note explains the work and its demonstration order. "
    return [
        {"h": "Summary"},
        {"p": filler * 4},
        {"h": "Questions"},
        {"p": ("Question: how does it work? Answer: " + filler) * 3},
    ]


def write_png(path, width=800, height=600, blank=False):
    """Write a valid 8-bit RGB PNG: either a flat frame or a striped one."""
    if blank:
        rows = [b"\x00" + b"\xff\xff\xff" * width for _ in range(height)]
    else:
        stripe = b"\x00" + b"".join(
            (b"\x00\x00\x00" if (x // 40) % 2 else b"\xff\xff\xff") for x in range(width)
        )
        rows = [stripe for _ in range(height)]
    raw = b"".join(rows)

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def write_pdf(path, pages=("Page one text", "Page two text")):
    """Write a minimal valid PDF with one text page per entry."""
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


def write_documents(root, directory="ЛР1", number=1):
    """Build a complete report set: DOCX, PDF, text.docx and content.json."""
    lab_dir = root / directory
    lab_dir.mkdir(parents=True, exist_ok=True)
    context = base_context()
    content = report_content()
    lab_runner.build(str(lab_dir / ("Отчет_ЛР%d.docx" % number)), number, "Topic",
                     content, str(lab_dir), context, title=True)
    lab_runner.build(str(lab_dir / "text.docx"), number, "Topic",
                     notes_content(), str(lab_dir), None, title=False)
    write_pdf(lab_dir / ("Отчет_ЛР%d.pdf" % number))
    (lab_dir / "content.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8"
    )
    return lab_dir


class RunnerPathsTest(unittest.TestCase):
    def test_paths_use_directory_or_legacy_default(self):
        root = Path("C:/workspace")
        default_content, default_report, _ = lab_runner.paths_for(root, 1, {})
        self.assertEqual(default_content, root / "ЛР1" / "content.json")
        self.assertEqual(default_report, root / "ЛР1" / "Отчет_ЛР1.docx")

        content, report, _ = lab_runner.paths_for(
            root, 1, {"directory": "practice/module-a"}
        )
        self.assertEqual(content, root / "practice/module-a" / "content.json")
        self.assertEqual(report, root / "practice/module-a" / "Отчет_ЛР1.docx")

    def test_directory_and_methodical_guide_must_be_relative(self):
        root = Path("C:/workspace")
        with self.assertRaisesRegex(SystemExit, "directory"):
            lab_runner.paths_for(root, 1, {"directory": "C:/outside"})

        with tempfile.TemporaryDirectory() as tempdir:
            absolute_guide = Path(tempdir) / "guide.pdf"
            absolute_guide.write_bytes(b"%PDF")
            with self.assertRaisesRegex(SystemExit, "methodical_guide"):
                lab_runner.build_report(
                    root,
                    1,
                    {"methodical_guide": str(absolute_guide), "theme": "Topic"},
                )


class RunnerBuildTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        lab_dir = self.root / "ЛР1"
        lab_dir.mkdir()
        (lab_dir / "content.json").write_text("[]", encoding="utf-8")
        (lab_dir / "guide.pdf").write_bytes(b"%PDF")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_build_uses_report_number_but_directory_report_name(self):
        lab = {
            "directory": "ЛР1",
            "report_number": 9,
            "methodical_guide": "ЛР1/guide.pdf",
            "theme": "Topic",
        }
        with patch.object(lab_runner, "build") as build, patch.object(
            lab_runner, "load_context", return_value={}
        ), patch.object(lab_runner.os, "replace"):
            lab_runner.build_report(self.root, 1, lab)

        report = self.root / "ЛР1" / "Отчет_ЛР1.docx"
        temporary = Path(build.call_args.args[0])
        self.assertEqual(temporary.parent, report.parent)
        self.assertEqual(temporary.suffix, ".docx")
        self.assertEqual(build.call_args.args[1], 9)

    def test_build_text_uses_state_paths_without_title_page(self):
        lab = {"theme": "Topic"}
        (self.root / "ЛР1" / "content_text.json").write_text("[]", encoding="utf-8")
        with patch.object(lab_runner, "build") as build, patch.object(lab_runner.os, "replace"):
            lab_runner.build_text(self.root, 1, lab)

        temporary = Path(build.call_args.args[0])
        self.assertEqual(temporary.parent, self.root / "ЛР1")
        self.assertEqual(temporary.suffix, ".docx")
        self.assertIsNone(build.call_args.args[5])
        self.assertFalse(build.call_args.kwargs["title"])

    def test_missing_methodical_guide_stops_before_build(self):
        lab = {
            "methodical_guide": "ЛР1/missing.pdf",
            "theme": "Topic",
        }
        with patch.object(lab_runner, "build") as build:
            with self.assertRaisesRegex(SystemExit, "methodical_guide"):
                lab_runner.build_report(self.root, 1, lab)
        build.assert_not_called()

    def test_legacy_record_without_methodical_guide_still_builds(self):
        lab = {"theme": "Topic"}
        with patch.object(lab_runner, "build") as build, patch.object(
            lab_runner, "load_context", return_value={}
        ), patch.object(lab_runner.os, "replace"):
            lab_runner.build_report(self.root, 1, lab)
        self.assertEqual(build.call_args.args[1], 1)

    def test_verify_report_accepts_existing_directory_artifact(self):
        screenshots = self.root / "ЛР1" / "screenshots"
        screenshots.mkdir()
        lab_runner.verify_report(
            self.root, 1, {"artifacts": ["ЛР1/screenshots"]}
        )

    def test_check_screenshot_rejects_too_small_png(self):
        image = self.root / "small.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + (100).to_bytes(4, "big") + (100).to_bytes(4, "big"))
        with self.assertRaisesRegex(SystemExit, "слишком мало"):
            lab_runner.check_screenshot(image)

    def test_verify_report_checks_code_and_image_references(self):
        lab_dir = self.root / "ЛР1"
        (lab_dir / "Ex1.html").write_text("<html></html>", encoding="utf-8")
        write_png(lab_dir / "shot.png")
        (lab_dir / "content.json").write_text(
            json.dumps([
                {"h": "Результат выполнения"},
                {"img": "shot.png"},
                {"codefile": "Ex1.html"},
            ]),
            encoding="utf-8",
        )
        lab_runner.verify_report(self.root, 1, {"artifacts": ["ЛР1/Ex1.html"]})

    def test_verify_report_requires_result_section_for_code(self):
        lab_dir = self.root / "ЛР1"
        (lab_dir / "Ex1.html").write_text("<html></html>", encoding="utf-8")
        (lab_dir / "content.json").write_text(
            json.dumps([{"h": "Goal"}, {"codefile": "Ex1.html"}]),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SystemExit, "Результат выполнения"):
            lab_runner.verify_report(self.root, 1, {"artifacts": ["ЛР1/Ex1.html"]})

    def test_verify_report_rejects_missing_content_reference(self):
        lab_dir = self.root / "ЛР1"
        (lab_dir / "content.json").write_text(
            json.dumps([{"img": "missing.png"}]), encoding="utf-8"
        )
        with self.assertRaisesRegex(SystemExit, "ссылки из content"):
            lab_runner.verify_report(self.root, 1, {"artifacts": ["ЛР1"]})

    def test_build_pdf_uses_libreoffice_when_word_fails(self):
        report = self.root / "ЛР1" / "Отчет_ЛР1.docx"
        report.write_bytes(b"docx")
        pdf = self.root / "ЛР1" / "Отчет_ЛР1.pdf"

        def run(command, **_):
            if command[0] == "powershell":
                raise subprocess.CalledProcessError(1, command)
            pdf.write_bytes(b"%PDF-1.7\n")

        with patch.object(lab_runner.subprocess, "run", side_effect=run), patch.object(
            lab_runner.shutil, "which", return_value="soffice"
        ):
            lab_runner.build_pdf(self.root, 1, {})
        self.assertEqual(pdf.read_bytes()[:4], b"%PDF")

    def test_quality_gate_checks_report_pdf_and_cheat_sheet(self):
        lab_dir = write_documents(self.root)
        lab = {
            "artifacts": [
                "ЛР1/Отчет_ЛР1.docx",
                "ЛР1/Отчет_ЛР1.pdf",
                "ЛР1/text.docx",
            ]
        }
        lab_runner.quality_gate(self.root, 1, lab)
        self.assertTrue((lab_dir / "quality_gate.json").is_file())

    def test_quality_gate_rejects_invalid_pdf(self):
        lab_dir = write_documents(self.root)
        (lab_dir / "Отчет_ЛР1.pdf").write_bytes(b"not a PDF")
        lab = {"artifacts": ["ЛР1/Отчет_ЛР1.docx", "ЛР1/Отчет_ЛР1.pdf", "ЛР1/text.docx"]}
        with self.assertRaisesRegex(SystemExit, "сигнатуры"):
            lab_runner.quality_gate(self.root, 1, lab)


class RunnerStateValidationTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_legacy_minimal_lab_state_is_valid(self):
        lab_runner.validate_lab_state(
            self.root, 1, {"status": "not_started", "theme": "Topic"}
        )

    def test_state_validation_accepts_portable_metadata(self):
        lab = {
            "status": "ready_for_review",
            "theme": "Topic",
            "directory": "ЛР1",
            "artifacts": ["ЛР1/source.py", "ЛР1/screenshots"],
            "commands": {"check": "py _tools/check.py"},
            "command_templates": {"server": "py -m http.server <port>"},
            "tooling": {"inspector": "bundled:inspect_docx"},
            "verification": {
                "status": "passed",
                "checked_at": "2026-10-01T12:34:56Z",
                "checks": ["artifacts"],
                "warnings": [],
            },
        }
        lab_runner.validate_lab_state(self.root, 1, lab)

    def test_state_validation_rejects_absolute_command_path(self):
        with self.assertRaisesRegex(SystemExit, "абсолютный путь"):
            lab_runner.validate_lab_state(
                self.root, 1, {"theme": "Topic", "commands": {"check": "py C:/Users/test/tool.py"}}
            )

    def test_state_validation_rejects_duplicate_artifacts(self):
        with self.assertRaisesRegex(SystemExit, "дублирующиеся"):
            lab_runner.validate_lab_state(
                self.root, 1, {"theme": "Topic", "artifacts": ["ЛР1/a.txt", "ЛР1\\a.txt"]}
            )

    def test_state_validation_rejects_unknown_bundled_tool(self):
        with self.assertRaisesRegex(SystemExit, "неизвестный"):
            lab_runner.validate_lab_state(
                self.root, 1, {"theme": "Topic", "tooling": {"tool": "bundled:unknown"}}
            )

    def test_validate_state_requires_no_report_outputs(self):
        state = {"labs": {"1": {"theme": "Topic"}}}
        lab_runner.validate_state(self.root, 1, state, state["labs"]["1"])


class RunnerQualityRecordTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.lab_dir = write_documents(self.root)
        self.lab = {
            "theme": "Topic",
            "artifacts": ["ЛР1/Отчет_ЛР1.docx", "ЛР1/Отчет_ЛР1.pdf", "ЛР1/text.docx"],
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_quality_gate_writes_portable_record(self):
        lab_runner.quality_gate(self.root, 1, self.lab)
        record = json.loads((self.lab_dir / "quality_gate.json").read_text(encoding="utf-8"))
        self.assertEqual(record["schema_version"], 3)
        self.assertIn("report_structure", record["checks"])
        self.assertEqual(record["status"], "passed")
        self.assertTrue(record["checked_at"].endswith("Z"))
        self.assertEqual(record["files"], ["ЛР1/Отчет_ЛР1.docx", "ЛР1/Отчет_ЛР1.pdf", "ЛР1/text.docx"])

    def test_failed_gate_preserves_existing_record(self):
        record_path = self.lab_dir / "quality_gate.json"
        record_path.write_text('{"status": "previous"}\n', encoding="utf-8")
        (self.lab_dir / "Отчет_ЛР1.pdf").write_bytes(b"not a PDF")
        with self.assertRaisesRegex(SystemExit, "сигнатуры"):
            lab_runner.quality_gate(self.root, 1, self.lab)
        self.assertEqual(record_path.read_text(encoding="utf-8"), '{"status": "previous"}\n')

    def test_verify_report_ignores_absent_quality_gate_json_in_artifacts(self):
        lab = dict(self.lab)
        lab["artifacts"] = list(self.lab["artifacts"]) + ["ЛР1/quality_gate.json"]
        self.assertFalse((self.lab_dir / "quality_gate.json").exists())
        lab_runner.verify_report(self.root, 1, lab)

    def test_quality_gate_updates_lab_state(self):
        state_path = self.root / "_lab_state.json"
        initial_state = {
            "version": 2,
            "labs": {
                "1": {
                    "theme": "Topic",
                    "artifacts": ["ЛР1/Отчет_ЛР1.docx", "ЛР1/Отчет_ЛР1.pdf", "ЛР1/text.docx"],
                }
            }
        }
        state_path.write_text(json.dumps(initial_state, ensure_ascii=False, indent=2), encoding="utf-8")
        lab_runner.quality_gate(self.root, 1, initial_state["labs"]["1"])
        updated_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIn("ЛР1/quality_gate.json", updated_state["labs"]["1"]["artifacts"])
        self.assertEqual(updated_state["labs"]["1"]["verification"]["status"], "passed")


class ReportStructureGateTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.lab_dir = write_documents(self.root)
        self.lab = {
            "theme": "Topic",
            "artifacts": ["ЛР1/Отчет_ЛР1.docx", "ЛР1/Отчет_ЛР1.pdf", "ЛР1/text.docx"],
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_gate_rejects_title_only_report(self):
        (self.lab_dir / "content.json").write_text("[]", encoding="utf-8")
        lab_runner.build(
            str(self.lab_dir / "Отчет_ЛР1.docx"), 1, "Topic", [],
            str(self.lab_dir), base_context(), title=True,
        )
        with self.assertRaisesRegex(SystemExit, "раздел"):
            lab_runner.quality_gate(self.root, 1, self.lab)
        self.assertFalse((self.lab_dir / "quality_gate.json").exists())

    def test_gate_rejects_blank_evidence(self):
        (self.lab_dir / "app.py").write_text("print('hi')\n", encoding="utf-8")
        write_png(self.lab_dir / "shot.png", blank=True)
        content = report_content() + [
            {"h": "Результат выполнения"},
            {"img": "shot.png", "caption": "Рисунок 1 — вывод"},
            {"codefile": "app.py"},
        ]
        (self.lab_dir / "content.json").write_text(
            json.dumps(content, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaisesRegex(SystemExit, "пустое"):
            lab_runner.quality_gate(self.root, 1, self.lab)

    def test_gate_rejects_placeholder_notes(self):
        placeholder = [{"p": "Ответ: [Сформулируйте развернутый академический ответ] " * 6}]
        lab_runner.build(
            str(self.lab_dir / "text.docx"), 1, "Topic",
            notes_content() + placeholder, str(self.lab_dir), None, title=False,
        )
        with self.assertRaisesRegex(SystemExit, "заглушки"):
            lab_runner.quality_gate(self.root, 1, self.lab)

    def test_gate_records_warning_without_pymupdf(self):
        with patch.object(lab_runner, "pdf_page_count", return_value=None):
            lab_runner.quality_gate(self.root, 1, self.lab)
        record = json.loads((self.lab_dir / "quality_gate.json").read_text(encoding="utf-8"))
        self.assertTrue(record["warnings"])
        self.assertIn("PDF-бэкенд", record["warnings"][0])

    def test_blank_screenshot_is_rejected(self):
        image = self.root / "blank.png"
        write_png(image, blank=True)
        with self.assertRaisesRegex(SystemExit, "пустое"):
            lab_runner.check_screenshot(image)

    def test_textured_screenshot_is_accepted(self):
        image = self.root / "textured.png"
        write_png(image)
        lab_runner.check_screenshot(image)
        self.assertGreater(image.stat().st_size, 0)


class DocxTextTest(unittest.TestCase):
    def test_extract_docx_text_returns_visible_paragraphs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "guide.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:body><w:p><w:r><w:t>First</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t>Second</w:t></w:r></w:p></w:body></w:document>',
                )
            self.assertEqual(docx_text.extract_docx_text(path), "First\nSecond")

    def test_extract_docx_text_renders_tables_as_markdown(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "guide.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:body>'
                    '<w:p><w:r><w:t>Before</w:t></w:r></w:p>'
                    '<w:tbl>'
                    '<w:tr><w:tc><w:p><w:r><w:t>Variant</w:t></w:r></w:p></w:tc>'
                    '<w:tc><w:p><w:r><w:t>Task</w:t></w:r></w:p></w:tc></w:tr>'
                    '<w:tr><w:tc><w:p><w:r><w:t>9</w:t></w:r></w:p></w:tc>'
                    '<w:tc><w:p><w:r><w:t>Build a page</w:t></w:r></w:p></w:tc></w:tr>'
                    '</w:tbl>'
                    '<w:p><w:r><w:t>After</w:t></w:r></w:p>'
                    '</w:body></w:document>',
                )
            text = docx_text.extract_docx_text(path)
            self.assertIn("| Variant | Task |", text)
            self.assertIn("| 9 | Build a page |", text)
            self.assertLess(text.index("Before"), text.index("| Variant"))
            self.assertLess(text.index("| 9 |"), text.index("After"))


class ReadGuideAndInitTextTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.lab_dir = self.root / "ЛР1"
        self.lab_dir.mkdir()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_read_guide_and_init_text(self):
        guide_path = self.lab_dir / "guide.docx"
        guide_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'
            '<w:p><w:r><w:t>Тема: Введение в веб</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Контрольные вопросы:</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>1. Что такое DOM дерево?</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>2. Чем отличается flex от grid?</w:t></w:r></w:p>'
            '</w:body></w:document>'
        )
        with zipfile.ZipFile(guide_path, "w") as archive:
            archive.writestr("word/document.xml", guide_xml)

        lab = {
            "theme": "Введение в веб",
            "methodical_guide": "ЛР1/guide.docx",
        }

        # 1. Тест read_guide с выводом в файл
        out_txt = self.lab_dir / "guide_dump.txt"
        text = lab_runner.read_guide(self.root, 1, lab, out_path=str(out_txt))
        self.assertIn("Что такое DOM дерево?", text)
        self.assertTrue(out_txt.is_file())
        self.assertEqual(out_txt.read_text(encoding="utf-8"), text)

        # 2. Тест init_text
        lab_runner.init_text(self.root, 1, lab)
        text_content_path = self.lab_dir / "content_text.json"
        self.assertTrue(text_content_path.is_file())
        items = json.loads(text_content_path.read_text(encoding="utf-8"))
        p_texts = [it["p"] for it in items if "p" in it]
        self.assertTrue(any("Что такое DOM дерево?" in p for p in p_texts))
        self.assertTrue(any("Чем отличается flex от grid?" in p for p in p_texts))
        self.assertTrue(any("Ответ:" in p for p in p_texts))


if __name__ == "__main__":
    unittest.main()
