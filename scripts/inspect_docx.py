# -*- coding: utf-8 -*-
"""Инспектор .docx: печатает структуру абзацев word/document.xml (текст + форматирование).
Использование: python inspect_docx.py <файл.docx> [количество_абзацев]"""
import sys, zipfile, xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

def rpr_info(rpr):
    if rpr is None:
        return ""
    parts = []
    rf = rpr.find(W + "rFonts")
    if rf is not None:
        parts.append("font=%s/%s/%s" % (rf.get(W + "ascii"), rf.get(W + "hAnsi"), rf.get(W + "cs")))
    sz = rpr.find(W + "sz")
    if sz is not None:
        parts.append("sz=%s(half-pt)" % sz.get(W + "val"))
    for tag in ("b", "i", "caps", "smallCaps", "u"):
        el = rpr.find(W + tag)
        if el is not None and el.get(W + "val") != "0":
            parts.append(tag)
    return " ".join(parts)

def paragraph_text(p):
    return "".join(t.text or "" for t in p.iter(W + "t"))

def cell_text(cell):
    return "\n".join(paragraph_text(p) for p in cell.findall(W + "p"))

def main():
    docx = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10 ** 9
    with zipfile.ZipFile(docx) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    ppr_tags = ("pStyle", "jc", "spacing", "ind", "textAlignment")
    for i, p in enumerate(body.findall(W + "p")):
        if i >= limit:
            print("... (всего абзацев: %d)" % len(body.findall(W + 'p')))
            break
        info = []
        ppr = p.find(W + "pPr")
        if ppr is not None:
            for tag in ppr_tags:
                el = ppr.find(W + tag)
                if el is not None:
                    attrs = ",".join("%s=%s" % (k.split("}")[-1], v) for k, v in el.attrib.items())
                    info.append("%s[%s]" % (tag, attrs))
        runs = []
        for r in p.findall(W + "r"):
            t = r.find(W + "t")
            txt = (t.text or "") if t is not None else ""
            ri = rpr_info(r.find(W + "rPr"))
            br = "BR " if r.find(W + "br") is not None else ""
            if r.find(W + "drawing") is not None:
                txt = "[РИСУНОК]"
            runs.append("[%s]%r" % (ri or "-", br + txt))
        print("P%02d %s :: %s" % (i, " ".join(info) or "-", " | ".join(runs) if runs else "(пустой)"))
    for table_number, table in enumerate(body.findall(W + "tbl"), 1):
        rows = table.findall(W + "tr")
        print("TABLE %d rows=%d" % (table_number, len(rows)))
        for row_number, row in enumerate(rows, 1):
            cells = row.findall(W + "tc")
            print("  TR %d :: %s" % (row_number, " | ".join(repr(cell_text(cell)) for cell in cells)))
    sect = body.find(W + "sectPr")
    if sect is not None:
        pg = sect.find(W + "pgSz"); mar = sect.find(W + "pgMar")
        print("SECT pgSz=%s pgMar=%s" % (
            {k.split('}')[-1]: v for k, v in pg.attrib.items()} if pg is not None else {},
            {k.split('}')[-1]: v for k, v in mar.attrib.items()} if mar is not None else {}))

if __name__ == "__main__":
    main()
