"""CLI:  python -m docparser FILE|DIR [FILE|DIR ...] [--out DIR] [--jobs N] [--ocr] [--json [--format elements|simple]]"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import HWP_NOTICE, __version__
from .batch import expand_inputs, output_names, process_file

LICENSE_SUMMARY = f"""docparser (ho) {__version__}

{HWP_NOTICE}
  - HWPX parsing is original code written from Hancom's published file format documents.
    Hancom gives no warranty on the accuracy of derived works.
  - PowerPoint 97-2003 (.ppt) parsing follows Microsoft's [MS-PPT] Open Specification.

Third-party open source (all permissive; full texts ship with each package and are listed in NOTICE):
  pdfplumber, pdfminer.six, python-docx, python-pptx, charset-normalizer  MIT
  pypdfium2 (+ PDFium)  BSD-3-Clause / Apache-2.0      lxml, olefile  BSD      openpyxl  MIT
  odfpy  used under Apache-2.0 (tri-licensed)           defusedxml  PSF-2.0
  Optional OCR: rapidocr, PP-OCRv5 models  Apache-2.0 · onnxruntime  MIT · opencv-python  Apache-2.0
               (opencv-python bundles FFmpeg under LGPL-2.1)
"""


class _LicensesAction(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(LICENSE_SUMMARY)
        parser.exit()


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="docparser", description="Extract text/structure from documents",
                                 epilog=f"{HWP_NOTICE}  (python -m docparser --licenses : open source notices)")
    ap.add_argument("--licenses", action=_LicensesAction, help="show license / attribution notices and exit")
    ap.add_argument("--version", action="version", version=f"docparser (ho) {__version__}")
    ap.add_argument("files", nargs="+", type=Path, help="files and/or folders (folders are searched recursively)")
    ap.add_argument("-j", "--jobs", type=int, default=1,
                    help="parse this many files at the same time, one process each (0 = CPU count; default 1). "
                         "With --ocr every process loads its own OCR engine, so keep it small")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--format", choices=["elements", "simple"], default="elements",
                    help="JSON layout: 'elements' = Upstage Document Parse-style (default), 'simple' = text+metadata")
    ap.add_argument("--blocks", action="store_true", help="simple format: include internal blocks")
    ap.add_argument("--out", type=Path, help="write <name>.md, <name>_images/ and <name>.json into this directory")
    ap.add_argument("--ocr", action="store_true", help="OCR text inside pictures (needs: pip install rapidocr onnxruntime)")
    ap.add_argument("--ocr-max-side", type=int, default=None, help="cap OCR detector input (less RAM, may miss small text)")
    ap.add_argument("--ocr-threads", type=int, default=-1, help="CPU threads for OCR (-1 = onnxruntime default)")
    ap.add_argument("--pdf-password", default=None)
    ap.add_argument("--no-images", action="store_true", help="skip image extraction (faster, text only)")
    ap.add_argument("--revisions", choices=["accept", "markup"], default="accept",
                    help="DOCX tracked changes: 'accept' = text with all changes accepted (default), "
                         "'markup' = {++inserted++} / {--deleted--}")
    ap.add_argument("--no-comments", action="store_true", help="DOCX: leave out Word comments (메모)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    files = expand_inputs(args.files)
    if not files:
        print("[ERROR] no supported files found", file=sys.stderr)
        return 1
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    names = output_names(files) if args.out else {}
    opts = {k: getattr(args, k) for k in ("ocr", "ocr_max_side", "ocr_threads", "pdf_password", "no_images",
                                         "revisions", "no_comments", "format", "blocks", "json", "out")}
    jobs = (os.cpu_count() or 1) if args.jobs == 0 else max(1, args.jobs)
    jobs = min(jobs, len(files))

    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:      # results come back in input order
            results = list(pool.map(process_file, files, [names.get(f) for f in files], [opts] * len(files)))
    else:
        results = [process_file(f, names.get(f), opts) for f in files]

    failures = 0
    for f, r in zip(files, results):
        failures += not r["success"]
        if args.out:
            print(r["line"])
        elif args.json:
            print(r["payload"])
        else:
            if len(files) > 1:
                print(f"\n===== {f} =====")
            print(r["text"])
    if args.out and len(files) > 1:
        summary = [{"file": str(f), "output": names[f], "success": r["success"], "type": r["file_type"],
                    "error": r["error"], "chars": r["chars"], "images": r["images"], "seconds": r["seconds"]}
                   for f, r in zip(files, results)]
        (args.out / "_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n{len(files) - failures}/{len(files)} ok -> {args.out / '_summary.json'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
