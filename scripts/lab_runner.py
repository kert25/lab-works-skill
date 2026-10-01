# -*- coding: utf-8 -*-
"""ASCII-safe runner for reports described by _lab_state.json.

Usage:
    py lab_runner.py --root <project-root> --lab N --action build-report
    py lab_runner.py --root <project-root> --lab N --action verify-report

Theme names and Cyrillic paths are read from UTF-8 JSON instead of shell
arguments. The selected lab record must contain `theme`; report content and
output paths default to the standard layout when omitted.
"""
import argparse
import json
import sys
from pathlib import Path

from make_docx import build, load_context

VALID_STATUSES = {"not_started", "in_progress", "ready_for_review", "completed"}


def fail(message):
    raise SystemExit("_lab_state.json: " + message)


def load_state(root):
    path = root / "_lab_state.json"
    try:
        with path.open(encoding="utf-8-sig") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        fail("не найден: %s" % path)
    except json.JSONDecodeError as exc:
        fail("некорректный JSON в %s: %s" % (path, exc))
    if not isinstance(state, dict) or not isinstance(state.get("labs"), dict):
        fail("ожидается объект с полем labs")
    return state


def get_lab(state, lab_number):
    lab = state["labs"].get(str(lab_number))
    if not isinstance(lab, dict):
        fail("labs.%d отсутствует или не является объектом" % lab_number)
    status = lab.get("status", "not_started")
    if status not in VALID_STATUSES:
        fail("labs.%d.status должен быть одним из: %s" %
             (lab_number, ", ".join(sorted(VALID_STATUSES))))
    theme = lab.get("theme")
    if not isinstance(theme, str) or not theme.strip():
        fail("labs.%d.theme отсутствует или пуст" % lab_number)
    return lab


def resolve(root, value):
    path = Path(value)
    return path if path.is_absolute() else root / path


def paths_for(root, lab_number, lab):
    lab_dir = root / ("ЛР%d" % lab_number)
    return (
        resolve(root, lab.get("content", lab_dir / "content.json")),
        resolve(root, lab.get("report", lab_dir / ("Отчет_ЛР%d.docx" % lab_number))),
        resolve(root, lab.get("context", "_context.json")),
    )


def build_report(root, lab_number, lab):
    content_path, report_path, context_path = paths_for(root, lab_number, lab)
    if not content_path.is_file():
        fail("не найден content: %s" % content_path)
    with content_path.open(encoding="utf-8-sig") as stream:
        content = json.load(stream)
    if not isinstance(content, list):
        fail("content должен быть JSON-списком: %s" % content_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    build(str(report_path), lab_number, lab["theme"], content,
          str(content_path.parent), load_context(str(context_path)))
    print("REPORT %s" % report_path)


def verify_report(root, lab_number, lab):
    _, report_path, _ = paths_for(root, lab_number, lab)
    artifacts = lab.get("artifacts", [str(report_path)])
    if not isinstance(artifacts, list) or not artifacts:
        fail("labs.%d.artifacts должен быть непустым списком" % lab_number)
    missing = [str(resolve(root, artifact)) for artifact in artifacts
               if not resolve(root, artifact).is_file()]
    if missing:
        fail("не найдены артефакты: %s" % "; ".join(missing))
    for artifact in artifacts:
        print("OK %s" % resolve(root, artifact))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="корень проекта; по умолчанию текущий каталог")
    parser.add_argument("--lab", type=int, required=True, help="номер ЛР")
    parser.add_argument("--action", required=True, choices=("build-report", "verify-report"))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    state = load_state(root)
    lab = get_lab(state, args.lab)
    if args.action == "build-report":
        build_report(root, args.lab, lab)
    else:
        verify_report(root, args.lab, lab)


if __name__ == "__main__":
    main()
