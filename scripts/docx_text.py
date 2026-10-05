# -*- coding: utf-8 -*-
"""Extract visible paragraph text from a DOCX file without external dependencies.

Usage:
    py docx_text.py guide.docx

The output contains paragraphs and table-cell text in document order. DOCX is a
ZIP package, so this utility only needs the Python standard library.
"""
import argparse
import sys
import zipfile
import xml.etree.ElementTree as element_tree
from pathlib import Path

WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx_text(path):
    """Return visible document paragraphs joined by newlines."""
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

    paragraphs = []
    for paragraph in root.iter(WORD_NAMESPACE + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(WORD_NAMESPACE + "t"))
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", help="исходный DOCX")
    args = parser.parse_args()
    text = extract_docx_text(Path(args.docx))
    if text:
        print(text)


if __name__ == "__main__":
    main()
