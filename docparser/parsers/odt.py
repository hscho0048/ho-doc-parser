"""OpenDocument Text parser (odfpy, used under its Apache-2.0 licence option).

Handles headings (outline-level), lists, sections (multi-column sections are stored in logical
order), frames/text boxes, footnotes, tables with merged cells (number-*-spanned +
covered-table-cell) and repeated rows/columns.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import List, Optional

from odf.namespaces import DCNS, DRAWNS, METANS, OFFICENS, SVGNS, TABLENS, TEXTNS, XLINKNS
from odf.opendocument import load

from ..base import BaseParser, ExtractionResult, check_zip_safety
from ..errors import CorruptedDocumentError, EncryptedDocumentError
from ..model import Block, Heading, Image, ListItem, Paragraph, Table, TableCell

TEXT_NODE = 3
MAX_REPEAT = 200  # cap number-*-repeated expansion (spreadsheet-like empty filler rows)
_SKIP = {
    (OFFICENS, "annotation"), (OFFICENS, "annotation-end"), (TEXTNS, "tracked-changes"),
    (TEXTNS, "sequence-decls"), (TEXTNS, "variable-decls"), (TEXTNS, "user-field-decls"),
    (TEXTNS, "note-citation"), (TEXTNS, "bookmark-ref"), (DRAWNS, "image"),
}


def _attr(el, ns: str, name: str, default: Optional[str] = None) -> Optional[str]:
    try:
        v = el.getAttrNS(ns, name)
    except Exception:
        v = None
    return v if v not in (None, "") else default


def _int_attr(el, ns, name, default=1) -> int:
    v = _attr(el, ns, name)
    return int(v) if v and v.isdigit() else default


class OdtParser(BaseParser):
    file_type = "odt"
    extensions = (".odt", ".ott")

    def __init__(self, extract_images: bool = True) -> None:
        self.extract_images = extract_images

    def _extract(self, path: Path) -> ExtractionResult:
        check_zip_safety(path)
        _check_encryption(path)
        try:
            doc = load(str(path))
        except (KeyError, zipfile.BadZipFile, ValueError) as exc:
            raise CorruptedDocumentError(str(exc)) from exc
        except Exception as exc:  # expat errors etc.
            raise CorruptedDocumentError(f"{type(exc).__name__}: {exc}") from exc

        self._notes: List[str] = []
        with zipfile.ZipFile(path) as self._zip:
            blocks = self._blocks(doc.text)
        blocks += [Paragraph(n) for n in self._notes]
        return ExtractionResult(blocks=blocks, metadata=_metadata(doc))

    # ------------------------------------------------------------------ blocks

    def _blocks(self, container, list_level: int = -1) -> List[Block]:
        out: List[Block] = []
        for node in container.childNodes:
            if node.nodeType == TEXT_NODE:
                continue
            q = node.qname
            if q in _SKIP:
                continue
            if q == (TEXTNS, "h"):
                text, extra = self._inline(node)
                if text:
                    out.append(Heading(text, min(_int_attr(node, TEXTNS, "outline-level", 1), 6)))
                out += extra
            elif q == (TEXTNS, "p"):
                text, extra = self._inline(node)
                if text:
                    out.append(ListItem(text, level=max(list_level, 0)) if list_level >= 0 else Paragraph(text))
                out += extra
            elif q == (TEXTNS, "list"):
                out += self._blocks(node, list_level + 1)
            elif q in ((TEXTNS, "list-item"), (TEXTNS, "list-header")):
                out += self._blocks(node, list_level)
            elif q == (TABLENS, "table"):
                out.append(self._table(node))
            elif q == (DRAWNS, "frame"):
                out += self._frame(node, list_level)
            elif q in ((TEXTNS, "section"), (TEXTNS, "index-body"), (DRAWNS, "text-box"),
                       (TEXTNS, "table-of-content"), (TEXTNS, "alphabetical-index"),
                       (TEXTNS, "illustration-index"), (TEXTNS, "table-index"), (TEXTNS, "user-index"),
                       (TEXTNS, "bibliography"), (OFFICENS, "forms"), (TEXTNS, "soft-page-break")):
                out += self._blocks(node, list_level)
        return out

    def _frame(self, frame, list_level: int) -> List[Block]:
        """draw:frame: first draw:image only (others are fallback renditions), then text boxes."""
        out: List[Block] = []
        kids = [c for c in frame.childNodes if c.nodeType != TEXT_NODE]
        image = next((c for c in kids if c.qname == (DRAWNS, "image")), None)
        if image is not None and self.extract_images:
            href = _attr(image, XLINKNS, "href") or ""
            alt = " ".join(_plain(c).strip() for c in kids if c.qname in ((SVGNS, "title"), (SVGNS, "desc")))
            data = self._zip_read(href)
            if data:
                out.append(Image(data=data, ext=href.rsplit(".", 1)[-1], alt=alt.strip()))
        for c in kids:
            if c.qname == (DRAWNS, "text-box"):
                out += self._blocks(c, list_level)
        return out

    def _zip_read(self, href: str) -> bytes:
        if not href or "://" in href:
            return b""  # external link
        try:
            return self._zip.read(href.lstrip("./"))
        except KeyError:
            return b""

    def _inline(self, el):
        """Text of a paragraph + blocks floating inside it (text boxes, frames)."""
        parts: List[str] = []
        extra: List[Block] = []

        def walk(node):
            for ch in node.childNodes:
                if ch.nodeType == TEXT_NODE:
                    parts.append(ch.data)
                    continue
                q = ch.qname
                if q in _SKIP:
                    continue
                if q == (TEXTNS, "s"):
                    parts.append(" " * min(_int_attr(ch, TEXTNS, "c", 1), 50))
                elif q == (TEXTNS, "tab"):
                    parts.append("\t")
                elif q == (TEXTNS, "line-break"):
                    parts.append("\n")
                elif q == (TEXTNS, "note"):
                    cite = ""
                    for c in ch.childNodes:
                        if c.nodeType != TEXT_NODE and c.qname == (TEXTNS, "note-citation"):
                            cite = "".join(str(x) for x in c.childNodes)
                    body = [c for c in ch.childNodes if c.nodeType != TEXT_NODE and c.qname == (TEXTNS, "note-body")]
                    note_text = " ".join(_plain(b) for b in body).strip()
                    label = cite.strip() or str(len(self._notes) + 1)
                    parts.append(f"[^{label}]")
                    if note_text:
                        self._notes.append(f"[^{label}]: {note_text}")
                elif q == (DRAWNS, "frame"):
                    extra.extend(self._frame(ch, -1))
                elif q in ((DRAWNS, "text-box"), (DRAWNS, "custom-shape"), (DRAWNS, "rect"), (DRAWNS, "g")):
                    extra.extend(self._blocks(ch))
                else:
                    walk(ch)

        walk(el)
        return "".join(parts).strip(), extra

    # ------------------------------------------------------------------ tables

    def _table(self, tbl) -> Table:
        cells: List[TableCell] = []
        rows = list(_iter_rows(tbl))
        r = 0
        for row, is_header in rows:
            repeat = min(_int_attr(row, TABLENS, "number-rows-repeated", 1), MAX_REPEAT)
            for _ in range(repeat):
                col = 0
                for cell in row.childNodes:
                    if cell.nodeType == TEXT_NODE:
                        continue
                    crep = min(_int_attr(cell, TABLENS, "number-columns-repeated", 1), MAX_REPEAT)
                    if cell.qname == (TABLENS, "covered-table-cell"):
                        col += crep
                        continue
                    if cell.qname != (TABLENS, "table-cell"):
                        continue
                    for _ in range(crep):
                        cells.append(TableCell(
                            row=r, col=col, blocks=self._blocks(cell),
                            rowspan=_int_attr(cell, TABLENS, "number-rows-spanned", 1),
                            colspan=_int_attr(cell, TABLENS, "number-columns-spanned", 1),
                            is_header=is_header))
                        col += 1
                r += 1
        return _trim(Table(cells=cells))


def _iter_rows(el, header=False):
    for ch in el.childNodes:
        if ch.nodeType == TEXT_NODE:
            continue
        if ch.qname == (TABLENS, "table-row"):
            yield ch, header
        elif ch.qname in ((TABLENS, "table-rows"), (TABLENS, "table-row-group")):
            yield from _iter_rows(ch, header)
        elif ch.qname == (TABLENS, "table-header-rows"):
            yield from _iter_rows(ch, True)


def _trim(table: Table) -> Table:
    """Drop trailing fully-empty rows/columns produced by repeated filler cells."""
    used_rows = {c.row for c in table.cells if c.blocks}
    used_cols = {c.col for c in table.cells if c.blocks}
    if not used_rows:
        return Table(cells=[])
    max_r, max_c = max(used_rows), max(used_cols)
    table.cells = [c for c in table.cells if c.row <= max_r and c.col <= max_c]
    return table


def _plain(el) -> str:
    out = []
    for ch in el.childNodes:
        if ch.nodeType == TEXT_NODE:
            out.append(ch.data)
        elif ch.qname == (TEXTNS, "s"):
            out.append(" ")
        elif ch.qname in ((TEXTNS, "p"), (TEXTNS, "h")):
            out.append(_plain(ch) + " ")
        else:
            out.append(_plain(ch))
    return "".join(out)


def _metadata(doc) -> dict:
    meta = {}
    mapping = {(DCNS, "title"): "title", (DCNS, "creator"): "last_modified_by",
               (METANS, "initial-creator"): "author", (DCNS, "subject"): "subject",
               (DCNS, "description"): "description", (METANS, "keyword"): "keywords",
               (METANS, "creation-date"): "created", (DCNS, "date"): "modified",
               (METANS, "generator"): "application"}
    try:
        for node in doc.meta.childNodes:
            if node.nodeType == TEXT_NODE:
                continue
            key = mapping.get(node.qname)
            if key:
                meta[key] = _plain(node).strip()
            elif node.qname == (METANS, "document-statistic"):
                for attr, k in (("page-count", "page_count"), ("table-count", "table_count_declared"),
                                ("word-count", "word_count")):
                    v = _attr(node, METANS, attr)
                    if v and v.isdigit():
                        meta[k] = int(v)
    except Exception:
        pass
    if not meta.get("author") and meta.get("last_modified_by"):
        meta["author"] = meta["last_modified_by"]
    return meta


def _check_encryption(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        try:
            manifest = zf.read("META-INF/manifest.xml")
        except KeyError:
            return
    if re.search(rb"encryption-data", manifest):
        raise EncryptedDocumentError("OpenDocument file is password protected")
