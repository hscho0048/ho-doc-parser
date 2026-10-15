"""Legacy PowerPoint 97-2003 (.ppt) parser.

Pure Python implementation of the public [MS-PPT] binary format using olefile:
CurrentUser -> UserEditAtom chain -> PersistDirectory -> DocumentContainer -> SlideListWithText,
which gives the *current* slide order (incremental saves leave stale records in the stream, so a
naive linear scan returns deleted/old text). Falls back to a linear scan if the structure is damaged.

Optionally converts through LibreOffice (MPL-2.0, run as an external process) for richer output.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import zlib
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import olefile

from ..base import BaseParser, ExtractionResult
from ..errors import CorruptedDocumentError, EncryptedDocumentError
from ..images import dib_to_bmp
from ..model import Block, Heading, Image, ListItem, PageMarker, Paragraph
from ._ole import summary_metadata

RT_DOCUMENT = 0x03E8
RT_SLIDE = 0x03EE
RT_SLIDE_PERSIST_ATOM = 0x03F3
RT_OUTLINE_TEXT_REF = 0x0F9E
RT_TEXT_HEADER = 0x0F9F
RT_TEXT_CHARS = 0x0FA0
RT_TEXT_BYTES = 0x0FA8
RT_SLIDE_LIST_WITH_TEXT = 0x0FF0
RT_USER_EDIT = 0x0FF5
RT_PERSIST_DIRECTORY = 0x1772
RT_FOPT = 0xF00B            # OfficeArtFOPT (shape properties; pib = picture)
RT_BSE = 0xF007             # OfficeArtFBSE (BStore entry)
PID_PIB = 0x0104
IMAGE_KIND = -1
_BLIP_EXT = {0xF01A: "emf", 0xF01B: "wmf", 0xF01C: "pict", 0xF01D: "jpg", 0xF01E: "png", 0xF01F: "dib",
             0xF029: "tif", 0xF02A: "jpg"}
HEADER_TOKEN_ENCRYPTED = 0xF3D1C4DF
TITLE_TYPES = {0, 6}          # Tx_TYPE_TITLE, Tx_TYPE_CENTERTITLE
BODY_TYPES = {1, 5, 7, 8}     # body / centered body / half body / quarter body
TEXT_ATOMS = {RT_TEXT_CHARS, RT_TEXT_BYTES}

Record = Tuple[int, int, int, int]  # offset, instance, type, length  (payload starts at offset+8)


class PptParser(BaseParser):
    file_type = "ppt"
    extensions = (".ppt", ".pps", ".pot")

    def __init__(self, use_libreoffice: bool = False, soffice_path: Optional[str] = None,
                 extract_images: bool = True) -> None:
        self.use_libreoffice = use_libreoffice
        self.soffice_path = soffice_path
        self.extract_images = extract_images

    def _extract(self, path: Path) -> ExtractionResult:
        if self.use_libreoffice:
            converted = self._via_libreoffice(path)
            if converted is not None:
                return converted
        try:
            ole = olefile.OleFileIO(str(path))
        except OSError as exc:
            raise CorruptedDocumentError(f"not a valid OLE compound file: {exc}") from exc
        with ole:
            if not ole.exists("PowerPoint Document"):
                raise CorruptedDocumentError("'PowerPoint Document' stream missing")
            stream = ole.openstream("PowerPoint Document").read()
            current_user = ole.openstream("Current User").read() if ole.exists("Current User") else b""
            self._pictures = ole.openstream("Pictures").read() if self.extract_images and ole.exists("Pictures") else b""
            meta = summary_metadata(ole)
        self._doc_stream, self._bstore = stream, []

        warnings: List[str] = []
        if len(current_user) >= 16:
            token = struct.unpack_from("<I", current_user, 12)[0]
            if token == HEADER_TOKEN_ENCRYPTED:
                raise EncryptedDocumentError("PowerPoint file is password protected")
        try:
            slides = self._slides_structured(stream, current_user)
        except Exception as exc:
            warnings.append(f"slide structure unreadable ({exc}); used linear text scan")
            slides = [_linear_scan(stream)]

        blocks: List[Block] = []
        for idx, texts in enumerate(slides, 1):
            blocks.append(PageMarker(f"slide {idx}"))
            title = next((t for kind, t in texts if kind in TITLE_TYPES), "")
            blocks.append(Heading(f"Slide {idx}" + (f": {title}" if title else ""), level=2))
            for kind, text in texts:
                if kind == IMAGE_KIND:
                    img = self._blip(text)
                    if img is not None:
                        blocks.append(img)
                    continue
                if kind in TITLE_TYPES and text == title:
                    continue
                for line in filter(None, (x.strip() for x in text.split("\n"))):
                    blocks.append(ListItem(line) if kind in BODY_TYPES else Paragraph(line))
        meta["slide_count"] = len(slides)
        return ExtractionResult(blocks=blocks, metadata=meta, warnings=warnings)

    # ------------------------------------------------------------------ structured path

    def _slides_structured(self, data: bytes, current_user: bytes) -> List[List[Tuple[int, str]]]:
        if len(current_user) < 20:
            raise ValueError("Current User stream missing")
        offset = struct.unpack_from("<I", current_user, 16)[0]
        edits, seen = [], set()
        while offset and offset not in seen and offset + 32 <= len(data):
            seen.add(offset)
            _, _, rtype, _ = _header(data, offset)
            if rtype != RT_USER_EDIT:
                raise ValueError(f"expected UserEditAtom at {offset}")
            last_edit, persist_dir_off, doc_ref = struct.unpack_from("<III", data, offset + 16)
            edits.append((persist_dir_off, doc_ref))
            offset = last_edit
        if not edits:
            raise ValueError("no UserEditAtom")

        persist: Dict[int, int] = {}
        for persist_dir_off, _ in reversed(edits):  # oldest first, newer edits override
            persist.update(_persist_directory(data, persist_dir_off))
        doc_off = persist[edits[0][1]]
        if _header(data, doc_off)[2] != RT_DOCUMENT:
            raise ValueError("DocumentContainer not found")
        self._bstore = _bstore(data, doc_off) if self.extract_images else []

        slides: List[List[Tuple[int, str]]] = []
        for rec in _children(data, doc_off):
            if rec[2] == RT_SLIDE_LIST_WITH_TEXT and rec[1] == 0:
                for psr_ref, outline in _slide_list(data, rec):
                    slides.append(self._slide_texts(data, persist.get(psr_ref), outline))
        return slides

    def _slide_texts(self, data: bytes, slide_off: Optional[int], outline: List[Tuple[int, str]]):
        """[(text type, text)] in drawing order; pictures appear as (IMAGE_KIND, bstore index)."""
        texts: List[Tuple[int, object]] = []
        used = set()
        if slide_off is not None and _header(data, slide_off)[2] == RT_SLIDE:
            current_type = 4
            for off, inst, rtype, length in _walk(data, slide_off):
                if rtype == RT_TEXT_HEADER and length >= 4:
                    current_type = struct.unpack_from("<I", data, off + 8)[0]
                elif rtype in TEXT_ATOMS:
                    texts.append((current_type, _atom_text(data, off, rtype, length)))
                elif rtype == RT_OUTLINE_TEXT_REF and length >= 4:
                    idx = struct.unpack_from("<I", data, off + 8)[0]
                    if idx < len(outline) and idx not in used:
                        used.add(idx)
                        texts.append(outline[idx])
                elif rtype == RT_FOPT and self.extract_images:
                    pib = _fopt_pib(data, off, inst, length)
                    if pib:
                        texts.append((IMAGE_KIND, pib))
        texts += [t for i, t in enumerate(outline) if i not in used]
        out, seen = [], set()
        for kind, text in texts:  # some writers store the same text twice
            if kind == IMAGE_KIND:
                out.append((kind, text))
                continue
            key = text.strip()
            if key and key not in seen:
                seen.add(key)
                out.append((kind, text))
        return out

    def _blip(self, idx: int) -> Optional[Image]:
        """BStore entry (1-based) -> picture bytes from the 'Pictures' stream or embedded in the FBSE."""
        if not 1 <= idx <= len(self._bstore):
            return None
        embedded, fo_delay = self._bstore[idx - 1]
        try:
            if embedded is not None:
                return _decode_blip(self._doc_stream, embedded)
            if self._pictures and fo_delay < len(self._pictures):
                return _decode_blip(self._pictures, fo_delay)
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------ LibreOffice

    def _via_libreoffice(self, path: Path) -> Optional[ExtractionResult]:
        exe = self.soffice_path or shutil.which("soffice") or shutil.which("libreoffice")
        if not exe:
            return None
        from .pptx import PptxParser
        with tempfile.TemporaryDirectory() as tmp:
            try:
                subprocess.run([exe, "--headless", "--convert-to", "pptx", "--outdir", tmp, str(path)],
                               check=True, timeout=180, capture_output=True)
                out = next(Path(tmp).glob("*.pptx"))
            except Exception:
                return None
            return PptxParser()._extract(out)


# ---------------------------------------------------------------------- record helpers


def _header(data: bytes, off: int) -> Record:
    if off + 8 > len(data):
        raise ValueError(f"record header out of range at {off}")
    ver_inst, rtype, length = struct.unpack_from("<HHI", data, off)
    return off, ver_inst >> 4, rtype, length


def _is_container(data: bytes, off: int) -> bool:
    return (struct.unpack_from("<H", data, off)[0] & 0x0F) == 0x0F


def _children(data: bytes, off: int) -> Iterator[Record]:
    _, _, _, length = _header(data, off)
    pos, end = off + 8, min(off + 8 + length, len(data))
    while pos + 8 <= end:
        rec = _header(data, pos)
        yield rec
        pos += 8 + rec[3]


def _walk(data: bytes, off: int, depth: int = 0) -> Iterator[Record]:
    for rec in _children(data, off):
        yield rec
        if depth < 32 and _is_container(data, rec[0]):
            yield from _walk(data, rec[0], depth + 1)


def _persist_directory(data: bytes, off: int) -> Dict[int, int]:
    _, _, rtype, length = _header(data, off)
    if rtype != RT_PERSIST_DIRECTORY:
        raise ValueError("PersistDirectoryAtom not found")
    out, pos, end = {}, off + 8, off + 8 + length
    while pos + 4 <= end:
        v = struct.unpack_from("<I", data, pos)[0]
        pid, count = v & 0xFFFFF, v >> 20
        pos += 4
        for i in range(count):
            out[pid + i] = struct.unpack_from("<I", data, pos)[0]
            pos += 4
    return out


def _slide_list(data: bytes, rec: Record):
    """Yields (persist id of slide, [(text type, text), ...] outline texts) per SlidePersistAtom."""
    current: Optional[int] = None
    texts: List[Tuple[int, str]] = []
    ttype = 4
    for off, _, rtype, length in _children(data, rec[0]):
        if rtype == RT_SLIDE_PERSIST_ATOM:
            if current is not None:
                yield current, texts
            current, texts = struct.unpack_from("<I", data, off + 8)[0], []
        elif rtype == RT_TEXT_HEADER and length >= 4:
            ttype = struct.unpack_from("<I", data, off + 8)[0]
        elif rtype in TEXT_ATOMS:
            texts.append((ttype, _atom_text(data, off, rtype, length)))
    if current is not None:
        yield current, texts


def _fopt_pib(data: bytes, off: int, inst: int, length: int) -> int:
    """pib (picture BStore index) from an OfficeArtFOPT; `inst` = number of properties."""
    pos, end = off + 8, off + 8 + length
    for _ in range(inst):
        if pos + 6 > end:
            break
        opid, value = struct.unpack_from("<HI", data, pos)
        if opid & 0x3FFF == PID_PIB:  # value is a 1-based BStore index
            return value
        pos += 6
    return 0


def _bstore(data: bytes, doc_off: int) -> List[Tuple[Optional[int], int]]:
    """[(offset of embedded blip | None, foDelay offset into 'Pictures')] per FBSE, in BStore order."""
    out = []
    for off, _, rtype, length in _walk(data, doc_off):
        if rtype != RT_BSE or length < 36:
            continue
        size, _, fo_delay = struct.unpack_from("<III", data, off + 8 + 20)
        cb_name = data[off + 8 + 33]
        blip_off = off + 8 + 36 + cb_name
        embedded = blip_off if blip_off + 8 <= off + 8 + length else None
        out.append((embedded, fo_delay))
    return out


def _decode_blip(buf: bytes, off: int) -> Optional[Image]:
    _, inst, rtype, length = _header(buf, off)
    ext = _BLIP_EXT.get(rtype)
    if ext is None:
        return None
    body = buf[off + 8: off + 8 + length]
    uid_len = 32 if inst & 1 else 16
    if ext in ("emf", "wmf", "pict"):
        meta = body[uid_len: uid_len + 34]
        compression = meta[32] if len(meta) >= 34 else 0xFE
        payload = body[uid_len + 34:]
        if compression == 0:
            try:
                payload = zlib.decompress(payload)
            except zlib.error:
                payload = zlib.decompressobj(-15).decompress(payload)
        return Image(data=payload, ext=ext)
    payload = body[uid_len + 1:]  # 1-byte tag
    if ext == "dib":
        return Image(data=dib_to_bmp(payload), ext="bmp")
    return Image(data=payload, ext=ext)


def _atom_text(data: bytes, off: int, rtype: int, length: int) -> str:
    raw = data[off + 8: off + 8 + length]
    text = raw.decode("utf-16-le", "replace") if rtype == RT_TEXT_CHARS else raw.decode("latin-1")
    return text.replace("\r", "\n").replace("\x0b", "\n")


def _linear_scan(data: bytes) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    pos = 0
    ttype = 4

    def scan(start: int, end: int, depth: int) -> None:
        nonlocal ttype
        p = start
        while p + 8 <= end:
            try:
                off, _, rtype, length = _header(data, p)
            except ValueError:
                return
            if rtype == RT_TEXT_HEADER and length >= 4:
                ttype = struct.unpack_from("<I", data, off + 8)[0]
            elif rtype in TEXT_ATOMS:
                out.append((ttype, _atom_text(data, off, rtype, length)))
            elif _is_container(data, off) and depth < 32:
                scan(off + 8, min(off + 8 + length, end), depth + 1)
            p = off + 8 + length

    scan(pos, len(data), 0)
    seen, dedup = set(), []
    for kind, t in out:
        if t.strip() and t.strip() not in seen:
            seen.add(t.strip())
            dedup.append((kind, t))
    return dedup
