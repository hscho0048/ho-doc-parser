"""One parse job, run in its own process so peak RAM / CPU time belong to this file only.

    python worker.py <source file> <output dir> '<options json>'

Writes <output dir>/result.json (+ result.md, images) and exits.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    src, out, opts = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    from docparser import DocumentParserRegistry
    from docparser.parsers import DocxParser, XlsxParser

    images = bool(opts.get("images", True))
    reg = DocumentParserRegistry.default(extract_images=images, pdf_password=opts.get("pdf_password") or None)
    reg.register("docx", lambda: DocxParser(extract_images=images, revisions=opts.get("revisions", "accept"),
                                            include_comments=bool(opts.get("comments", True))), DocxParser.extensions)
    reg.register("xlsx", lambda: XlsxParser(extract_images=images, include_hidden=bool(opts.get("hidden_sheets"))),
                 XlsxParser.extensions)
    doc = reg.parse(src)

    ocr_note = None
    if doc.success and opts.get("ocr") and doc.images:
        try:
            from docparser.ocr import RapidOcrEngine, ocr_document
            ocr_document(doc, RapidOcrEngine(threads=int(opts.get("threads", 2))))
        except ImportError:
            ocr_note = "OCR 패키지가 없어 OCR을 건너뜀 (pip install rapidocr onnxruntime)"
            doc.warnings.append(ocr_note)

    result = {"success": doc.success, "error": doc.error_message, "file_type": doc.file_type,
              "warnings": doc.warnings, "metadata": doc.metadata}
    if doc.success:
        doc.save(out, "result")
        result["markdown"] = doc.text
        result["elements"] = doc.to_elements()
        result["elements"].pop("metadata", None)
        image_dir = doc.metadata.get("image_dir") or ""
        # every picture, including ones inside table cells (those are not top-level figure elements)
        result["images"] = [{"path": f"{image_dir}/{i.name}" if image_dir else i.name, "name": i.name, "alt": i.alt,
                             "width": i.width, "height": i.height, "ocr_text": i.ocr_text} for i in doc.images]

    wall = time.perf_counter() - t0
    try:
        import psutil
        p = psutil.Process()
        mi, ct = p.memory_info(), p.cpu_times()
        peak = getattr(mi, "peak_wset", None) or mi.rss
        cpu = ct.user + ct.system
        result["resources"] = {"wall_s": round(wall, 2), "cpu_s": round(cpu, 2), "peak_mb": round(peak / 2**20),
                               "cores": round(cpu / wall, 1) if wall > 0 else None}
    except ImportError:
        result["resources"] = {"wall_s": round(wall, 2), "cpu_s": None, "peak_mb": None, "cores": None}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
