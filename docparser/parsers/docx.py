"""DOCX parser (python-docx / lxml).

Walks the body XML in document order so paragraphs, tables, content controls and text boxes keep
their reading order. Multi-column sections (w:cols) need no special handling: Word stores text in
logical order. Merged cells come from w:gridSpan / w:vMerge, nested tables are kept as nested blocks.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import docx
from docx.opc.exceptions import PackageNotFoundError
from lxml import etree

from ..base import BaseParser, ExtractionResult, check_zip_safety
from ..errors import CorruptedDocumentError
from ..model import Block, Heading, Image, ListItem, Paragraph, Table, TableCell

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
V_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"
R_EMBED, R_ID = f"{{{R_NS}}}embed", f"{{{R_NS}}}id"
WP_INLINE, WP_ANCHOR, WP_DOCPR = f"{{{WP_NS}}}inline", f"{{{WP_NS}}}anchor", f"{{{WP_NS}}}docPr"
RT_FOOTNOTES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
RT_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"


def w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


T, TAB, BR, CR, P, TBL, TR, TC, SDT = (w(x) for x in ("t", "tab", "br", "cr", "p", "tbl", "tr", "tc", "sdt"))
TXBX = w("txbxContent")
FALLBACK = f"{{{MC_NS}}}Fallback"
# subtrees whose text must not leak into the paragraph text
_SKIP_TEXT = {TXBX, FALLBACK, w("delText"), w("instrText"), w("pPr"), w("rPr"), w("fldData"), w("del"),
              w("moveFrom")}
_HEADING_RE = re.compile(r"^(?:heading|제목)\s*(\d)$", re.I)


class DocxParser(BaseParser):
    file_type = "docx"
    extensions = (".docx", ".docm")

    def __init__(self, include_footnotes: bool = True, extract_images: bool = True,
                 include_comments: bool = True, revisions: str = "accept") -> None:
        """revisions: "accept" = text as it reads with every tracked change accepted (default);
        "markup" = show tracked changes as {++inserted++} / {--deleted--}."""
        if revisions not in ("accept", "markup"):
            raise ValueError("revisions must be 'accept' or 'markup'")
        self.include_footnotes = include_footnotes
        self.extract_images = extract_images
        self.include_comments = include_comments
        self.revisions = revisions

    def _extract(self, path: Path) -> ExtractionResult:
        check_zip_safety(path)
        try:
            document = docx.Document(str(path))
        except (PackageNotFoundError, KeyError, etree.XMLSyntaxError) as exc:
            raise CorruptedDocumentError(str(exc)) from exc

        self._styles = self._style_map(document)
        self._part = document.part
        self._list_styles = {sid for sid, (name, _) in self._styles.items() if "list" in name}
        self._walker = _TextWalker(self.revisions, comment_marks=self.include_comments)
        body = document.element.body
        blocks = self._blocks(body)
        if self.include_footnotes:
            blocks.extend(self._footnotes(document))
        comments = self._comments(document) if self.include_comments else []
        blocks.extend(comments)

        warnings: List[str] = []
        revision_count = sum(1 for el in body.iter(INS, DEL, MOVE_TO, MOVE_FROM)
                             if el.getparent() is not None and el.getparent().tag not in (w("rPr"), w("pPr")))
        if revision_count and self.revisions == "accept":
            warnings.append(f"{revision_count} tracked change(s): extracted as if all were accepted "
                            "(DocxParser(revisions='markup') shows them)")

        cp = document.core_properties
        meta = {
            "title": cp.title, "author": cp.author, "subject": cp.subject, "keywords": cp.keywords,
            "last_modified_by": cp.last_modified_by,
            "created": cp.created.isoformat() if cp.created else None,
            "modified": cp.modified.isoformat() if cp.modified else None,
            "section_count": len(document.sections),
            "column_layout": max((_col_count(s) for s in document.sections), default=1),
        }
        meta.update(_app_props(document))
        meta.update({"revision_count": revision_count, "comment_count": len(comments),
                     "revisions_mode": self.revisions if revision_count else None})
        return ExtractionResult(blocks=blocks, metadata=meta, warnings=warnings)

    # ------------------------------------------------------------------ styles

    @staticmethod
    def _style_map(document) -> Dict[str, Tuple[str, Optional[int]]]:
        """style_id -> (lower-case name, heading level or None) following basedOn chains."""
        raw = {}
        for s in document.styles.element.findall(w("style")):
            sid = s.get(w("styleId"))
            name_el, based_el = s.find(w("name")), s.find(w("basedOn"))
            lvl_el = s.find(f"{w('pPr')}/{w('outlineLvl')}")
            raw[sid] = (
                (name_el.get(w("val")) if name_el is not None else sid or "").lower(),
                based_el.get(w("val")) if based_el is not None else None,
                int(lvl_el.get(w("val"))) + 1 if lvl_el is not None and lvl_el.get(w("val"), "").isdigit() else None,
            )
        out = {}
        for sid, (name, _, _) in raw.items():
            level, cur, seen = None, sid, set()
            while cur in raw and cur not in seen and level is None:
                seen.add(cur)
                cname, based, olvl = raw[cur]
                m = _HEADING_RE.match(cname)
                level = int(m.group(1)) if m else (1 if cname == "title" else olvl)
                cur = based
            if level is not None and level > 9:
                level = None  # outlineLvl 9 == body text
            out[sid] = (name, level)
        return out

    # ------------------------------------------------------------------ block walk

    def _blocks(self, container) -> List[Block]:
        out: List[Block] = []
        for child in container:
            tag = child.tag
            if tag == P:
                out.extend(self._paragraph(child))
            elif tag == TBL:
                out.append(self._table(child))
            elif tag in (SDT, w("customXml"), w("ins"), w("smartTag")):
                content = child.find(w("sdtContent")) if tag == SDT else child
                if content is not None:
                    out.extend(self._blocks(content))
            elif tag == f"{{{MC_NS}}}AlternateContent":
                choice = child.find(f"{{{MC_NS}}}Choice")
                if choice is not None:
                    out.extend(self._blocks(choice))
        return out

    def _paragraph(self, p) -> List[Block]:
        parts: List[str] = []
        self._walker.collect(p, parts)
        text = "".join(parts).strip()
        out: List[Block] = []
        if text:
            out.append(self._classify(p, text))
        out.extend(self._images(p))
        # text boxes anchored in this paragraph (outermost only; nested ones are handled recursively)
        for box in p.iter(TXBX):
            if not _has_ancestor(box, {TXBX, FALLBACK}, stop=p):
                out.extend(self._blocks(box))
        return out

    def _images(self, p) -> List[Image]:
        """Pictures in this paragraph (DrawingML a:blip and legacy VML v:imagedata), in order."""
        if not self.extract_images:
            return []
        out: List[Image] = []
        for el in p.iter(A_BLIP, V_IMAGEDATA):
            if _has_ancestor(el, {TXBX, FALLBACK}, stop=p):
                continue
            rid = el.get(R_EMBED) if el.tag == A_BLIP else el.get(R_ID)
            part = self._part.related_parts.get(rid) if rid else None
            if part is None:
                continue
            alt, cur = "", el.getparent()
            while cur is not None and cur is not p:
                if cur.tag in (WP_INLINE, WP_ANCHOR):
                    doc_pr = cur.find(WP_DOCPR)
                    if doc_pr is not None:
                        alt = doc_pr.get("descr") or doc_pr.get("title") or ""
                    break
                cur = cur.getparent()
            out.append(Image(data=part.blob, ext=str(part.partname).rsplit(".", 1)[-1], alt=alt))
        return out

    def _classify(self, p, text: str) -> Block:
        ppr = p.find(w("pPr"))
        style_id, outline, num_level = None, None, None
        if ppr is not None:
            ps = ppr.find(w("pStyle"))
            style_id = ps.get(w("val")) if ps is not None else None
            ol = ppr.find(w("outlineLvl"))
            if ol is not None and ol.get(w("val"), "").isdigit() and int(ol.get(w("val"))) < 9:
                outline = int(ol.get(w("val"))) + 1
            ilvl = ppr.find(f"{w('numPr')}/{w('ilvl')}")
            if ppr.find(w("numPr")) is not None:
                num_level = int(ilvl.get(w("val"))) if ilvl is not None and ilvl.get(w("val"), "").isdigit() else 0
        name, level = self._styles.get(style_id, ("", None))
        level = outline or level
        if level:
            return Heading(text, min(level, 6))
        if num_level is not None or style_id in self._list_styles:
            return ListItem(text, level=num_level or 0, ordered="number" in name)
        return Paragraph(text)

    def _table(self, tbl) -> Table:
        cells: List[TableCell] = []
        open_vmerge: Dict[int, TableCell] = {}
        r = -1
        for tr in _iter_children(tbl, TR):
            trpr = tr.find(w("trPr"))
            if trpr is not None and trpr.find(DEL) is not None and self.revisions == "accept":
                continue  # row deleted with track changes on
            r += 1
            header = trpr is not None and trpr.find(w("tblHeader")) is not None
            col = _int_val(trpr.find(w("gridBefore")) if trpr is not None else None, 0)
            for tc in _iter_children(tr, TC):
                tcpr = tc.find(w("tcPr"))
                span = _int_val(tcpr.find(w("gridSpan")) if tcpr is not None else None, 1)
                vm = tcpr.find(w("vMerge")) if tcpr is not None else None
                vm_val = None if vm is None else (vm.get(w("val")) or "continue")
                if vm_val == "continue" and col in open_vmerge:
                    open_vmerge[col].rowspan += 1
                    col += span
                    continue
                cell = TableCell(row=r, col=col, colspan=span, is_header=header, blocks=self._blocks(tc))
                if vm_val == "restart":
                    open_vmerge[col] = cell
                else:
                    open_vmerge.pop(col, None)
                cells.append(cell)
                col += span
        return Table(cells=cells)

    # ------------------------------------------------------------------ comments

    def _comments(self, document) -> List[Block]:
        """Word comments (메모) as [^cN] notes: author, date, text and the commented text."""
        out: List[Block] = []
        for rel in document.part.rels.values():
            if rel.reltype != RT_COMMENTS or rel.is_external:
                continue
            root = etree.fromstring(rel.target_part.blob)
            for c in root.findall(w("comment")):
                cid = c.get(w("id"))
                parts: List[str] = []
                for p in c.iter(P):
                    _collect_text(p, parts)
                    parts.append(" ")
                text = re.sub(r"\s+", " ", "".join(parts)).strip()
                if not text:
                    continue
                who = ", ".join(x for x in (c.get(w("author")), (c.get(w("date")) or "")[:10]) if x)
                target = self._walker.targets.get(cid, "")
                note = f"[^c{cid}]: 메모({who}): {text}" if who else f"[^c{cid}]: 메모: {text}"
                if target:
                    note += f' — 대상: "{target}"'
                out.append(Paragraph(note))
        return out

    # ------------------------------------------------------------------ footnotes

    def _footnotes(self, document) -> List[Block]:
        out: List[Block] = []
        for rel in document.part.rels.values():
            if rel.reltype != RT_FOOTNOTES or rel.is_external:
                continue
            root = etree.fromstring(rel.target_part.blob)
            for fn in root.findall(w("footnote")):
                if fn.get(w("type")) in ("separator", "continuationSeparator", "continuationNotice"):
                    continue
                parts: List[str] = []
                for p in fn.iter(P):
                    _collect_text(p, parts)
                    parts.append(" ")
                text = "".join(parts).strip()
                if text:
                    out.append(Paragraph(f"[^{fn.get(w('id'))}]: {text}"))
        return out


# ---------------------------------------------------------------------- helpers


INS, DEL, MOVE_TO, MOVE_FROM, DEL_TEXT = w("ins"), w("del"), w("moveTo"), w("moveFrom"), w("delText")
C_START, C_END, C_REF = w("commentRangeStart"), w("commentRangeEnd"), w("commentReference")


class _TextWalker:
    """Collects run text of a paragraph.

    revisions="accept": the document as it reads after accepting every tracked change
                        (insertions kept, deletions and moved-away text dropped).
    revisions="markup": CriticMarkup — {++inserted++} and {--deleted--}.
    Comment anchors: the commented text is recorded per comment id, and a [^cN] marker is placed
    where Word shows the comment reference.
    """

    def __init__(self, revisions: str = "accept", comment_marks: bool = True) -> None:
        self.markup = revisions == "markup"
        self.comment_marks = comment_marks
        self.open: Dict[str, List[str]] = {}      # comment id -> text inside its range (still open)
        self.targets: Dict[str, str] = {}         # comment id -> commented text

    def collect(self, el, out: List[str], deleted: bool = False) -> None:
        for child in el:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag in (DEL, MOVE_FROM):
                if self.markup:
                    inner: List[str] = []
                    self.collect(child, inner, deleted=True)
                    if "".join(inner).strip():
                        self._emit(out, "{--" + "".join(inner) + "--}")
                continue
            if tag in (INS, MOVE_TO) and self.markup:
                inner = []
                self.collect(child, inner, deleted)
                if "".join(inner).strip():
                    self._emit(out, "{++" + "".join(inner) + "++}")
                continue
            if tag in _SKIP_TEXT:
                if tag == DEL_TEXT and deleted:
                    out.append(child.text or "")
                continue
            if tag == T:
                self._emit(out, child.text or "")
            elif tag == TAB:
                self._emit(out, "\t")
            elif tag in (BR, CR):
                self._emit(out, "\n")
            elif tag == w("noBreakHyphen"):
                self._emit(out, "-")
            elif tag == w("footnoteReference"):
                out.append(f"[^{child.get(w('id'))}]")
            elif tag == C_START:
                self.open[child.get(w("id"))] = []
            elif tag == C_END:
                cid = child.get(w("id"))
                self.targets[cid] = "".join(self.open.pop(cid, [])).strip()
            elif tag == C_REF:
                if self.comment_marks:
                    out.append(f"[^c{child.get(w('id'))}]")
            elif tag == P:  # nested paragraph (e.g. inside a sdt) -> keep separated
                self.collect(child, out, deleted)
                self._emit(out, "\n")
            else:
                self.collect(child, out, deleted)

    def _emit(self, out: List[str], text: str) -> None:
        out.append(text)
        for buf in self.open.values():
            buf.append(text)


def _collect_text(el, out: List[str]) -> None:
    """Accepted-view text of an element (used for footnotes/comments bodies)."""
    _TextWalker().collect(el, out)


def _iter_children(el, tag):
    """Direct children with `tag`, looking through content controls / custom XML wrappers."""
    for child in el:
        if child.tag == tag:
            yield child
        elif child.tag in (SDT, w("customXml")):
            inner = child.find(w("sdtContent")) if child.tag == SDT else child
            if inner is not None:
                yield from _iter_children(inner, tag)


def _has_ancestor(el, tags, stop) -> bool:
    cur = el.getparent()
    while cur is not None and cur is not stop:
        if cur.tag in tags:
            return True
        cur = cur.getparent()
    return False


def _int_val(el, default: int) -> int:
    if el is None:
        return default
    v = el.get(w("val"), "")
    return int(v) if v.isdigit() else default


def _col_count(section) -> int:
    cols = section._sectPr.find(w("cols"))
    return _int_val_attr(cols, "num", 1) if cols is not None else 1


def _int_val_attr(el, attr, default):
    v = el.get(w(attr), "")
    return int(v) if v.isdigit() else default


def _app_props(document) -> dict:
    try:
        for part in document.part.package.iter_parts():
            if str(part.partname) == "/docProps/app.xml":
                root = etree.fromstring(part.blob)
                out = {}
                for el in root:
                    local = etree.QName(el).localname
                    if local in ("Pages", "Words", "Characters", "Application") and el.text:
                        key = {"Pages": "page_count", "Words": "word_count", "Characters": "character_count",
                               "Application": "application"}[local]
                        out[key] = int(el.text) if el.text.isdigit() else el.text
                return out
    except Exception:
        pass
    return {}
