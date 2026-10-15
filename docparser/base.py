"""BaseParser: the public parse() never raises — it always returns a ParsedDocument."""
from __future__ import annotations

import logging
import time
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Tuple, Union

from .errors import (
    CorruptedDocumentError,
    DocParserError,
    EmptyDocumentError,
    EncryptedDocumentError,
    UnsupportedFormatError,
)
from .images import finalize_images
from .model import Block, ParsedDocument, Table, render_markdown
from .normalize import normalize_text

logger = logging.getLogger("docparser")

# zip-bomb guard (DOCX/PPTX/ODT/XLSX/HWPX are ZIP packages)
MAX_UNCOMPRESSED_BYTES = 2 * 1024**3
MAX_COMPRESSION_RATIO = 250


@dataclass
class ExtractionResult:
    """What a concrete parser returns from _extract()."""

    blocks: List[Block] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


class BaseParser(ABC):
    file_type: ClassVar[str] = ""
    extensions: ClassVar[Tuple[str, ...]] = ()

    def parse(self, file_path: Union[str, Path]) -> ParsedDocument:
        path = Path(file_path)
        started = time.perf_counter()
        try:
            if not path.is_file():
                raise FileNotFoundError(f"file not found: {path}")
            size = path.stat().st_size
            if size == 0:
                raise EmptyDocumentError("file is empty (0 bytes)")
            result = self._extract(path)
            image_meta = finalize_images(result.blocks, path.stem)
            text = normalize_text(render_markdown(result.blocks, image_meta["image_dir"] or ""))
            result.metadata.update(image_meta)
            metadata = {k: v for k, v in result.metadata.items() if v not in (None, "", [], {})}
            metadata.setdefault("file_name", path.name)
            metadata["file_size"] = size
            metadata["table_count"] = _count_tables(result.blocks)
            metadata["char_count"] = len(text)
            metadata["parse_seconds"] = round(time.perf_counter() - started, 4)
            if not text:
                metadata["empty"] = True
                result.warnings.append("document contains no extractable text")
            return ParsedDocument(text=text, file_type=self.file_type, metadata=metadata, success=True,
                                  blocks=result.blocks, warnings=result.warnings)
        except EncryptedDocumentError as exc:
            return self._fail(path, f"encrypted or protected document: {exc}", exc, level=logging.WARNING)
        except (UnsupportedFormatError, EmptyDocumentError, FileNotFoundError) as exc:
            return self._fail(path, str(exc), exc, level=logging.WARNING)
        except CorruptedDocumentError as exc:
            return self._fail(path, f"corrupted document: {exc}", exc)
        except DocParserError as exc:
            return self._fail(path, str(exc), exc)
        except MemoryError as exc:
            return self._fail(path, "document too large to parse in memory", exc)
        except Exception as exc:  # any library error = damaged / unexpected input
            return self._fail(path, f"failed to parse ({type(exc).__name__}): {exc}", exc, exc_info=True)

    @abstractmethod
    def _extract(self, path: Path) -> ExtractionResult:
        """Parse the file. May raise; parse() turns exceptions into failures."""

    def _fail(self, path: Path, message: str, exc: BaseException, level: int = logging.ERROR,
              exc_info: bool = False) -> ParsedDocument:
        logger.log(level, "[%s] %s: %s", self.file_type, path, message, exc_info=exc_info)
        return ParsedDocument.failure(self.file_type, message, {"file_name": path.name})


def _count_tables(blocks: List[Block]) -> int:
    n = 0
    for b in blocks:
        if isinstance(b, Table):
            n += 1
            for c in b.cells:
                n += _count_tables(c.blocks)
    return n


def check_zip_safety(path: Path) -> None:
    """Reject corrupt archives and decompression bombs before handing them to a library."""
    try:
        with zipfile.ZipFile(path) as zf:
            total = sum(i.file_size for i in zf.infolist())
            packed = sum(i.compress_size for i in zf.infolist()) or 1
    except zipfile.BadZipFile as exc:
        raise CorruptedDocumentError(f"invalid ZIP container: {exc}") from exc
    if total > MAX_UNCOMPRESSED_BYTES or (total > 50 * 1024**2 and total / packed > MAX_COMPRESSION_RATIO):
        raise CorruptedDocumentError(
            f"archive expands to {total} bytes (ratio {total / packed:.0f}x) - refusing (zip bomb guard)")
