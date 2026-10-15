"""Batch helpers for the CLI: expand folders, pick non-clashing output names, parse one file.

`process_file` lives here (not in __main__) so ProcessPoolExecutor workers can import it on Windows,
where child processes are spawned and cannot see functions defined in a `python -m` __main__ module.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from .detect import EXTENSION_MAP
from .registry import DocumentParserRegistry


def expand_inputs(paths: List[Path]) -> List[Path]:
    """Folders -> every supported file below them (sorted); plain paths are kept as given."""
    out: List[Path] = []
    for p in paths:
        if p.is_dir():
            out.extend(sorted(q for q in p.rglob("*") if q.is_file() and q.suffix.lower() in EXTENSION_MAP
                              and not q.name.startswith("~$")))
        else:
            out.append(p)
    return out


def output_names(files: List[Path]) -> Dict[Path, str]:
    """Output base name per input; two inputs with the same stem (a.pdf / a.docx) must not overwrite each other."""
    stems = [f.stem for f in files]
    used, names = set(), {}
    for f in files:
        name = f.stem if stems.count(f.stem) == 1 else f"{f.stem}_{f.suffix.lstrip('.').lower()}"
        base, n = name, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        names[f] = name
    return names


def process_file(f: Path, name: Optional[str], opts: dict) -> dict:
    """Parse one file (and save it when --out is given). Runs in a worker process, so it only takes and
    returns plain data."""
    t0 = time.perf_counter()
    registry = DocumentParserRegistry.default(pdf_password=opts["pdf_password"], extract_images=not opts["no_images"])
    if opts["revisions"] != "accept" or opts["no_comments"]:
        from .parsers import DocxParser
        registry.register("docx", lambda: DocxParser(extract_images=not opts["no_images"], revisions=opts["revisions"],
                                                     include_comments=not opts["no_comments"]), DocxParser.extensions)
    doc = registry.parse(f)
    if opts["ocr"] and doc.success and doc.images:
        from .ocr import RapidOcrEngine, ocr_document
        engine = RapidOcrEngine(det_max_side=opts["ocr_max_side"], threads=opts["ocr_threads"])
        ocr_document(doc, engine, progress=lambda k, n: print(f"  ocr {f.name} {k}/{n}", end="\r", file=sys.stderr))
    md_path = doc.save(opts["out"], name) if opts["out"] else None   # <name>.md + <name>_images/ (sets image paths)
    data = doc.to_elements() if (opts["format"] == "elements" and doc.success) else doc.to_dict(opts["blocks"])
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    line = ""
    if md_path is not None:
        (opts["out"] / f"{name}.json").write_text(payload, encoding="utf-8")
        status = "ok" if doc.success else f"FAILED: {doc.error_message}"
        extra = f", ocr text in {doc.metadata['ocr_images']} images" if "ocr_images" in doc.metadata else ""
        line = (f"{f} -> {md_path.name} ({doc.file_type}, {doc.metadata.get('char_count', 0)} chars, "
                f"{doc.metadata.get('image_count', 0)} images{extra}) {status}")
    return {"success": doc.success, "error": doc.error_message, "file_type": doc.file_type, "payload": payload,
            "text": doc.text if doc.success else f"[ERROR] {doc.error_message}", "line": line,
            "chars": doc.metadata.get("char_count", 0), "images": doc.metadata.get("image_count", 0),
            "seconds": round(time.perf_counter() - t0, 2)}
