"""Usage example:  python examples/parse_file.py path/to/document.docx"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docparser import DocumentParserRegistry, Table  # noqa: E402
from docparser.parsers import PdfParser  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main(path: str) -> None:
    registry = DocumentParserRegistry.default()
    # optional: per-format configuration, e.g. a password for encrypted PDFs
    registry.register("pdf", lambda: PdfParser(password=None, detect_columns=True), PdfParser.extensions)

    doc = registry.parse(path)          # never raises — check doc.success
    if not doc.success:
        print(f"[{doc.file_type}] failed: {doc.error_message}")
        return

    print("type     :", doc.file_type)
    print("metadata :", json.dumps(doc.metadata, ensure_ascii=False, default=str))
    print("warnings :", doc.warnings)
    print("tables   :", len(doc.tables))
    for t in doc.tables[:3]:            # structured access to merged cells
        for cell in t.cells[:6]:
            print(f"   r{cell.row} c{cell.col} span={cell.rowspan}x{cell.colspan}: {cell.text!r}")
    print("-" * 60)
    print(doc.text[:3000])              # markdown (complex tables as HTML)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        sys.exit("usage: python examples/parse_file.py <file>")
    main(sys.argv[1])
