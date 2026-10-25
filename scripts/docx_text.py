# -*- coding: utf-8 -*-
"""Extract visible text from a DOCX file without external dependencies.

Usage:
    py docx_text.py guide.docx

Paragraphs are returned in document order. Tables are rendered as Markdown
(`| cell | cell |`), so variant tables from a методичка stay readable instead
of collapsing into a stream of unrelated lines. DOCX is a ZIP package, so this
utility only needs the Python standard library.
"""
import argparse
import sys
import zipfile
import xml.etree.ElementTree as element_tree
from pathlib import Path

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _paragraph_text(paragraph):
    return "".join(node.text or "" for node in paragraph.iter(WORD_NAMESPACE + "t"))


def _cell_text(cell):
    parts = [_paragraph_text(paragraph).strip() for paragraph in cell.findall(WORD_NAMESPACE + "p")]
    return " ".join(part for part in parts if part).replace("|", "\\|")


def _table_markdown(table):
    rows = []
    for row in table.findall(WORD_NAMESPACE + "tr"):
        rows.append([_cell_text(cell) for cell in row.findall(WORD_NAMESPACE + "tc")])
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(padded[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    return "\n".join(lines)


def extract_docx_text(path):
    """Return visible paragraphs and Markdown tables joined by newlines."""
    try:
        with zipfile.ZipFile(path) as archive:
            document = archive.read("word/document.xml")
    except FileNotFoundError:
        raise SystemExit("DOCX не найден: %s" % path)
    except zipfile.BadZipFile:
        raise SystemExit("Файл не является корректным DOCX: %s" % path)
    except KeyError:
        raise SystemExit("DOCX не содержит word/document.xml: %s" % path)

    try:
        root = element_tree.fromstring(document)
    except element_tree.ParseError as exc:
        raise SystemExit("Некорректный word/document.xml в %s: %s" % (path, exc))

    body = root.find(WORD_NAMESPACE + "body")
    if body is None:
        return ""
    blocks = []
    for child in body:
        if child.tag == WORD_NAMESPACE + "p":
            text = _paragraph_text(child)
            if text:
                blocks.append(text)
        elif child.tag == WORD_NAMESPACE + "tbl":
            table = _table_markdown(child)
            if table:
                blocks.append(table)
    return "\n".join(blocks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", help="исходный DOCX")
    args = parser.parse_args()
    text = extract_docx_text(Path(args.docx))
    if text:
        print(text)


if __name__ == "__main__":
    main()
