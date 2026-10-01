# -*- coding: utf-8 -*-
"""Extract text from PDF with PyMuPDF.

Usage:
    py pdf_text.py <file.pdf> [pages]

`pages` is an optional 1-based inclusive range such as ``1-5`` or a single
page number such as ``3``. Ranges exceeding the document length are clipped
with a warning; malformed or fully out-of-range ranges fail clearly.
"""
import argparse
import sys

import fitz


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="путь к PDF")
    parser.add_argument("pages", nargs="?", help="страница или диапазон страниц, например 1-5")
    args = parser.parse_args(argv)

    with fitz.open(args.file) as document:
        if not document:
            raise SystemExit("PDF не содержит страниц: %s" % args.file)
        try:
            page_indices = parse_pages(args.pages, len(document))
        except ValueError as exc:
            raise SystemExit("pages: " + str(exc))
        for index in page_indices:
            print("===== PAGE %d =====" % (index + 1))
            print(document[index].get_text())


if __name__ == "__main__":
    main()
