# -*- coding: utf-8 -*-
"""ASCII-safe runner for reports described by _lab_state.json.

Usage:
    py lab_runner.py --root <project-root> --lab N --action build-report
    py lab_runner.py --root <project-root> --lab N --action verify-report
    py lab_runner.py --root <project-root> --lab N --action quality-gate

Theme names and Cyrillic paths are read from UTF-8 JSON instead of shell
arguments. The selected lab record must contain `theme`; report content and
output paths default to the standard layout when omitted.
"""
import argparse
import json
import sys
import zipfile
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


def relative_path(root, value, field, lab_number):
    path = Path(value)
    if path.is_absolute():
        fail("labs.%d.%s должен быть относительным путём" % (lab_number, field))
    return root / path


def lab_directory(root, lab_number, lab):
    directory = lab.get("directory")
    if directory is None:
        return root / ("ЛР%d" % lab_number)
    if not isinstance(directory, str) or not directory.strip():
        fail("labs.%d.directory должен быть непустой строкой" % lab_number)
    return relative_path(root, directory, "directory", lab_number)


def report_number_for(lab_number, lab):
    report_number = lab.get("report_number", lab_number)
    if isinstance(report_number, bool) or not isinstance(report_number, int):
        fail("labs.%d.report_number должен быть целым числом" % lab_number)
    return report_number


def paths_for(root, lab_number, lab):
    lab_dir = lab_directory(root, lab_number, lab)
    return (
        resolve(root, lab.get("content", lab_dir / "content.json")),
        resolve(root, lab.get("report", lab_dir / ("Отчет_ЛР%d.docx" % lab_number))),
        resolve(root, lab.get("context", "_context.json")),
    )


def build_report(root, lab_number, lab):
    methodical_guide = lab.get("methodical_guide")
    if methodical_guide is not None:
        if not isinstance(methodical_guide, str) or not methodical_guide.strip():
            fail("labs.%d.methodical_guide должен быть непустой строкой" % lab_number)
        guide_path = relative_path(root, methodical_guide, "methodical_guide", lab_number)
        if not guide_path.is_file():
            fail("не найдена methodical_guide: %s" % guide_path)
    content_path, report_path, context_path = paths_for(root, lab_number, lab)
    if not content_path.is_file():
        fail("не найден content: %s" % content_path)
    with content_path.open(encoding="utf-8-sig") as stream:
        content = json.load(stream)
    if not isinstance(content, list):
        fail("content должен быть JSON-списком: %s" % content_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    build(str(report_path), report_number_for(lab_number, lab), lab["theme"], content,
          str(content_path.parent), load_context(str(context_path)))
    print("REPORT %s" % report_path)


def verify_content_references(content_path):
    """Ensure report JSON does not reference missing source files or images."""
    try:
        with content_path.open(encoding="utf-8-sig") as stream:
            content = json.load(stream)
    except json.JSONDecodeError as exc:
        fail("некорректный JSON в content: %s" % exc)
    if not isinstance(content, list):
        fail("content должен быть JSON-списком: %s" % content_path)
    missing = []
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            fail("content[%d] должен быть объектом" % index)
        for key in ("codefile", "img"):
            if key not in item:
                continue
            value = item[key]
            if not isinstance(value, str) or not value.strip():
                fail("content[%d].%s должен быть непустой строкой" % (index, key))
            path = Path(value)
            if path.is_absolute():
                fail("content[%d].%s должен быть относительным путём" % (index, key))
            resolved = content_path.parent / path
            if not resolved.is_file():
                missing.append(str(resolved))
            else:
                print("OK %s" % resolved)
    if missing:
        fail("не найдены ссылки из content: %s" % "; ".join(missing))


def verify_report(root, lab_number, lab):
    content_path, report_path, _ = paths_for(root, lab_number, lab)
    if not content_path.is_file():
        fail("не найден content: %s" % content_path)
    verify_content_references(content_path)
    artifacts = lab.get("artifacts", [str(report_path)])
    if not isinstance(artifacts, list) or not artifacts:
        fail("labs.%d.artifacts должен быть непустым списком" % lab_number)
    missing = []
    for artifact in artifacts:
        if not isinstance(artifact, str) or not artifact.strip():
            fail("labs.%d.artifacts должен содержать непустые строки" % lab_number)
        artifact_path = Path(artifact)
        if artifact_path.is_absolute():
            fail("labs.%d.artifacts должен содержать относительные пути" % lab_number)
        resolved = root / artifact_path
        if not resolved.exists():
            missing.append(str(resolved))
        else:
            print("OK %s" % resolved)
    if missing:
        fail("не найдены артефакты: %s" % "; ".join(missing))


def required_file(root, path, label):
    if not path.is_file():
        fail("не найден %s: %s" % (label, path))
    print("OK %s" % path)


def verify_docx(path, label):
    required_file(None, path, label)
    try:
        with zipfile.ZipFile(path) as archive:
            if "word/document.xml" not in archive.namelist():
                fail("%s не содержит word/document.xml: %s" % (label, path))
    except zipfile.BadZipFile:
        fail("%s не является корректным DOCX: %s" % (label, path))
    print("DOCX %s" % path)


def verify_pdf(path):
    required_file(None, path, "PDF-отчёт")
    if path.read_bytes()[:4] != b"%PDF":
        fail("PDF-отчёт не начинается с сигнатуры %%PDF: %s" % path)
    print("PDF %s" % path)


def quality_gate(root, lab_number, lab):
    """Check report inputs and all mandatory final documents without rebuilding them."""
    verify_report(root, lab_number, lab)
    _, report_path, _ = paths_for(root, lab_number, lab)
    lab_dir = lab_directory(root, lab_number, lab)
    pdf_path = resolve(root, lab.get("pdf", lab_dir / ("Отчет_ЛР%d.pdf" % lab_number)))
    text_path = resolve(root, lab.get("text", lab_dir / "text.docx"))
    verify_docx(report_path, "DOCX-отчёт")
    verify_pdf(pdf_path)
    verify_docx(text_path, "конспект text.docx")
    print("QUALITY GATE PASSED: lab %d" % lab_number)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="корень проекта; по умолчанию текущий каталог")
    parser.add_argument("--lab", type=int, required=True, help="номер ЛР")
    parser.add_argument("--action", required=True,
                        choices=("build-report", "verify-report", "quality-gate"))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    state = load_state(root)
    lab = get_lab(state, args.lab)
    if args.action == "build-report":
        build_report(root, args.lab, lab)
    elif args.action == "verify-report":
        verify_report(root, args.lab, lab)
    else:
        quality_gate(root, args.lab, lab)


if __name__ == "__main__":
    main()
