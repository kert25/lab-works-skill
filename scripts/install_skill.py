# -*- coding: utf-8 -*-
"""Install or update the lab-works skill in local agent skill directories.

Usage:
    py scripts/install_skill.py                  # update every detected target
    py scripts/install_skill.py --check          # report drift, exit 1 on drift
    py scripts/install_skill.py --target DIR     # explicit target (repeatable)
    py scripts/install_skill.py --no-backup      # replace without keeping the old copy

Targets are full skill directories (the folder that contains SKILL.md), not
skill roots. By default the script updates:

    ~/.agents/skills/lab-works
    ~/.gemini/config/skills/lab-works

Only files that belong to the skill are copied: development-only paths
(.git, .skill-eval/workspace, vendored tooling, caches) and UPGRADE.md are
skipped. Every replacement is verified by SHA-256 and rolled back on failure.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
SKIP_DIRECTORIES = {".git", "__pycache__", ".pytest_cache"}
SKIP_PATHS = {
    ".skill-eval/workspace",
    ".skill-eval/skill-creator",
    ".skill-eval/anthropic-skills",
    "UPGRADE.md",
}


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def ignore(directory: str, names: list[str]) -> set[str]:
    """Return names that must not be copied into an installed skill."""
    base = Path(directory)
    skipped = set()
    for name in names:
        candidate = base / name
        relative = candidate.as_posix()
        if name in SKIP_DIRECTORIES or name.endswith(".pyc"):
            skipped.add(name)
        elif any(relative.endswith("/" + tail) or relative == tail for tail in SKIP_PATHS):
            skipped.add(name)
    return skipped


def is_dev_path(relative: str) -> bool:
    """Return True for paths that belong to development only."""
    parts = relative.split("/")
    if any(part in SKIP_DIRECTORIES for part in parts):
        return True
    if relative.endswith(".pyc"):
        return True
    for tail in SKIP_PATHS:
        if relative == tail or relative.startswith(tail + "/"):
            return True
    return False


def collect(root: Path, skip_dev: bool = False) -> dict[str, str]:
    """Map every file of a skill tree to a SHA-256 digest."""
    digests = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = _relative(root, path)
        if skip_dev and is_dev_path(relative):
            continue
        digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def _replace_with_retries(source: Path, target: Path, attempts: int = 4) -> None:
    """Rename a path, retrying transient Windows locks (antivirus, indexer)."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError as error:
            last_error = error
            if attempt < attempts:
                time.sleep(0.25 * attempt)
    raise last_error


def _backup_path(target: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    candidate = target.parent / ("%s.backup-%s" % (target.name, stamp))
    counter = 2
    while candidate.exists():
        candidate = target.parent / ("%s.backup-%s-%d" % (target.name, stamp, counter))
        counter += 1
    return candidate


def _validate(source: Path, target: Path) -> None:
    if not (source / "SKILL.md").is_file():
        raise SystemExit("в источнике нет SKILL.md: %s" % source)
    resolved_source = source.resolve()
    resolved_target = target.resolve()
    if resolved_target == resolved_source or resolved_source in resolved_target.parents:
        raise SystemExit("цель не может совпадать с источником или лежать внутри него: %s" % target)


def check(source: Path, target: Path) -> tuple[str, list[str]]:
    """Compare an installed copy with the source and describe the difference."""
    if not target.is_dir():
        return "missing", ["каталог не найден: %s" % target]
    expected = collect(source, skip_dev=True)
    actual = collect(target, skip_dev=True)
    problems = []
    for name in sorted(set(expected) - set(actual)):
        problems.append("нет файла: %s" % name)
    for name in sorted(set(actual) - set(expected)):
        problems.append("лишний файл: %s" % name)
    for name in sorted(set(expected) & set(actual)):
        if expected[name] != actual[name]:
            problems.append("изменён: %s" % name)
    return ("drift" if problems else "ok"), problems


def install(source: Path, target: Path, make_backup: bool = True) -> str:
    """Replace the target with a verified copy of the source; return a status."""
    _validate(source, target)
    expected = collect(source, skip_dev=True)
    if target.is_dir() and collect(target, skip_dev=True) == expected:
        return "up to date"

    staging = target.parent / (".%s-stage-%s" % (target.name, uuid.uuid4().hex))
    shutil.copytree(source, staging, ignore=ignore)
    backup = None
    try:
        if target.exists():
            if make_backup:
                backup = _backup_path(target)
                _replace_with_retries(target, backup)
            else:
                shutil.rmtree(target)
        _replace_with_retries(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if backup is not None and backup.exists() and not target.exists():
            _replace_with_retries(backup, target)
        raise

    if collect(target, skip_dev=True) != expected:
        shutil.rmtree(target, ignore_errors=True)
        if backup is not None and backup.exists():
            _replace_with_retries(backup, target)
        raise SystemExit("установка не прошла проверку по SHA-256: %s" % target)
    return "installed" if backup is None else "installed (backup: %s)" % backup.name


def default_targets() -> list[Path]:
    home = Path.home()
    return [path for path in (
        home / ".agents" / "skills" / "lab-works",
        home / ".gemini" / "config" / "skills" / "lab-works",
    ) if path.parent.is_dir()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE, help="каталог скилла (по умолчанию — этот репозиторий)")
    parser.add_argument("--target", type=Path, action="append", help="каталог установки; можно повторить")
    parser.add_argument("--check", action="store_true", help="только сравнить копии и вернуть код 1 при расхождении")
    parser.add_argument("--no-backup", action="store_true", help="не сохранять предыдущую копию")
    arguments = parser.parse_args()

    source = arguments.source.resolve()
    targets = [path.expanduser() for path in arguments.target] if arguments.target else default_targets()
    if not targets:
        raise SystemExit("не найдено ни одного каталога установки; передайте --target")

    failed = False
    for target in targets:
        if arguments.check:
            status, problems = check(source, target)
            print("%s: %s" % (target, status))
            for problem in problems:
                print("  - %s" % problem)
            failed = failed or status != "ok"
        else:
            print("%s: %s" % (target, install(source, target, make_backup=not arguments.no_backup)))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
