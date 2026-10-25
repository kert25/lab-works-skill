# -*- coding: utf-8 -*-
"""Extract text from a PDF using the first available backend.

Usage:
    py pdf_text.py <file.pdf> [pages]

`pages` is an optional 1-based inclusive range such as ``1-5`` or a single
page number such as ``3``. Ranges exceeding the document length are clipped
with a warning; malformed or fully out-of-range ranges fail clearly.

Backends, in order of preference:
    1. PyMuPDF (`pymupdf`, then the legacy `fitz` import);
    2. pypdf;
    3. the `pdftotext` executable from Poppler.
If none is installed the command fails with an install hint. This keeps the
skill usable on machines that cannot install PyMuPDF.
"""
import argparse
import shutil
import subprocess
import sys

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _backend():
    """Return (name, module) for the first usable PDF backend."""
    try:
        import pymupdf
        return "pymupdf", pymupdf
    except ImportError:
        pass
    try:
        import fitz  # legacy name of PyMuPDF
        return "fitz", fitz
    except ImportError:
        pass
    try:
        import pypdf
        return "pypdf", pypdf
    except ImportError:
        pass
    if shutil.which("pdftotext"):
        return "pdftotext", None
    return None, None


def parse_pages(value, page_count):
    """Return zero-based page indices for a user-facing 1-based range."""
    if value is None:
        return range(page_count)
    try:
        if "-" in value:
            start_text, end_text = value.split("-", 1)
        else:
            start_text = end_text = value
        start, end = int(start_text), int(end_text)
    except ValueError as exc:
        raise ValueError("диапазон страниц должен быть вида 1-5 или 3") from exc
    if start < 1 or end < start:
        raise ValueError("диапазон страниц должен быть положительным и возрастающим")
    if start > page_count:
        raise ValueError("первая страница диапазона %d больше числа страниц %d" % (start, page_count))
    clipped_end = min(end, page_count)
    if clipped_end != end:
        print("WARNING: диапазон %d-%d ограничен последней страницей %d" % (start, end, page_count), file=sys.stderr)
    return range(start - 1, clipped_end)


def _pdftotext(path):
    result = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode != 0:
        raise SystemExit("pdftotext не смог прочитать %s: %s" % (path, (result.stderr or "").strip()))
    return result.stdout


def page_count(path):
    """Return the number of pages, or None when no backend is installed."""
    name, module = _backend()
    if name in ("pymupdf", "fitz"):
        with module.open(str(path)) as document:
            return len(document)
    if name == "pypdf":
        return len(module.PdfReader(str(path)).pages)
    if name == "pdftotext":
        text = _pdftotext(path)
        chunks = text.split("\f")
        while chunks and not chunks[-1].strip():
            chunks.pop()
        return len(chunks)
    return None


def page_texts(path, pages=None):
    """Yield (1-based page number, text) for the requested pages."""
    name, module = _backend()
    if name is None:
        raise SystemExit(
            "для чтения PDF нужен один из бэкендов: pymupdf (pip install pymupdf), "
            "pypdf (pip install pypdf) или pdftotext в PATH"
        )
    if name in ("pymupdf", "fitz"):
        with module.open(str(path)) as document:
            if not document:
                raise SystemExit("PDF не содержит страниц: %s" % path)
            for index in parse_pages(pages, len(document)):
                yield index + 1, document[index].get_text()
        return
    if name == "pypdf":
        reader = module.PdfReader(str(path))
        total = len(reader.pages)
        if not total:
            raise SystemExit("PDF не содержит страниц: %s" % path)
        for index in parse_pages(pages, total):
            yield index + 1, reader.pages[index].extract_text() or ""
        return

    text = _pdftotext(path)
    chunks = text.split("\f")
    total = len(chunks)
    while total and not chunks[total - 1].strip():
        total -= 1
    if not total:
        raise SystemExit("PDF не содержит страниц: %s" % path)
    for index in parse_pages(pages, total):
        yield index + 1, chunks[index]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="путь к PDF")
    parser.add_argument("pages", nargs="?", help="страница или диапазон страниц, например 1-5")
    args = parser.parse_args(argv)

    try:
        for number, text in page_texts(args.file, args.pages):
            print("===== PAGE %d =====" % number)
            print(text)
    except ValueError as exc:
        raise SystemExit("pages: " + str(exc))


if __name__ == "__main__":
    main()
