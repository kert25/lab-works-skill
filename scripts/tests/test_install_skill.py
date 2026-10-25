# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import install_skill  # noqa: E402


class InstallSkillTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source = self.root / "skill"
        self.source.mkdir()
        (self.source / "SKILL.md").write_text("---\nname: lab-works\n---\n", encoding="utf-8")
        (self.source / "scripts").mkdir()
        (self.source / "scripts" / "tool.py").write_text("print(1)\n", encoding="utf-8")
        (self.source / "scripts" / "__pycache__").mkdir()
        (self.source / "scripts" / "__pycache__" / "tool.pyc").write_bytes(b"cache")
        (self.source / ".git").mkdir()
        (self.source / ".git" / "config").write_text("git", encoding="utf-8")
        (self.source / ".skill-eval" / "workspace").mkdir(parents=True)
        (self.source / ".skill-eval" / "workspace" / "run.txt").write_text("run", encoding="utf-8")
        (self.source / "UPGRADE.md").write_text("roadmap", encoding="utf-8")
        self.target = self.root / "installed" / "lab-works"
        self.target.parent.mkdir()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_install_copies_skill_and_skips_dev_files(self):
        status = install_skill.install(self.source, self.target)
        self.assertEqual(status, "installed")
        files = install_skill.collect(self.target)
        self.assertIn("SKILL.md", files)
        self.assertIn("scripts/tool.py", files)
        self.assertNotIn("UPGRADE.md", files)
        self.assertNotIn(".git/config", files)
        self.assertFalse(any("__pycache__" in name for name in files))
        self.assertFalse(any(".skill-eval/workspace" in name for name in files))

    def test_check_reports_ok_then_drift(self):
        install_skill.install(self.source, self.target)
        self.assertEqual(install_skill.check(self.source, self.target)[0], "ok")

        (self.target / "SKILL.md").write_text("changed", encoding="utf-8")
        status, problems = install_skill.check(self.source, self.target)
        self.assertEqual(status, "drift")
        self.assertTrue(any("SKILL.md" in problem for problem in problems))

    def test_reinstall_creates_backup(self):
        install_skill.install(self.source, self.target)
        (self.target / "SKILL.md").write_text("previous", encoding="utf-8")

        status = install_skill.install(self.source, self.target)
        self.assertIn("backup:", status)
        backups = list(self.target.parent.glob("lab-works.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "SKILL.md").read_text(encoding="utf-8"), "previous")

    def test_no_backup_replaces_without_copy(self):
        install_skill.install(self.source, self.target)
        (self.target / "SKILL.md").write_text("previous", encoding="utf-8")
        install_skill.install(self.source, self.target, make_backup=False)
        self.assertFalse(list(self.target.parent.glob("lab-works.backup-*")))

    def test_reinstall_without_changes_is_idempotent(self):
        install_skill.install(self.source, self.target)
        self.assertEqual(install_skill.install(self.source, self.target), "up to date")
        self.assertFalse(list(self.target.parent.glob("lab-works.backup-*")))

    def test_pycache_in_target_is_not_drift(self):
        install_skill.install(self.source, self.target)
        cache = self.target / "scripts" / "__pycache__"
        cache.mkdir()
        (cache / "tool.pyc").write_bytes(b"cache")
        self.assertEqual(install_skill.check(self.source, self.target)[0], "ok")
        self.assertEqual(install_skill.install(self.source, self.target), "up to date")

    def test_missing_target_is_reported(self):
        status, problems = install_skill.check(self.source, self.root / "absent")
        self.assertEqual(status, "missing")
        self.assertTrue(problems)

    def test_target_inside_source_is_rejected(self):
        with self.assertRaises(SystemExit):
            install_skill.install(self.source, self.source / "nested")


if __name__ == "__main__":
    unittest.main()
