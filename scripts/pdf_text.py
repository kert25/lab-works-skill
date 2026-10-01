# -*- coding: utf-8 -*-
"""Extract text from PDF (pymupdf). Usage: py _tools\\pdf_text.py <file.pdf> [pages: e.g. 1-5]"""
import sys
import fitz

def main():
    path = sys.argv[1]
    pages = None
    if len(sys.argv) > 2:
        a, b = sys.argv[2].split('-')
        pages = range(int(a) - 1, int(b))
    doc = fitz.open(path)
    rng = pages if pages is not None else range(len(doc))
    for i in rng:
        print(f'===== PAGE {i + 1} =====')
        print(doc[i].get_text())

if __name__ == '__main__':
    main()
