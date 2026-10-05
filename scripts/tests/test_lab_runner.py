# -*- coding: utf-8 -*-
import json
import subprocess
import sys
import tempfile
import zipfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import docx_text  # noqa: E402
import lab_runner  # noqa: E402


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
        (lab_dir / "shot.png").write_bytes(b"PNG")
        (lab_dir / "content.json").write_text(
            json.dumps([{"codefile": "Ex1.html"}, {"img": "shot.png"}]),
            encoding="utf-8",
        )
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
        lab_dir = self.root / "ЛР1"
        report = lab_dir / "Отчет_ЛР1.docx"
        cheat_sheet = lab_dir / "text.docx"
        for document in (report, cheat_sheet):
            with zipfile.ZipFile(document, "w") as archive:
                archive.writestr("word/document.xml", "<document />")
        (lab_dir / "Отчет_ЛР1.pdf").write_bytes(b"%PDF-1.7\n")
        lab = {
            "artifacts": [
                "ЛР1/Отчет_ЛР1.docx",
                "ЛР1/Отчет_ЛР1.pdf",
                "ЛР1/text.docx",
            ]
        }
        lab_runner.quality_gate(self.root, 1, lab)

    def test_quality_gate_rejects_invalid_pdf(self):
        lab_dir = self.root / "ЛР1"
        for name in ("Отчет_ЛР1.docx", "text.docx"):
            with zipfile.ZipFile(lab_dir / name, "w") as archive:
                archive.writestr("word/document.xml", "<document />")
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
        self.lab_dir = self.root / "ЛР1"
        self.lab_dir.mkdir()
        for name in ("Отчет_ЛР1.docx", "text.docx"):
            with zipfile.ZipFile(self.lab_dir / name, "w") as archive:
                archive.writestr("word/document.xml", "<document />")
        (self.lab_dir / "Отчет_ЛР1.pdf").write_bytes(b"%PDF-1.7\n")
        (self.lab_dir / "content.json").write_text("[]", encoding="utf-8")
        self.lab = {
            "theme": "Topic",
            "artifacts": ["ЛР1/Отчет_ЛР1.docx", "ЛР1/Отчет_ЛР1.pdf", "ЛР1/text.docx"],
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_quality_gate_writes_portable_record(self):
        lab_runner.quality_gate(self.root, 1, self.lab)
        record = json.loads((self.lab_dir / "quality_gate.json").read_text(encoding="utf-8"))
        self.assertEqual(record["schema_version"], 2)
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


if __name__ == "__main__":
    unittest.main()
