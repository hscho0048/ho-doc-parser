"""HWPX (OWPML, KS X 6101) parser — zipfile + ElementTree.

본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
(HWPX follows the OWPML national standard KS X 6101; the Hancom attribution above is kept here as well
because the format knowledge comes from Hancom's published HWPML/OWPML documents.)

Sections are read in content.hpf spine order. Elements are matched by local name so both the 2011
and 2016+ namespace URIs work. Paragraph text comes from hp:p/hp:run/hp:t; tables from hp:tbl
(hp:cellAddr / hp:cellSpan give merges); text boxes from hp:drawText; notes from hp:footNote/hp:endNote.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

try:
    from defusedxml.ElementTree import fromstring  # hardened against entity expansion attacks
except ImportError:  # pragma: no cover
    from xml.etree.ElementTree import fromstring

from ..base import BaseParser, ExtractionResult, check_zip_safety
from ..errors import CorruptedDocumentError, EncryptedDocumentError
from ..model import Block, Heading, Image, Paragraph, Table, TableCell

_SECTION_RE = re.compile(r"^Contents/section(\d+)\.xml$", re.I)
_DRAWING = {"rect", "ellipse", "arc", "polygon", "curve", "connectLine", "container", "textart", "pic",
            "ole", "video", "chart"}
_SKIP_CTRL = {"header", "footer", "pageNum", "pageHiding", "colPr", "newNum", "autoNum", "bookmark",
              "fieldBegin", "fieldEnd", "pageNumCtrl", "indexmark", "hiddenComment"}
_OUTLINE_STYLE = re.compile(r"^(?:개요|outline)\s*(\d+)$", re.I)


def ln(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


class HwpxParser(BaseParser):
    file_type = "hwpx"
    extensions = (".hwpx",)

    def __init__(self, include_headers_footers: bool = False, extract_images: bool = True) -> None:
        self.include_headers_footers = include_headers_footers
        self.extract_images = extract_images

    def _extract(self, path: Path) -> ExtractionResult:
        check_zip_safety(path)
        warnings: List[str] = []
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            self._check_encryption(zf, names)
            sections = self._section_order(zf, names)
            if not sections:
                raise CorruptedDocumentError("no Contents/section*.xml found")
            self._styles = self._read_styles(zf, names)
            self._zf, self._names, self._bin_cache = zf, set(names), {}
            self._items = self._manifest_items(zf, names)
            self._notes: List[Block] = []
            blocks: List[Block] = []
            for name in sections:
                try:
                    root = fromstring(zf.read(name))
                except Exception as exc:
                    warnings.append(f"{name}: unreadable ({type(exc).__name__}: {exc})")
                    continue
                for child in root:
                    if ln(child.tag) == "p":
                        blocks.extend(self._paragraph(child))
            blocks.extend(self._notes)
            meta = self._metadata(zf, names)
        meta["section_count"] = len(sections)
        return ExtractionResult(blocks=blocks, metadata=meta, warnings=warnings)

    # ------------------------------------------------------------------ package

    @staticmethod
    def _check_encryption(zf: zipfile.ZipFile, names: List[str]) -> None:
        if any(i.flag_bits & 0x1 for i in zf.infolist()):
            raise EncryptedDocumentError("HWPX archive entries are encrypted")
        if "META-INF/manifest.xml" in names and b"encryption-data" in zf.read("META-INF/manifest.xml"):
            raise EncryptedDocumentError("HWPX document is password protected")

    @staticmethod
    def _section_order(zf: zipfile.ZipFile, names: List[str]) -> List[str]:
        found = sorted((n for n in names if _SECTION_RE.match(n)),
                       key=lambda n: int(_SECTION_RE.match(n).group(1)))
        hpf = next((n for n in names if n.lower().endswith("content.hpf")), None)
        if hpf:
            try:
                root = fromstring(zf.read(hpf))
                items = {i.get("id"): i.get("href", "") for i in root.iter() if ln(i.tag) == "item"}
                ordered = []
                for ref in (r for r in root.iter() if ln(r.tag) == "itemref"):
                    href = items.get(ref.get("idref"), "")
                    href = href if href.startswith("Contents/") else f"Contents/{href}"
                    if href in names and _SECTION_RE.match(href):
                        ordered.append(href)
                if ordered:
                    return ordered + [n for n in found if n not in ordered]
            except Exception:
                pass
        return found

    @staticmethod
    def _manifest_items(zf: zipfile.ZipFile, names: List[str]) -> Dict[str, str]:
        hpf = next((n for n in names if n.lower().endswith("content.hpf")), None)
        if not hpf:
            return {}
        try:
            root = fromstring(zf.read(hpf))
        except Exception:
            return {}
        return {i.get("id"): i.get("href", "") for i in root.iter() if ln(i.tag) == "item"}

    @staticmethod
    def _read_styles(zf: zipfile.ZipFile, names: List[str]) -> Dict[str, str]:
        if "Contents/header.xml" not in names:
            return {}
        try:
            root = fromstring(zf.read("Contents/header.xml"))
        except Exception:
            return {}
        return {s.get("id"): (s.get("name") or "") for s in root.iter() if ln(s.tag) == "style"}

    @staticmethod
    def _metadata(zf: zipfile.ZipFile, names: List[str]) -> dict:
        meta: dict = {}
        hpf = next((n for n in names if n.lower().endswith("content.hpf")), None)
        if not hpf:
            return meta
        try:
            root = fromstring(zf.read(hpf))
        except Exception:
            return meta
        for el in root.iter():
            tag = ln(el.tag)
            if tag == "title" and el.text:
                meta["title"] = el.text.strip()
            elif tag == "meta" and el.get("name") and el.text:
                key = {"creator": "author", "subject": "subject", "description": "description",
                       "lastsaveby": "last_modified_by", "CreatedDate": "created",
                       "ModifiedDate": "modified", "keyword": "keywords"}.get(el.get("name"))
                if key:
                    meta[key] = el.text.strip()
        return meta

    # ------------------------------------------------------------------ content

    def _paragraph(self, p) -> List[Block]:
        out: List[Block] = []
        after: List[Block] = []
        buf: List[str] = []
        level = self._heading_level(p.get("styleIDRef"))

        def flush():
            text = "".join(buf).strip()
            buf.clear()
            if text:
                out.append(Heading(text, level) if level else Paragraph(text))

        for run in p:
            if ln(run.tag) != "run":
                continue
            for item in run:
                tag = ln(item.tag)
                if tag == "t":
                    buf.append(_t_text(item))
                elif tag == "tbl":
                    flush()
                    out.append(self._table(item))
                elif tag == "ctrl":
                    for c in item:
                        ctag = ln(c.tag)
                        if ctag in ("footNote", "endNote"):
                            n = len(self._notes) + 1
                            buf.append(f"[^{n}]")
                            note = " ".join(getattr(b, "text", "") for b in self._sublists(c)).strip()
                            self._notes.append(Paragraph(f"[^{n}]: {note}"))
                        elif ctag in ("header", "footer") and self.include_headers_footers:
                            after.extend(self._sublists(c))
                elif tag == "equation":
                    script = next((x.text for x in item if ln(x.tag) == "script" and x.text), "")
                    if script.strip():
                        buf.append(f" [수식: {script.strip()}] ")
                elif tag in _DRAWING:
                    pictures = self._pictures(item)
                    if pictures:
                        flush()
                        out.extend(pictures)
                    after.extend(self._sublists(item))
        flush()
        return out + after

    def _pictures(self, el) -> List[Image]:
        """hc:img references below a drawing object (pictures inside text boxes are found via their paragraph)."""
        if not self.extract_images:
            return []
        out: List[Image] = []
        alt = next((x.text for x in el if ln(x.tag) == "shapeComment" and x.text), "") or ""
        stack = [el]
        while stack:
            node = stack.pop(0)
            for ch in node:
                tag = ln(ch.tag)
                if tag == "subList":
                    continue
                if tag == "img" and ch.get("binaryItemIDRef"):
                    img = self._bin_image(ch.get("binaryItemIDRef"), alt)
                    if img is not None:
                        out.append(img)
                else:
                    stack.append(ch)
        return out

    def _bin_image(self, ref: str, alt: str) -> Optional[Image]:
        href = self._items.get(ref) or next((n for n in self._names if n.startswith(f"BinData/{ref}.")), None)
        if not href:
            return None
        if href not in self._names and f"Contents/{href}" in self._names:
            href = f"Contents/{href}"
        if href not in self._bin_cache:
            try:
                self._bin_cache[href] = self._zf.read(href)
            except KeyError:
                return None
        return Image(data=self._bin_cache[href], ext=href.rsplit(".", 1)[-1], alt=alt.strip())

    def _heading_level(self, style_ref: Optional[str]) -> Optional[int]:
        m = _OUTLINE_STYLE.match(self._styles.get(style_ref or "", "").strip())
        return min(int(m.group(1)), 6) if m else None

    def _sublists(self, el) -> List[Block]:
        """Paragraphs of the outermost hp:subList elements below `el` (nested ones recurse naturally)."""
        out: List[Block] = []
        for ch in el:
            tag = ln(ch.tag)
            if tag == "subList":
                for p in ch:
                    if ln(p.tag) == "p":
                        out.extend(self._paragraph(p))
            elif tag == "tbl":
                out.append(self._table(ch))
            elif tag != "p":
                out.extend(self._sublists(ch))
        return out

    def _table(self, tbl) -> Table:
        cells: List[TableCell] = []
        caption = ""
        seq = 0
        cols = int(tbl.get("colCnt") or 0) or 1
        for ch in tbl:
            tag = ln(ch.tag)
            if tag == "caption":
                caption = " ".join(getattr(b, "text", "") for b in self._sublists(ch)).strip()
            elif tag == "tr":
                for tc in ch:
                    if ln(tc.tag) != "tc":
                        continue
                    addr = next((x for x in tc if ln(x.tag) == "cellAddr"), None)
                    span = next((x for x in tc if ln(x.tag) == "cellSpan"), None)
                    row = _int(addr.get("rowAddr") if addr is not None else None, seq // cols)
                    col = _int(addr.get("colAddr") if addr is not None else None, seq % cols)
                    blocks: List[Block] = []
                    for sub in tc:
                        if ln(sub.tag) == "subList":
                            for p in sub:
                                if ln(p.tag) == "p":
                                    blocks.extend(self._paragraph(p))
                    cells.append(TableCell(
                        row=row, col=col, blocks=blocks,
                        rowspan=max(_int(span.get("rowSpan") if span is not None else None, 1), 1),
                        colspan=max(_int(span.get("colSpan") if span is not None else None, 1), 1),
                        is_header=tc.get("header") in ("1", "true")))
                    seq += 1
        return Table(cells=cells, caption=caption)


def _t_text(t) -> str:
    parts = [t.text or ""]
    for ch in t:
        tag = ln(ch.tag)
        if tag == "tab":
            parts.append("\t")
        elif tag == "lineBreak":
            parts.append("\n")
        elif tag in ("nbSpace", "fwSpace"):
            parts.append(" ")
        elif tag == "hyphen":
            parts.append("-")
        parts.append(ch.tail or "")
    return "".join(parts)


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
