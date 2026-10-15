"""docparser — local, permissively licensed document text/structure extraction.

    from docparser import DocumentParserRegistry
    doc = DocumentParserRegistry.default().parse("report.docx")
"""
from .base import BaseParser, ExtractionResult
from .errors import (CorruptedDocumentError, DocParserError, EncryptedDocumentError, UnsupportedFormatError)
from .model import (Heading, ListItem, PageMarker, Paragraph, ParsedDocument, Table, TableCell,
                    render_markdown, render_table_html)
from .registry import DocumentParserRegistry, parse_document

__version__ = "1.0.0"

# Hancom's published-format condition: this exact sentence must appear in the product's user interface,
# manual, help and source code (whichever exist). Kept for the HWPX parser, whose format knowledge comes
# from Hancom's published HWPML/OWPML documents. Shown by `python -m docparser --help` and `--licenses`.
HWP_NOTICE = "본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다."

__all__ = [
    "HWP_NOTICE",
    "BaseParser", "ExtractionResult", "DocumentParserRegistry", "parse_document", "ParsedDocument",
    "Heading", "Paragraph", "ListItem", "PageMarker", "Table", "TableCell", "render_markdown",
    "render_table_html", "DocParserError", "EncryptedDocumentError", "UnsupportedFormatError",
    "CorruptedDocumentError",
]
