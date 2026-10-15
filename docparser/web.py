"""Entry point for the in-browser reference runner (site/, Pyodide). Not used by the CLI.

The page hands over the uploaded bytes, this writes them to the virtual file system, parses them and
saves <out>/result.md, result.json and result_images/ there; the page reads those files back.
"""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path


def parse_upload(name: str, data: bytes, out_dir: str, options_json: str = "{}") -> str:
    """Parse one uploaded file. Returns a JSON string the page renders (images stay in out_dir)."""
    from . import DocumentParserRegistry
    from .parsers import DocxParser, XlsxParser

    opts = json.loads(options_json or "{}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    src = out / ("source" + Path(name).suffix.lower())
    src.write_bytes(bytes(data))

    t0 = time.perf_counter()
    images = bool(opts.get("images", True))
    reg = DocumentParserRegistry.default(extract_images=images, pdf_password=opts.get("pdf_password") or None)
    reg.register("docx", lambda: DocxParser(extract_images=images, revisions=opts.get("revisions", "accept"),
                                            include_comments=bool(opts.get("comments", True))), DocxParser.extensions)
    reg.register("xlsx", lambda: XlsxParser(extract_images=images, include_hidden=bool(opts.get("hidden_sheets")),
                                            include_comments=bool(opts.get("comments", True))), XlsxParser.extensions)
    doc = reg.parse(src)
    src.unlink()
    doc.metadata["file_name"] = name

    result = {"name": name, "size": len(data), "success": doc.success, "error": doc.error_message,
              "file_type": doc.file_type, "warnings": doc.warnings, "metadata": doc.metadata}
    if doc.success:
        doc.save(out, "result")
        result["markdown"] = doc.text
        result["elements"] = doc.to_elements()
        result["elements"].pop("metadata", None)
        (out / "result.json").write_text(json.dumps(result["elements"], ensure_ascii=False, indent=2, default=str),
                                         encoding="utf-8")
        image_dir = doc.metadata.get("image_dir") or ""
        result["images"] = [{"path": f"{image_dir}/{i.name}" if image_dir else i.name, "name": i.name, "alt": i.alt,
                             "width": i.width, "height": i.height} for i in doc.images]
    result["seconds"] = round(time.perf_counter() - t0, 2)
    return json.dumps(result, ensure_ascii=False, default=str)


def zip_results(jobs_json: str) -> bytes:
    """Bundle several parsed jobs into one zip: <stem>.md, <stem>.json, <stem>_images/ per file, plus
    _summary.json. `jobs_json` = [{"dir": out_dir, "name": original file name, ...summary fields}]."""
    from .batch import output_names
    from .images import _safe

    jobs = json.loads(jobs_json)
    names = output_names([Path(j["name"]) for j in jobs])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        summary = []
        for j in jobs:
            stem, d = names[Path(j["name"])], Path(j["dir"])
            summary.append({"file": j["name"], "output": stem, "success": j.get("success"), "type": j.get("file_type"),
                            "error": j.get("error"), "seconds": j.get("seconds")})
            if not j.get("success"):
                continue
            md = (d / "result.md").read_text(encoding="utf-8")
            new_dir = f"{_safe(stem)}_images"
            md = md.replace("result_images/", new_dir + "/")
            zf.writestr(f"{stem}.md", md)
            zf.writestr(f"{stem}.json", (d / "result.json").read_text(encoding="utf-8")
                        .replace("result_images/", new_dir + "/"))
            img_dir = d / "result_images"
            if img_dir.is_dir():
                for f in sorted(img_dir.iterdir()):
                    zf.writestr(f"{new_dir}/{f.name}", f.read_bytes())
        zf.writestr("_summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
    return buf.getvalue()
