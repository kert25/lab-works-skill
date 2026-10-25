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
    py lab_runner.py --root <project-root> --lab N --action check-screenshot
    py lab_runner.py --root <project-root> --lab N --action capture-cli
    py lab_runner.py --root <project-root> --lab N --action read-guide [--out guide.txt]
    py lab_runner.py --root <project-root> --lab N --action init-text

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
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from docx_text import extract_docx_text
from make_docx import build, load_context

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

VALID_STATUSES = {"not_started", "in_progress", "ready_for_review", "completed"}
VALID_VERIFICATION_STATUSES = {"passed", "failed", "blocked"}
BUNDLED_TOOLS = {
    "lab_runner": "lab_runner.py",
    "inspect_docx": "inspect_docx.py",
    "pdf_text": "pdf_text.py",
    "docx2pdf": "docx2pdf.ps1",
    "cdp": "cdp.js",
    "capture_cdp": "capture_cdp.js",
    "shot": "shot.ps1",
    "docx_text": "docx_text.py",
}
CHECK_NAMES = (
    "content_references", "artifacts", "report_docx", "report_pdf", "text_docx",
    "report_structure", "execution_evidence",
)
MIN_REPORT_SECTIONS = 3
MIN_TEXT_NOTES_CHARS = 300
RESULT_HEADINGS = {
    "результат выполнения",
    "результаты выполнения",
    "результат работы",
    "результаты работы",
}
PLACEHOLDER_MARKERS = (
    "сформулируйте развернутый",
    "сформулируйте развёрнутый",
    "[вопрос",
    "ответ: [",
    "todo",
    "tbd",
    "fixme",
    "заполните",
)
WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
COMMAND_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:[A-Za-z]:[\\/]|/)")


def fail(message):
    raise SystemExit(message)


def load_state(root):
    path = root / "_lab_state.json"
    try:
        with path.open(encoding="utf-8-sig") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        fail("не найден файл состояния: %s (создайте _lab_state.json)" % path)
    except json.JSONDecodeError as exc:
        fail("некорректный JSON в %s: %s" % (path, exc))
    if not isinstance(state, dict) or not isinstance(state.get("labs"), dict):
        fail("ожидается объект с полем labs в %s" % path)
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


def atomic_build_docx(output_path, *build_args, **build_kwargs):
    """Build DOCX beside its destination, then atomically replace it.

    An opened DOCX must never truncate the previous report.  If Windows locks
    the destination, the completed temporary document is retained for recovery.
    """
    output_path = Path(output_path)
    temporary = output_path.with_name(
        ".%s.new-%s.docx" % (output_path.stem, uuid.uuid4().hex)
    )
    try:
        build(str(temporary), *build_args, **build_kwargs)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    try:
        os.replace(temporary, output_path)
    except PermissionError as exc:
        raise SystemExit(
            "не удалось заменить DOCX, вероятно он открыт в Word или другом просмотрщике: %s\n"
            "Закройте документ и повторите команду. Новая собранная копия сохранена: %s" %
            (output_path, temporary)
        ) from exc
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


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
    atomic_build_docx(
        report_path, report_number_for(lab_number, lab), lab["theme"], content,
        str(content_path.parent), load_context(str(context_path)),
    )
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
    atomic_build_docx(
        text_path, report_number_for(lab_number, lab), lab["theme"], content,
        str(content_path.parent), None, title=False,
    )
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


def png_dimensions(path):
    """Return PNG dimensions using only the mandatory IHDR chunk."""
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        fail("доказательство выполнения должно быть корректным PNG: %s" % path)
    return struct.unpack(">II", header[16:24])


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _unfilter_png_rows(raw, width, height, channels):
    """Undo PNG scanline filters; return None for unsupported or damaged data."""
    stride = width * channels
    if len(raw) < height * (stride + 1):
        return None
    rows = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        if filter_type == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                up = previous[index]
                upper_left = previous[index - channels] if index >= channels else 0
                estimate = left + up - upper_left
                distance_left = abs(estimate - left)
                distance_up = abs(estimate - up)
                distance_upper_left = abs(estimate - upper_left)
                if distance_left <= distance_up and distance_left <= distance_upper_left:
                    line[index] = (line[index] + left) & 0xFF
                elif distance_up <= distance_upper_left:
                    line[index] = (line[index] + up) & 0xFF
                else:
                    line[index] = (line[index] + upper_left) & 0xFF
        elif filter_type != 0:
            return None
        rows.append(line)
        previous = line
    return rows


def png_visual_stats(path):
    """Decode a plain 8-bit PNG and report how varied its pixels are.

    Returns (width, height, distinct_colors, non_background_ratio) or None when
    the PNG uses a layout this dependency-free decoder does not support.
    """
    data = Path(path).read_bytes()
    if data[:8] != PNG_SIGNATURE:
        return None
    width = height = depth = color_type = interlace = None
    palette = b""
    compressed = bytearray()
    position = 8
    while position + 8 <= len(data):
        length = struct.unpack(">I", data[position:position + 4])[0]
        kind = data[position + 4:position + 8]
        chunk = data[position + 8:position + 8 + length]
        position += 12 + length
        if kind == b"IHDR":
            width, height, depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
        elif kind == b"PLTE":
            palette = chunk
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break
    channels = PNG_CHANNELS.get(color_type)
    if not channels or depth != 8 or interlace != 0 or not width or not height or not compressed:
        return None
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error:
        return None
    rows = _unfilter_png_rows(raw, width, height, channels)
    if rows is None:
        return None
    row_step = max(1, height // 200)
    column_step = max(1, width // 200)
    colors = {}
    for y in range(0, height, row_step):
        line = rows[y]
        for x in range(0, width, column_step):
            index = x * channels
            if color_type == 3:
                entry = line[index]
                if palette and entry * 3 + 2 < len(palette):
                    color = (palette[entry * 3] >> 3, palette[entry * 3 + 1] >> 3, palette[entry * 3 + 2] >> 3)
                else:
                    color = (entry >> 3, entry >> 3, entry >> 3)
            elif color_type in (0, 4):
                value = line[index] >> 3
                color = (value, value, value)
            else:
                color = (line[index] >> 3, line[index + 1] >> 3, line[index + 2] >> 3)
            colors[color] = colors.get(color, 0) + 1
    sampled = sum(colors.values())
    if not sampled:
        return None
    dominant = max(colors.values())
    return width, height, len(colors), 1.0 - dominant / sampled


def check_screenshot(path):
    """Validate PNG evidence: signature, minimum size and a non-blank frame."""
    path = Path(path)
    width, height = png_dimensions(path)
    if width < 640 or height < 360:
        fail("PNG-доказательство слишком мало (%dx%d, минимум 640x360): %s" %
             (width, height, path))
    stats = png_visual_stats(path)
    if stats is None:
        print("WARNING: PNG не удалось декодировать для проверки на пустоту: %s" % path)
        return
    _, _, colors, non_background = stats
    if colors < 2 or non_background < 0.002:
        fail("PNG-доказательство пустое: кадр одноцветный, содержимого нет: %s" % path)
    print("SCREENSHOT OK %s (%dx%d, цветов %d, не фон %.1f%%)" %
          (path, width, height, colors, non_background * 100))


def report_has_executable_content(content, lab):
    """A report is executable when it ships code or declares a CLI demonstration."""
    if any(isinstance(item, dict) and ("code" in item or "codefile" in item) for item in content):
        return True
    commands = lab.get("commands") if isinstance(lab, dict) else None
    return (isinstance(commands, dict) and isinstance(commands.get("cli_evidence"), str)
            and bool(commands["cli_evidence"].strip()))


def execution_evidence_images(content_path, lab=None):
    """Return evidence images required by code/CLI sections, or none for text-only reports."""
    with content_path.open(encoding="utf-8-sig") as stream:
        content = json.load(stream)
    has_code = report_has_executable_content(content, lab or {})
    result_positions = [
        index for index, item in enumerate(content)
        if isinstance(item, dict)
        and str(item.get("h", "")).strip().casefold() in RESULT_HEADINGS
    ]
    if not has_code:
        return []
    if not result_positions:
        fail("в отчёте есть код или cli_evidence, но нет раздела «Результат выполнения» со скриншотом")
    images = []
    for index in result_positions:
        following = content[index + 1] if index + 1 < len(content) else None
        if not isinstance(following, dict) or "img" not in following:
            fail("раздел «Результат выполнения» должен сразу содержать img с фактическим PNG запуска")
        images.append(content_path.parent / following["img"])
    return images


def verify_execution_evidence(content_path, lab):
    """Require a valid, non-blank PNG after a result section when the report is executable."""
    for image in execution_evidence_images(content_path, lab):
        if image.suffix.casefold() != ".png":
            fail("доказательство выполнения должно быть PNG: %s" % image)
        check_screenshot(image)
        print("EXECUTION EVIDENCE %s" % image)


def check_report_screenshots(root, lab_number, lab):
    """Validate evidence PNGs without opening windows or stealing focus."""
    content_path, _, _ = paths_for(root, lab_number, lab)
    if not content_path.is_file():
        fail("не найден content: %s" % content_path)
    images = execution_evidence_images(content_path, lab)
    if not images:
        print("SCREENSHOT CHECK: no executable evidence required")
        return
    for image in images:
        if image.suffix.casefold() != ".png":
            fail("доказательство выполнения должно быть PNG: %s" % image)
        check_screenshot(image)


def capture_cli_evidence(root, lab_number, lab, out_path=None):
    """Capture CLI evidence through the bundled headless renderer."""
    commands = lab.get("commands")
    if not isinstance(commands, dict) or not isinstance(commands.get("cli_evidence"), str):
        fail("для capture-cli укажите labs.%d.commands.cli_evidence" % lab_number)
    content_path, _, _ = paths_for(root, lab_number, lab)
    images = execution_evidence_images(content_path, lab)
    if out_path:
        image = resolve(root, out_path)
        declared = {item.resolve() for item in images}
        if declared and image.resolve() not in declared:
            fail("capture-cli: --out %s не указан среди img в content.json" % out_path)
    elif len(images) == 1:
        image = images[0]
    else:
        fail("capture-cli: в отчёте %d разделов «Результат выполнения»; укажите --out для нужного PNG" % len(images))
    if image.suffix.casefold() != ".png":
        fail("img доказательства должен быть PNG: %s" % image)
    image.parent.mkdir(parents=True, exist_ok=True)
    launcher = Path(__file__).resolve().parent / "capture_cdp.js"
    if not launcher.is_file():
        fail("не найден инструмент capture_cdp.js: %s" % launcher)
    try:
        subprocess.run(
            ["node", str(launcher), "--cli", commands["cli_evidence"], "--out", str(image)],
            cwd=root, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit("не удалось снять CLI-доказательство через capture_cdp: %s" % error)
    check_screenshot(image)
    print("CLI EVIDENCE %s" % image)


def verify_report(root, lab_number, lab):
    content_path, report_path, _ = paths_for(root, lab_number, lab)
    if not content_path.is_file():
        fail("не найден content: %s" % content_path)
    verify_content_references(content_path)
    verify_execution_evidence(content_path, lab)
    artifacts = lab.get("artifacts", [str(report_path.relative_to(root))])
    if not isinstance(artifacts, list) or not artifacts:
        fail("labs.%d.artifacts должен быть непустым списком; добавляйте артефакты по мере создания" % lab_number)
    missing = []
    for artifact in artifacts:
        normalized = normalized_relative_path(artifact)
        if normalized is None:
            fail("labs.%d.artifacts должен содержать относительные пути" % lab_number)
        resolved = root / normalized
        if Path(normalized).name == "quality_gate.json":
            continue
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


def docx_element_count(path, local_name):
    """Count elements such as w:drawing or w:tbl inside the DOCX body."""
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    body = root.find(namespace + "body")
    if body is None:
        return 0
    return len(body.findall(".//" + namespace + local_name))


def pdf_page_count(path):
    """Return the page count through PyMuPDF, or None when the library is absent."""
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            return None
    try:
        with fitz.open(str(path)) as document:
            return len(document)
    except Exception as error:  # noqa: BLE001 - any parser failure means a broken PDF
        fail("PDF не читается PyMuPDF: %s" % error)


def collapsed_text(value):
    return re.sub(r"\s+", "", value)


def verify_report_structure(root, lab_number, lab):
    """Check report substance: sections, figures, tables, notes and PDF pages.

    Returns warnings for checks that this environment could not perform.
    """
    warnings = []
    content_path, report_path, _ = paths_for(root, lab_number, lab)
    _, text_path = text_paths_for(root, lab_number, lab)
    pdf_path = pdf_path_for(root, lab_number, lab)
    try:
        with content_path.open(encoding="utf-8-sig") as stream:
            content = json.load(stream)
    except json.JSONDecodeError as exc:
        fail("некорректный JSON в content: %s" % exc)
    headings = [item["h"] for item in content
                if isinstance(item, dict) and isinstance(item.get("h"), str)]
    paragraphs = [item["p"] for item in content
                  if isinstance(item, dict) and isinstance(item.get("p"), str)]
    if len(headings) < MIN_REPORT_SECTIONS:
        fail("в отчёте %d раздел(ов); нужно минимум %d содержательных раздела "
             "(цель, задание, ход работы, результат)" % (len(headings), MIN_REPORT_SECTIONS))
    if len(paragraphs) < 2:
        fail("в отчёте меньше двух абзацев текста: отчёт не может состоять только из заголовков")
    report_text = extract_docx_text(report_path)
    position = 0
    for heading in headings:
        index = report_text.find(heading, position)
        if index < 0:
            fail("раздел «%s» не найден в DOCX или нарушен порядок разделов" % heading)
        position = index
    images = sum(1 for item in content if isinstance(item, dict) and "img" in item)
    drawings = docx_element_count(report_path, "drawing")
    if drawings != images:
        fail("в DOCX %d рисунков, а в content.json — %d; проверьте ссылки img" %
             (drawings, images))
    tables = sum(1 for item in content if isinstance(item, dict) and "table" in item)
    docx_tables = docx_element_count(report_path, "tbl")
    if docx_tables != tables:
        fail("в DOCX %d таблиц, а в content.json — %d" % (docx_tables, tables))
    notes = extract_docx_text(text_path)
    collapsed_notes = collapsed_text(notes)
    if len(collapsed_notes) < MIN_TEXT_NOTES_CHARS:
        fail("text.docx слишком короткий для конспекта защиты (%d символов без пробелов, "
             "нужно минимум %d)" % (len(collapsed_notes), MIN_TEXT_NOTES_CHARS))
    lowered_notes = notes.casefold()
    for marker in PLACEHOLDER_MARKERS:
        if marker in lowered_notes:
            fail("в text.docx остались заглушки конспекта: «%s»" % marker)
    pages = pdf_page_count(pdf_path)
    if pages is None:
        warnings.append("PyMuPDF недоступен: число страниц PDF не проверено")
    elif pages < 2:
        fail("PDF содержит %d страницу: собран только титульный лист" % pages)
    print("STRUCTURE OK: разделов %d, рисунков %d, таблиц %d, страниц PDF %s" %
          (len(headings), images, tables, pages if pages is not None else "?"))
    return warnings


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

def write_quality_record(root, lab_number, lab, report_path, pdf_path, text_path, warnings=None):
    lab_dir = lab_directory(root, lab_number, lab)
    lab_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 3,
        "lab": lab_number,
        "report_number": report_number_for(lab_number, lab),
        "status": "passed",
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "checks": list(CHECK_NAMES),
        "warnings": list(warnings or []),
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

    state_path = root / "_lab_state.json"
    if state_path.is_file():
        try:
            with state_path.open("r", encoding="utf-8-sig") as stream:
                state_data = json.load(stream)
            lab_key = str(lab_number)
            if isinstance(state_data.get("labs"), dict) and lab_key in state_data["labs"]:
                cur_lab = state_data["labs"][lab_key]
                rel_qgate = relative_to_root(root, output)
                artifacts = cur_lab.setdefault("artifacts", [])
                if rel_qgate not in artifacts:
                    artifacts.append(rel_qgate)
                cur_lab["verification"] = {
                    "status": "passed",
                    "checked_at": record["checked_at"],
                    "checks": list(CHECK_NAMES),
                    "warnings": list(record["warnings"]),
                }
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=root, delete=False,
                                                 prefix="._lab_state-", suffix=".tmp") as stream:
                    json.dump(state_data, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    temp_state = Path(stream.name)
                try:
                    os.replace(temp_state, state_path)
                finally:
                    temp_state.unlink(missing_ok=True)
                print("STATE UPDATED: lab %d verification -> passed" % lab_number)
        except Exception as exc:
            print("WARNING: не удалось обновить _lab_state.json: %s" % exc, file=sys.stderr)


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
    if isinstance(record, dict) and record.get("status") != "passed":
        fail("quality gate record is not a passed gate")
    actual = record.get("sha256") if isinstance(record, dict) else None
    expected = quality_hashes(root, lab_number, lab, report_path, pdf_path, text_path)
    if not isinstance(actual, dict):
        fail("quality gate record has no SHA-256 manifest; run quality-gate again")
    if actual != expected:
        fail("quality gate record is stale; tracked files changed after the last quality-gate")
    print("QUALITY RECORD CURRENT: lab %d" % lab_number)

def quality_gate(root, lab_number, lab):
    """Check report inputs, substance and documents; then record a successful gate."""
    verify_report(root, lab_number, lab)
    _, report_path, _ = paths_for(root, lab_number, lab)
    pdf_path = pdf_path_for(root, lab_number, lab)
    _, text_path = text_paths_for(root, lab_number, lab)
    verify_docx(report_path, "DOCX-отчёт")
    verify_pdf(pdf_path)
    verify_docx(text_path, "конспект text.docx")
    warnings = verify_report_structure(root, lab_number, lab)
    write_quality_record(root, lab_number, lab, report_path, pdf_path, text_path, warnings)
    print("QUALITY GATE PASSED: lab %d" % lab_number)


def extract_guide_text(guide_path):
    ext = guide_path.suffix.lower()
    if ext == ".docx":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from docx_text import extract_docx_text
        return extract_docx_text(guide_path)
    elif ext == ".pdf":
        try:
            import pymupdf as fitz
        except ImportError:
            try:
                import fitz
            except ImportError:
                fail("для чтения PDF-методички требуется библиотека PyMuPDF (pymupdf)")
        with fitz.open(guide_path) as doc:
            return "\n".join(page.get_text() for page in doc)
    else:
        try:
            return guide_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return guide_path.read_text(encoding="cp1251", errors="replace")


def read_guide(root, lab_number, lab, out_path=None):
    """Extract and display/save text from the methodical guide (DOCX/PDF)."""
    guide = lab.get("methodical_guide")
    if not guide:
        fail("в labs.%d не указано поле methodical_guide" % lab_number)
    normalized = normalized_relative_path(guide)
    if not normalized:
        fail("labs.%d.methodical_guide должен быть относительным путём: %s" % (lab_number, guide))
    guide_path = root / normalized
    if not guide_path.is_file():
        fail("не найден файл методички: %s" % guide_path)

    text = extract_guide_text(guide_path)
    if out_path:
        target = Path(out_path)
        if not target.is_absolute():
            target = root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print("GUIDE SAVED: %s (%d символов)" % (target, len(text)))
    else:
        print(text)
    return text


def extract_review_questions(guide_text):
    """Find review questions section in guide text and return a list of extracted questions."""
    match = re.search(
        r"(?:контрольные\s+вопросы|вопросы\s+для\s+самопроверки|вопросы\s+к\s+защите|вопросы\s+для\s+защиты)[:\s]*(.*?)(?:\n\s*\n\s*(?:[А-ЯA-Z]{3,}|список|литература)|\Z)",
        guide_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    block = match.group(1).strip()
    questions = []
    current_q = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(?:\d+[\.\)]|[а-яa-z][\.\)])\s*(.+)$", line, re.IGNORECASE)
        if m:
            if current_q:
                questions.append(" ".join(" ".join(current_q).split()))
            current_q = [m.group(1)]
        elif current_q:
            current_q.append(line)
    if current_q:
        questions.append(" ".join(" ".join(current_q).split()))
    return [q for q in questions if len(q) > 3]


def init_text(root, lab_number, lab):
    """Create a starter content_text.json with extracted questions from methodical_guide."""
    text_content_path, _ = text_paths_for(root, lab_number, lab)
    if text_content_path.is_file():
        print("EXISTS %s" % text_content_path)
        return

    questions = []
    guide = lab.get("methodical_guide")
    if guide:
        normalized = normalized_relative_path(guide)
        if normalized:
            guide_path = root / normalized
            if guide_path.is_file():
                try:
                    guide_text = extract_guide_text(guide_path)
                    questions = extract_review_questions(guide_text)
                except Exception as exc:
                    print("WARNING: не удалось извлечь вопросы из методички: %s" % exc, file=sys.stderr)

    theme = lab.get("theme", "Лабораторная работа")
    items = [
        {"h": "1. Краткая суть работы и архитектура"},
        {"p": "В рамках работы по теме «%s» реализовано..." % theme},
        {"h": "2. Порядок демонстрации работы преподавателю"},
        {"p": "1. Запуск программы: ...\n2. Проверка ключевых функций: ...\n3. Демонстрация граничных условий: ..."},
        {"h": "3. Ответы на контрольные вопросы методички"},
    ]

    if questions:
        for idx, q in enumerate(questions, 1):
            items.append({"p": "Вопрос %d: %s" % (idx, q)})
            items.append({"p": "Ответ: [Сформулируйте развернутый академический ответ]"})
    else:
        items.append({"p": "Вопрос 1: [Вопрос из методички]"})
        items.append({"p": "Ответ: [Сформулируйте ответ]"})

    text_content_path.parent.mkdir(parents=True, exist_ok=True)
    with text_content_path.open("w", encoding="utf-8") as stream:
        json.dump(items, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print("INITIALIZED %s (%d элементов)" % (text_content_path, len(items)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="корень проекта; по умолчанию текущий каталог")
    parser.add_argument("--lab", type=int, required=True, help="номер ЛР")
    parser.add_argument(
        "--action", required=True,
        choices=(
            "validate-state", "build-report", "build-text", "build-pdf", "inspect-documents",
            "prepare-report", "verify-report", "quality-gate", "verify-quality",
            "check-screenshot", "capture-cli", "read-guide", "init-text",
        ),
    )
    parser.add_argument("--out", help="файл вывода read-guide или PNG для capture-cli")
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
    elif args.action == "check-screenshot":
        check_report_screenshots(root, args.lab, lab)
    elif args.action == "capture-cli":
        capture_cli_evidence(root, args.lab, lab, args.out)
    elif args.action == "read-guide":
        read_guide(root, args.lab, lab, args.out)
    elif args.action == "init-text":
        init_text(root, args.lab, lab)
    else:
        quality_gate(root, args.lab, lab)


if __name__ == "__main__":
    main()
