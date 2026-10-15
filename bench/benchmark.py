"""Quality / speed report to decide whether local parsing is good enough or an AI API is needed.

    python bench/benchmark.py PATH [PATH ...]          # files or directories (recursive)
    python bench/benchmark.py --synthetic-pages 100    # also generate & time a 100-page 2-column PDF/DOCX
    python bench/benchmark.py DIR --out report.md

Per file it reports success, timing, pages/sec, tables (and how many have merged cells),
pages needing OCR and — when the document contains ordered markers like <<A01>> <<A02>> —
a reading-order score (longest increasing subsequence / markers found).
Nothing leaves the machine.
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docparser import DocumentParserRegistry  # noqa: E402
from docparser.detect import EXTENSION_MAP  # noqa: E402
from docparser.model import Table  # noqa: E402

MARKER = re.compile(r"<<([A-Z])(\d{2,4})>>")


def reading_order_score(text: str):
    found = [(m.group(1), int(m.group(2))) for m in MARKER.finditer(text)]
    if len(found) < 2:
        return None
    tails: list = []
    for v in found:  # longest non-decreasing subsequence
        i = bisect.bisect_right(tails, v)
        tails[i:i + 1] = [v]
    return round(len(tails) / len(found), 3)


def iter_files(paths):
    for p in paths:
        p = Path(p)
        if p.is_dir():
            yield from sorted(f for f in p.rglob("*") if f.suffix.lower() in EXTENSION_MAP)
        elif p.is_file():
            yield p


def all_tables(blocks):
    for b in blocks:
        if isinstance(b, Table):
            yield b
            for c in b.cells:
                yield from all_tables(c.blocks)


def run(files, repeat: int = 1):
    registry = DocumentParserRegistry.default()
    rows = []
    for f in files:
        times = []
        doc = None
        for _ in range(repeat):
            t0 = time.perf_counter()
            doc = registry.parse(f)
            times.append(time.perf_counter() - t0)
        secs = statistics.median(times)
        pages = doc.metadata.get("page_count") or doc.metadata.get("slide_count") or None
        tables = list(all_tables(doc.blocks))
        rows.append({
            "file": f.name, "type": doc.file_type, "ok": doc.success,
            "error": doc.error_message, "size_kb": round(f.stat().st_size / 1024, 1),
            "seconds": round(secs, 3), "pages": pages,
            "pages_per_sec": round(pages / secs, 1) if pages and secs else None,
            "chars": len(doc.text), "tables": len(tables),
            "merged_tables": sum(1 for t in tables if t.is_complex),
            "multi_column_pages": doc.metadata.get("multi_column_pages"),
            "needs_ocr_pages": len(doc.metadata.get("needs_ocr_pages", [])),
            "reading_order": reading_order_score(doc.text),
            "warnings": len(doc.warnings),
        })
    return rows


def synthetic(pages: int, out_dir: Path):
    """Large 2-column PDF + DOCX with merged tables and ordered markers."""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer
    from reportlab.platypus import Table as RLTable
    from reportlab.platypus import TableStyle

    styles = getSampleStyleSheet()
    W, H = A4
    m, gap = 50, 20
    cw = (W - 2 * m - gap) / 2
    pdf_path = out_dir / f"synthetic_{pages}p.pdf"
    doc = BaseDocTemplate(str(pdf_path), pagesize=A4)
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(m, m, cw, H - 2 * m), Frame(m + cw + gap, m, cw, H - 2 * m)])])
    story, k = [], 0
    filler = "Layout analysis keeps the reading order of each column intact across the page. "
    while k < pages * 9:
        k += 1
        story.append(Paragraph(f"&lt;&lt;P{k:04d}&gt;&gt; " + filler * 3, styles["BodyText"]))
        if k % 12 == 0:
            t = RLTable([["Region", "Q1", "Q2"], ["North", "10", "12"], ["South", "8", "9"]], colWidths=[70, 50, 50])
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("SPAN", (1, 0), (2, 0))]))
            story += [t, Spacer(1, 6)]
    doc.build(story)

    d = Document()
    for i in range(1, pages * 9):
        d.add_paragraph(f"<<P{i:04d}>> " + filler * 3)
        if i % 12 == 0:
            t = d.add_table(rows=3, cols=3)
            t.cell(0, 1).merge(t.cell(0, 2)).text = "2025"
    cols = OxmlElement("w:cols")
    cols.set(qn("w:num"), "2")
    d.sections[0]._sectPr.append(cols)
    docx_path = out_dir / f"synthetic_{pages}p.docx"
    d.save(docx_path)
    return [pdf_path, docx_path]


def to_markdown(rows) -> str:
    cols = ["file", "type", "ok", "seconds", "pages", "pages_per_sec", "chars", "tables", "merged_tables",
            "multi_column_pages", "needs_ocr_pages", "reading_order", "warnings"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join("" if r[c] is None else str(r[c]) for c in cols) + " |")
    fails = [r for r in rows if not r["ok"]]
    if fails:
        out.append("\nFailures:")
        out += [f"- {r['file']}: {r['error']}" for r in fails]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--synthetic-pages", type=int, default=0)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    files = list(iter_files(args.paths))
    tmp = None
    if args.synthetic_pages:
        tmp = tempfile.TemporaryDirectory()
        files += synthetic(args.synthetic_pages, Path(tmp.name))
    if not files:
        ap.error("no supported files given")
    rows = run(files, args.repeat)
    md = to_markdown(rows)
    print(md)
    if args.out:
        args.out.write_text(md + "\n\n```json\n" + json.dumps(rows, ensure_ascii=False, indent=2) + "\n```\n",
                            encoding="utf-8")
    if tmp:
        tmp.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
