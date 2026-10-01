# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import zipfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
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
        ):
            lab_runner.build_report(self.root, 1, lab)

        report = self.root / "ЛР1" / "Отчет_ЛР1.docx"
        self.assertEqual(build.call_args.args[0], str(report))
        self.assertEqual(build.call_args.args[1], 9)

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
        ):
            lab_runner.build_report(self.root, 1, lab)
        self.assertEqual(build.call_args.args[1], 1)

    def test_verify_report_accepts_existing_directory_artifact(self):
        screenshots = self.root / "ЛР1" / "screenshots"
        screenshots.mkdir()
        lab_runner.verify_report(
            self.root, 1, {"artifacts": ["ЛР1/screenshots"]}
        )

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


if __name__ == "__main__":
    unittest.main()
