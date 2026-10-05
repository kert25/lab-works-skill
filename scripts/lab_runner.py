# -*- coding: utf-8 -*-
"""ASCII-safe runner for reports described by _lab_state.json.

Usage:
    py lab_runner.py --root <project-root> --lab N --action validate-state
    py lab_runner.py --root <project-root> --lab N --action build-report
    py lab_runner.py --root <project-root> --lab N --action build-text
    py lab_runner.py --root <project-root> --lab N --action build-pdf
    py lab_runner.py --root <project-root> --lab N --action inspect-documents
    py lab_runner.py --root <project-root> --lab N --action prepare-report
    py lab_runner.py --root <project-root> --lab N --action verify-report
    py lab_runner.py --root <project-root> --lab N --action quality-gate

Theme names and Cyrillic paths are read from UTF-8 JSON instead of shell
arguments. The selected lab record must contain `theme`; report content and
output paths default to the standard layout when omitted.
"""
import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from make_docx import build, load_context

VALID_STATUSES = {"not_started", "in_progress", "ready_for_review", "completed"}
VALID_VERIFICATION_STATUSES = {"passed", "failed", "blocked"}
BUNDLED_TOOLS = {
    "lab_runner": "lab_runner.py",
    "inspect_docx": "inspect_docx.py",
    "pdf_text": "pdf_text.py",
    "docx2pdf": "docx2pdf.ps1",
    "cdp": "cdp.js",
    "shot": "shot.ps1",
    "docx_text": "docx_text.py",
}
CHECK_NAMES = ("content_references", "artifacts", "report_docx", "report_pdf", "text_docx")
WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
COMMAND_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:[A-Za-z]:[\\/]|/)")


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


def is_absolute_path(value):
    return Path(value).is_absolute() or bool(WINDOWS_ABSOLUTE.match(value)) or value.startswith("\\\\")


def normalized_relative_path(value):
    if not isinstance(value, str) or not value.strip():
        return None
    if is_absolute_path(value):
        return None
    normalized = posixpath.normpath(value.replace("\\", "/"))
    if normalized in (".", "") or normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def resolve(root, value):
    path = Path(value)
    return path if path.is_absolute() else root / path


def relative_path(root, value, field, lab_number):
    if normalized_relative_path(value) is None:
        fail("labs.%d.%s должен быть относительным путём" % (lab_number, field))
    return root / value


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


def text_paths_for(root, lab_number, lab):
    """Return the input JSON and output DOCX paths for defence notes."""
    lab_dir = lab_directory(root, lab_number, lab)
    return (
        resolve(root, lab.get("text_content", lab_dir / "content_text.json")),
        resolve(root, lab.get("text", lab_dir / "text.docx")),
    )


def pdf_path_for(root, lab_number, lab):
    lab_dir = lab_directory(root, lab_number, lab)
    return resolve(root, lab.get("pdf", lab_dir / ("Отчет_ЛР%d.pdf" % lab_number)))


def validate_string_map(value, field, lab_number, require_portable_commands=False):
    if not isinstance(value, dict):
        fail("labs.%d.%s должен быть объектом" % (lab_number, field))
    for key, command in value.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(command, str) or not command.strip():
            fail("labs.%d.%s должен содержать непустые строковые ключи и значения" %
                 (lab_number, field))
        if require_portable_commands and COMMAND_ABSOLUTE_PATH.search(command):
            fail("labs.%d.commands не должен содержать абсолютный путь: %s" %
                 (lab_number, command))


def resolve_bundled_tool(reference, lab_number, key):
    if not isinstance(reference, str) or not reference.startswith("bundled:"):
        fail("labs.%d.tooling.%s должен иметь вид bundled:<tool>" % (lab_number, key))
    name = reference[len("bundled:"):]
    filename = BUNDLED_TOOLS.get(name)
    if filename is None:
        fail("labs.%d.tooling.%s содержит неизвестный bundled-инструмент: %s" %
             (lab_number, key, name))
    path = Path(__file__).resolve().parent / filename
    if not path.is_file():
        fail("bundled-инструмент не найден: %s" % path)
    return path


def validate_verification(value, lab_number):
    if not isinstance(value, dict):
        fail("labs.%d.verification должен быть объектом" % lab_number)
    if value.get("status") not in VALID_VERIFICATION_STATUSES:
        fail("labs.%d.verification.status должен быть одним из: %s" %
             (lab_number, ", ".join(sorted(VALID_VERIFICATION_STATUSES))))
    checked_at = value.get("checked_at")
    if not isinstance(checked_at, str) or not checked_at.endswith("Z"):
        fail("labs.%d.verification.checked_at должен быть UTC ISO 8601 строкой с Z" % lab_number)
    for field in ("checks", "warnings"):
        items = value.get(field)
        if not isinstance(items, list) or not all(isinstance(item, str) and item.strip() for item in items):
            fail("labs.%d.verification.%s должен быть списком непустых строк" %
                 (lab_number, field))


def validate_lab_state(root, lab_number, lab):
    """Validate portable state metadata without requiring report outputs."""
    get_lab({"labs": {str(lab_number): lab}}, lab_number)
    for field in ("directory", "methodical_guide", "content", "report", "context", "pdf", "text", "text_content"):
        if field in lab and normalized_relative_path(lab[field]) is None:
            fail("labs.%d.%s должен быть относительным путём" % (lab_number, field))
    artifacts = lab.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, list) or not artifacts:
            fail("labs.%d.artifacts должен быть непустым списком" % lab_number)
        normalized = []
        for artifact in artifacts:
            path = normalized_relative_path(artifact)
            if path is None:
                fail("labs.%d.artifacts должен содержать относительные пути" % lab_number)
            normalized.append(path.casefold())
        if len(normalized) != len(set(normalized)):
            fail("labs.%d.artifacts содержит дублирующиеся пути" % lab_number)
    if "commands" in lab:
        validate_string_map(lab["commands"], "commands", lab_number, require_portable_commands=True)
    if "command_templates" in lab:
        validate_string_map(lab["command_templates"], "command_templates", lab_number)
    tooling = lab.get("tooling")
    if tooling is not None:
        if not isinstance(tooling, dict):
            fail("labs.%d.tooling должен быть объектом" % lab_number)
        for key, reference in tooling.items():
            if not isinstance(key, str) or not key.strip():
                fail("labs.%d.tooling должен содержать непустые строковые ключи" % lab_number)
            resolved = resolve_bundled_tool(reference, lab_number, key)
            print("TOOL %s -> %s" % (key, resolved))
    if "verification" in lab:
        validate_verification(lab["verification"], lab_number)


def validate_state(root, lab_number, state, lab):
    if not isinstance(state.get("labs"), dict):
        fail("поле labs должно быть объектом")
    validate_lab_state(root, lab_number, lab)
    print("STATE VALID: lab %d" % lab_number)


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


def build_text(root, lab_number, lab):
    """Build text.docx from content_text.json without a title page."""
    content_path, text_path = text_paths_for(root, lab_number, lab)
    if not content_path.is_file():
        fail("не найден content_text: %s" % content_path)
    try:
        with content_path.open(encoding="utf-8-sig") as stream:
            content = json.load(stream)
    except json.JSONDecodeError as exc:
        fail("некорректный JSON в content_text: %s" % exc)
    if not isinstance(content, list):
        fail("content_text должен быть JSON-списком: %s" % content_path)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    build(str(text_path), report_number_for(lab_number, lab), lab["theme"], content,
          str(content_path.parent), None, title=False)
    print("TEXT %s" % text_path)


def build_pdf(root, lab_number, lab):
    """Convert the report to PDF through Word COM or a LibreOffice fallback."""
    _, report_path, _ = paths_for(root, lab_number, lab)
    pdf_path = pdf_path_for(root, lab_number, lab)
    required_file(None, report_path, "DOCX-отчёт")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    scripts_dir = Path(__file__).resolve().parent
    word_command = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(scripts_dir / "docx2pdf.ps1"), "-Docx", str(report_path), "-Pdf", str(pdf_path),
    ]
    try:
        subprocess.run(word_command, cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError) as word_error:
        soffice = shutil.which("soffice") or shutil.which("soffice.exe")
        if not soffice:
            raise SystemExit(
                "не удалось преобразовать DOCX в PDF через Word COM (%s); "
                "Word и LibreOffice недоступны" % word_error
            )
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(pdf_path.parent), str(report_path)],
                cwd=root, check=True,
            )
        except (OSError, subprocess.CalledProcessError) as libre_error:
            raise SystemExit("не удалось преобразовать DOCX в PDF через LibreOffice: %s" % libre_error)
        generated = pdf_path.parent / (report_path.stem + ".pdf")
        if generated != pdf_path and generated.is_file():
            os.replace(generated, pdf_path)
    verify_pdf(pdf_path)


def inspect_documents(root, lab_number, lab):
    """Inspect report/notes DOCX structure and print extracted report PDF text."""
    _, report_path, _ = paths_for(root, lab_number, lab)
    _, text_path = text_paths_for(root, lab_number, lab)
    pdf_path = pdf_path_for(root, lab_number, lab)
    scripts_dir = Path(__file__).resolve().parent
    for script, path, extra in (
        ("inspect_docx.py", report_path, ["--summary"]),
        ("inspect_docx.py", text_path, ["--summary"]),
        ("pdf_text.py", pdf_path, []),
    ):
        print("--- %s: %s ---" % (script, path.name))
        try:
            subprocess.run([sys.executable, str(scripts_dir / script), str(path), *extra], cwd=root, check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise SystemExit("не удалось выполнить %s для %s: %s" % (script, path, error))


def prepare_report(root, lab_number, lab):
    """Run the complete reproducible build, inspection and quality-gate cycle."""
    build_report(root, lab_number, lab)
    build_text(root, lab_number, lab)
    build_pdf(root, lab_number, lab)
    verify_report(root, lab_number, lab)
    inspect_documents(root, lab_number, lab)
    quality_gate(root, lab_number, lab)


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
            if normalized_relative_path(value) is None:
                fail("content[%d].%s должен быть относительным путём" % (index, key))
            resolved = content_path.parent / value
            if not resolved.is_file():
                missing.append(str(resolved))
            else:
                print("OK %s" % resolved)
    if missing:
        fail("не найдены ссылки из content: %s" % "; ".join(missing))


def verify_execution_evidence(content_path):
    """Require a real PNG after a result section when code is reported."""
    with content_path.open(encoding="utf-8-sig") as stream:
        content = json.load(stream)
    has_code = any(isinstance(item, dict) and "codefile" in item for item in content)
    result_positions = [index for index, item in enumerate(content)
                        if isinstance(item, dict) and item.get("h", "").casefold() == "результат выполнения"]
    if not has_code or not result_positions:
        return
    for index in result_positions:
        following = content[index + 1] if index + 1 < len(content) else None
        if not isinstance(following, dict) or "img" not in following:
            fail("раздел «Результат выполнения» должен сразу содержать img с фактическим PNG запуска")
        image = content_path.parent / following["img"]
        if image.suffix.casefold() != ".png" or image.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            fail("доказательство выполнения должно быть корректным PNG: %s" % image)
        print("EXECUTION EVIDENCE %s" % image)


def verify_report(root, lab_number, lab):
    content_path, report_path, _ = paths_for(root, lab_number, lab)
    if not content_path.is_file():
        fail("не найден content: %s" % content_path)
    verify_content_references(content_path)
    verify_execution_evidence(content_path)
    artifacts = lab.get("artifacts", [str(report_path.relative_to(root))])
    if not isinstance(artifacts, list) or not artifacts:
        fail("labs.%d.artifacts должен быть непустым списком; добавляйте артефакты по мере создания" % lab_number)
    missing = []
    for artifact in artifacts:
        normalized = normalized_relative_path(artifact)
        if normalized is None:
            fail("labs.%d.artifacts должен содержать относительные пути" % lab_number)
        resolved = root / normalized
        if not resolved.exists():
            missing.append(str(resolved))
        else:
            print("OK %s" % resolved)
    if missing:
        fail("не найдены артефакты (добавляйте их по мере создания): %s" % "; ".join(missing))


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


def relative_to_root(root, path):
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        fail("файл quality gate находится вне корня проекта: %s" % path)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def quality_hashes(root, lab_number, lab, report_path, pdf_path, text_path):
    content_path, _, context_path = paths_for(root, lab_number, lab)
    quality_path = lab_directory(root, lab_number, lab) / "quality_gate.json"
    candidates = [content_path, context_path, report_path, pdf_path, text_path]
    for artifact in lab.get("artifacts", []):
        path = root / normalized_relative_path(artifact)
        if path != quality_path and path.is_file():
            candidates.append(path)
    existing = [path for path in set(candidates) if path.is_file()]
    return {relative_to_root(root, path): sha256_file(path) for path in sorted(existing)}

def write_quality_record(root, lab_number, lab, report_path, pdf_path, text_path):
    lab_dir = lab_directory(root, lab_number, lab)
    lab_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 2,
        "lab": lab_number,
        "report_number": report_number_for(lab_number, lab),
        "status": "passed",
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "checks": list(CHECK_NAMES),
        "files": [
            relative_to_root(root, report_path),
            relative_to_root(root, pdf_path),
            relative_to_root(root, text_path),
        ],
        "sha256": quality_hashes(root, lab_number, lab, report_path, pdf_path, text_path),
    }
    output = lab_dir / "quality_gate.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=lab_dir, delete=False,
                                     prefix=".quality_gate-", suffix=".tmp") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    try:
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    print("QUALITY RECORD %s" % output)


def verify_quality_record(root, lab_number, lab):
    """Verify that a quality record exists and still matches all tracked files."""
    _, report_path, _ = paths_for(root, lab_number, lab)
    lab_dir = lab_directory(root, lab_number, lab)
    pdf_path = pdf_path_for(root, lab_number, lab)
    _, text_path = text_paths_for(root, lab_number, lab)
    record_path = lab_dir / "quality_gate.json"
    required_file(None, record_path, "quality gate record")
    try:
        with record_path.open(encoding="utf-8-sig") as stream:
            record = json.load(stream)
    except json.JSONDecodeError as exc:
        fail("quality gate record has invalid JSON: %s" % exc)
    actual = record.get("sha256") if isinstance(record, dict) else None
    expected = quality_hashes(root, lab_number, lab, report_path, pdf_path, text_path)
    if not isinstance(actual, dict):
        fail("quality gate record has no SHA-256 manifest; run quality-gate again")
    if actual != expected:
        fail("quality gate record is stale; tracked files changed after the last quality-gate")
    print("QUALITY RECORD CURRENT: lab %d" % lab_number)

def quality_gate(root, lab_number, lab):
    """Check report inputs and mandatory documents; then record a successful gate."""
    verify_report(root, lab_number, lab)
    _, report_path, _ = paths_for(root, lab_number, lab)
    pdf_path = pdf_path_for(root, lab_number, lab)
    _, text_path = text_paths_for(root, lab_number, lab)
    verify_docx(report_path, "DOCX-отчёт")
    verify_pdf(pdf_path)
    verify_docx(text_path, "конспект text.docx")
    write_quality_record(root, lab_number, lab, report_path, pdf_path, text_path)
    print("QUALITY GATE PASSED: lab %d" % lab_number)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="корень проекта; по умолчанию текущий каталог")
    parser.add_argument("--lab", type=int, required=True, help="номер ЛР")
    parser.add_argument(
        "--action", required=True,
        choices=(
            "validate-state", "build-report", "build-text", "build-pdf", "inspect-documents",
            "prepare-report", "verify-report", "quality-gate", "verify-quality",
        ),
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    state = load_state(root)
    lab = get_lab(state, args.lab)
    if args.action == "validate-state":
        validate_state(root, args.lab, state, lab)
    elif args.action == "build-report":
        build_report(root, args.lab, lab)
    elif args.action == "build-text":
        build_text(root, args.lab, lab)
    elif args.action == "build-pdf":
        build_pdf(root, args.lab, lab)
    elif args.action == "inspect-documents":
        inspect_documents(root, args.lab, lab)
    elif args.action == "prepare-report":
        prepare_report(root, args.lab, lab)
    elif args.action == "verify-report":
        verify_report(root, args.lab, lab)
    elif args.action == "verify-quality":
        verify_quality_record(root, args.lab, lab)
    else:
        quality_gate(root, args.lab, lab)


if __name__ == "__main__":
    main()
