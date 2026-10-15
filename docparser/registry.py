"""Routes a file to the right parser using magic bytes, falling back to the extension."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, Optional, Union

from . import detect
from .base import BaseParser
from .model import ParsedDocument

logger = logging.getLogger("docparser")

ParserFactory = Callable[[], BaseParser]


class DocumentParserRegistry:
    def __init__(self) -> None:
        self._factories: Dict[str, ParserFactory] = {}
        self._ext_map: Dict[str, str] = {}

    def register(self, file_type: str, factory: ParserFactory, extensions: tuple = ()) -> None:
        """Register (or replace) the parser for a file type. `factory` is usually the parser class."""
        self._factories[file_type] = factory
        for ext in extensions:
            self._ext_map[ext.lower()] = file_type

    @classmethod
    def default(cls, extract_images: bool = True, **options) -> "DocumentParserRegistry":
        """Registry with all built-in parsers. `options` are forwarded, e.g. pdf_password='x'."""
        from .parsers import DocxParser, HwpxParser, OdtParser, PdfParser, PptParser, PptxParser, XlsxParser

        reg = cls()
        pdf_opts = {k[4:]: v for k, v in options.items() if k.startswith("pdf_")}
        reg.register("pdf", lambda: PdfParser(extract_images=extract_images, **pdf_opts), PdfParser.extensions)
        for p in (DocxParser, PptxParser, PptParser, OdtParser, HwpxParser, XlsxParser):
            reg.register(p.file_type, (lambda p=p: p(extract_images=extract_images)), p.extensions)
        return reg

    @property
    def supported_types(self):
        return sorted(self._factories)

    def resolve_type(self, path: Path) -> Optional[str]:
        by_content = detect.detect_by_content(path)
        by_ext = self._ext_map.get(path.suffix.lower())
        if by_content and by_ext and by_content != by_ext:
            logger.info("%s: extension says %s but content is %s - trusting content", path, by_ext, by_content)
        return by_content or by_ext

    def get_parser(self, file_path: Union[str, Path]) -> Optional[BaseParser]:
        ftype = self.resolve_type(Path(file_path))
        factory = self._factories.get(ftype or "")
        return factory() if factory else None

    def parse(self, file_path: Union[str, Path]) -> ParsedDocument:
        path = Path(file_path)
        if not path.is_file():
            return ParsedDocument.failure(path.suffix.lstrip(".") or "unknown", f"file not found: {path}")
        if path.stat().st_size == 0:
            return ParsedDocument.failure(path.suffix.lstrip(".") or "unknown", "file is empty (0 bytes)")
        ftype = self.resolve_type(path)
        if ftype == detect.ENCRYPTED_OOXML:
            return ParsedDocument.failure(self._ext_map.get(path.suffix.lower(), "ooxml"),
                                          "encrypted or protected document: password-protected Office file")
        factory = self._factories.get(ftype or "")
        if factory is None:
            return ParsedDocument.failure(ftype or path.suffix.lstrip(".") or "unknown",
                                          f"unsupported file type: {ftype or path.suffix or 'unknown'}")
        return factory().parse(path)


_default: Optional[DocumentParserRegistry] = None


def parse_document(file_path: Union[str, Path]) -> ParsedDocument:
    """Convenience one-liner using the default registry."""
    global _default
    if _default is None:
        _default = DocumentParserRegistry.default()
    return _default.parse(file_path)
