"""PDF parser — pdfplumber / pdfminer.six (both MIT). PyMuPDF is deliberately not used (AGPL).

Layout reconstruction
  1. Ruled tables via pdfplumber.find_tables(); cell boxes are snapped to a grid to derive
     rowspan/colspan, words are assigned to cells by their centre point.
  2. Remaining words (+ each table as one opaque item) are grouped into rows by vertical overlap.
  3. Multi-column detection: consecutive rows are merged into a segment while the *intersection* of
     their horizontal whitespace still contains an interior gap ("gutter"). A segment with a valid
     gutter is split into columns, each column is laid out recursively (handles full-width title +
     2 columns + full-width table + 3 columns on the same page).
  4. Lines -> paragraphs by spacing/indent/bullets; headings from font size relative to body text.
  5. Lines repeated at the top/bottom of most pages (running headers, page numbers) are removed.
Pages without a text layer are reported in metadata['needs_ocr_pages'] (OCR is out of scope).
"""
from __future__ import annotations

import re
import io
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

import pdfplumber

from ..base import BaseParser, ExtractionResult
from ..errors import CorruptedDocumentError, EncryptedDocumentError
from pdfminer.psparser import literal_name

from ..model import Block, Heading, Image, ListItem, PageMarker, Paragraph, Table, TableCell

_BULLET_RE = re.compile(r"^(?:[•●○◦▪■□◆◇▶►‣⁃\-–—*·ㅇ]\s|\(?\d{1,3}[.)]\s|[①-⑳]|[가-하][.)]\s|\(?[a-zA-Z][.)]\s)")
_LIST_STRIP_RE = re.compile(r"^(?:[•●○◦▪■□◆◇▶►‣⁃\-–—*·ㅇ])\s+")


@dataclass
class Item:
    x0: float
    x1: float
    top: float
    bottom: float
    text: str = ""
    size: float = 0.0
    obj: Optional[Union[Table, Image]] = None  # table or picture treated as one opaque layout item

    @property
    def h(self) -> float:
        return max(self.bottom - self.top, 0.1)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Line:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    n_words: int = 1

    @property
    def h(self) -> float:
        return max(self.bottom - self.top, 0.1)


Chunk = Union[Table, Image, List[Line]]  # a table / picture, or a run of lines from one column


@dataclass
class _Row:
    items: List[Item] = field(default_factory=list)
    top: float = 0.0
    bottom: float = 0.0
    line_h: float = 0.0


class PdfParser(BaseParser):
    file_type = "pdf"
    extensions = (".pdf",)

    def __init__(self, password: Optional[str] = None, detect_tables: bool = True,
                 detect_columns: bool = True, remove_headers_footers: bool = True,
                 max_pages: Optional[int] = None, extract_images: bool = True,
                 image_resolution: int = 150, min_image_pt: float = 24.0) -> None:
        self.password = password
        self.detect_tables = detect_tables
        self.detect_columns = detect_columns
        self.remove_headers_footers = remove_headers_footers
        self.max_pages = max_pages
        self.extract_images = extract_images
        self.image_resolution = image_resolution
        self.min_image_pt = min_image_pt

    # ------------------------------------------------------------------ entry point

    def _extract(self, path: Path) -> ExtractionResult:
        try:
            pdf = pdfplumber.open(str(path), password=self.password or "")
        except Exception as exc:
            raise _translate(exc) from exc
        warnings: List[str] = []
        with pdf:
            try:
                n_pages = len(pdf.pages)
            except Exception as exc:
                raise _translate(exc) from exc
            meta = _metadata(pdf)
            meta["page_count"] = n_pages
            pages: List[Tuple[float, List[Chunk]]] = []
            sizes: Counter = Counter()
            needs_ocr: List[int] = []
            column_pages = 0
            for i, page in enumerate(pdf.pages[: self.max_pages] if self.max_pages else pdf.pages, 1):
                try:
                    chunks, page_sizes, n_cols, empty = self._page(page)
                except Exception as exc:
                    warnings.append(f"page {i}: skipped ({type(exc).__name__}: {exc})")
                    chunks, page_sizes, n_cols, empty = [], Counter(), 1, False
                pages.append((float(page.height), chunks))
                sizes.update(page_sizes)
                column_pages += n_cols > 1
                if empty and page.images:
                    needs_ocr.append(i)
                page.close()
        if needs_ocr:
            meta["needs_ocr_pages"] = needs_ocr
            warnings.append(f"{len(needs_ocr)} page(s) have no text layer (scanned?) - OCR not applied")
        meta["multi_column_pages"] = column_pages

        if self.remove_headers_footers and len(pages) >= 3:
            _strip_running_lines(pages)
        body = _body_size(sizes)
        blocks: List[Block] = []
        for i, (_, chunks) in enumerate(pages, 1):
            blocks.append(PageMarker(f"page {i}"))
            for chunk in chunks:
                if isinstance(chunk, (Table, Image)):
                    blocks.append(chunk)
                else:
                    blocks.extend(_paragraphs(chunk, body))
        return ExtractionResult(blocks=blocks, metadata=meta, warnings=warnings)

    # ------------------------------------------------------------------ page

    def _page(self, page):
        if _has_duplicate_glyphs(page.chars):  # fake-bold (glyph drawn twice); dedupe is costly, so only when needed
            page = page.dedupe_chars()
        # no extra_attrs=["size"]: mixed CJK/Latin fonts report float-noisy sizes (9.96000001 vs 9.9599999)
        # which would split "표" from "," — size is taken from the word's characters instead
        words = page.extract_words(x_tolerance=2.5, y_tolerance=3, keep_blank_chars=False,
                                   use_text_flow=False, return_chars=True)
        for w in words:
            w["size"] = round(statistics.median(c["size"] for c in w["chars"]), 1) if w.get("chars") else 0.0
            w.pop("chars", None)
        found = []
        if self.detect_tables and (page.rects or page.lines or page.curves):
            found = [t for t in page.find_tables() if len(t.cells) >= 2]
        items: List[Item] = []
        table_words: List[List[dict]] = [[] for _ in found]
        sizes: Counter = Counter()
        for w in words:
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            sizes[w["size"]] += len(w["text"])
            for k, t in enumerate(found):
                x0, top, x1, bottom = t.bbox
                if x0 <= cx <= x1 and top <= cy <= bottom:
                    table_words[k].append(w)
                    break
            else:
                items.append(Item(w["x0"], w["x1"], w["top"], w["bottom"], w["text"], float(w.get("size", 0))))
        for t, tw in zip(found, table_words):
            x0, top, x1, bottom = t.bbox
            table = _build_table(t.cells, tw)
            if any(c.blocks for c in table.cells):  # ruled boxes without text are decoration
                items.append(Item(x0, x1, top, bottom, obj=table))
        if self.extract_images:
            page_area = float(page.width * page.height) or 1.0
            for im in page.images:
                x0, top = max(float(im["x0"]), 0.0), max(float(im["top"]), 0.0)
                x1, bottom = min(float(im["x1"]), float(page.width)), min(float(im["bottom"]), float(page.height))
                if x1 - x0 < self.min_image_pt or bottom - top < self.min_image_pt:
                    continue  # bullets, rules, tiny icons
                if words and (x1 - x0) * (bottom - top) > 0.85 * page_area:
                    continue  # full-page background behind real text
                img = self._image(page, im, (x0, top, x1, bottom))
                if img is not None:
                    items.append(Item(x0, x1, top, bottom, obj=img))
        if not items:
            return [], sizes, 1, not words
        med_h = statistics.median(i.h for i in items if i.obj is None) if any(
            i.obj is None for i in items) else 10.0
        self._min_gutter = max(8.0, 1.2 * med_h)
        self._med_h = med_h
        self._max_cols = 1
        chunks = self._layout(items, 0) if self.detect_columns else self._flat(items)
        return chunks, sizes, self._max_cols, not words

    def _image(self, page, im: dict, bbox) -> Optional[Image]:
        """Embedded JPEGs are returned byte-for-byte; anything else is rendered (pdfium) to PNG."""
        stream = im.get("stream")
        try:
            filters = [literal_name(f) for f, _ in stream.get_filters()] if stream is not None else []
            if filters == ["DCTDecode"]:
                return Image(data=stream.get_rawdata(), ext="jpg")
        except Exception:
            pass
        try:
            rendered = page.crop(bbox, strict=False).to_image(resolution=self.image_resolution).original
            buf = io.BytesIO()
            rendered.save(buf, "PNG")
            return Image(data=buf.getvalue(), ext="png")
        except Exception:
            return None

    def _flat(self, items: List[Item]) -> List[Chunk]:
        out: List[Chunk] = []
        for row in _rows(items):
            out.extend(_row_chunks(row))
        return _merge_line_chunks(out)

    def _layout(self, items: List[Item], depth: int) -> List[Chunk]:
        rows = _rows(items)
        out: List[Chunk] = []
        for seg_rows, gutters in self._segments(rows):
            groups = self._split(seg_rows, gutters) if gutters and depth < 6 else None
            if groups:
                self._max_cols = max(self._max_cols, len(groups))
                for g in groups:
                    out.extend(self._layout(g, depth + 1))
            else:
                for row in seg_rows:
                    out.extend(_row_chunks(row))
        return _merge_line_chunks(out)

    # ------------------------------------------------------------------ column detection

    def _segments(self, rows: List[_Row]):
        if not rows:
            return []
        X0 = min(i.x0 for r in rows for i in r.items)
        X1 = max(i.x1 for r in rows for i in r.items)
        segs = []
        cur_rows: List[_Row] = []
        cur_free: List[Tuple[float, float]] = []
        cur_ext = (0.0, 0.0)
        for row in rows:
            rfree = _free(row.items, X0, X1)
            rext = (min(i.x0 for i in row.items), max(i.x1 for i in row.items))
            if not cur_rows:
                cur_rows, cur_free, cur_ext = [row], rfree, rext
                continue
            new_free = _intersect(cur_free, rfree)
            new_ext = (min(cur_ext[0], rext[0]), max(cur_ext[1], rext[1]))
            g_cur = self._gutters(cur_free, cur_ext)
            g_new = self._gutters(new_free, new_ext)
            g_row = self._gutters(rfree, rext)
            # close the segment when its columns end, or when a single-column run turns multi-column,
            # or when a row sits far below the column it would belong to (heading under a section)
            if (g_cur and not g_new) or (not g_cur and g_row) or \
                    (g_cur and not g_row and self._detached(row, cur_rows, g_cur)):
                segs.append((cur_rows, g_cur))
                cur_rows, cur_free, cur_ext = [row], rfree, rext
            else:
                cur_rows.append(row)
                cur_free, cur_ext = new_free, new_ext
        segs.append((cur_rows, self._gutters(cur_free, cur_ext)))
        return segs

    def _detached(self, row: _Row, seg_rows: List[_Row], gutters) -> bool:
        """True if `row` lies in one column but much further below that column's last line than
        the column's usual line pitch — i.e. it follows the multi-column block instead of continuing it."""
        cuts = sorted((a + b) / 2 for a, b in gutters)
        sides = {sum(1 for c in cuts if i.cx > c) for i in row.items}
        if len(sides) != 1:
            return False
        side = sides.pop()

        def side_of(i: Item) -> int:
            return sum(1 for c in cuts if i.cx > c)

        # item level (not row level): a tall table row may have swallowed neighbouring lines
        all_items = [i for r in seg_rows for i in r.items]
        side_items = [i for i in all_items if side_of(i) == side]
        other_bottoms = [i.bottom for i in all_items if side_of(i) != side]
        if not side_items or not other_bottoms:
            return True
        if row.top <= max(other_bottoms):
            return False  # another column is still running alongside -> continuous column flow
        tops = sorted({round(i.top, 1) for i in side_items if i.obj is None})
        pitches = [b - a for a, b in zip(tops, tops[1:]) if b - a > 1]
        base = statistics.median(pitches) if pitches else 2.0 * self._med_h
        ref = max(tops[-1] if tops else float("-inf"),
                  max((i.bottom - self._med_h for i in side_items if i.obj is not None), default=float("-inf")))
        if row.top - ref > 1.8 * base:
            return True
        # set in a different font size than the column text -> heading that follows the block
        row_sizes = [i.size for i in row.items if i.size]
        seg_sizes = [i.size for i in all_items if i.size]
        return bool(row_sizes and seg_sizes and
                    abs(statistics.median(row_sizes) - statistics.median(seg_sizes)) > 1.0)

    def _gutters(self, free, ext) -> List[Tuple[float, float]]:
        lo, hi = ext
        return [g for g in free if g[1] - g[0] >= self._min_gutter and g[0] > lo + 1 and g[1] < hi - 1]

    def _split(self, rows: List[_Row], gutters) -> Optional[List[List[Item]]]:
        cuts = sorted((a + b) / 2 for a, b in gutters)
        groups: List[List[Item]] = [[] for _ in range(len(cuts) + 1)]
        for r in rows:
            for it in r.items:
                k = sum(1 for c in cuts if it.cx > c)
                groups[k].append(it)
        groups = [g for g in groups if g]
        if len(groups) < 2:
            return None
        two_sided = sum(1 for r in rows if len({sum(1 for c in cuts if i.cx > c) for i in r.items}) > 1)
        tall = any(r.bottom - r.top > 3 * self._med_h for r in rows)
        if two_sided < 2 and not tall:
            return None
        for g in groups:
            n_lines = len(_rows(g))
            n_words = sum(10 if i.obj is not None else 1 for i in g)
            if n_lines < 2 or n_words / n_lines < 2.5:
                return None  # looks like a borderless key/value form, keep row order
        return groups


# ---------------------------------------------------------------------- geometry helpers


def _has_duplicate_glyphs(chars) -> bool:
    seen = set()
    dup = 0
    for c in chars:
        key = (c["text"], round(c["x0"]), round(c["top"]))
        if key in seen:
            dup += 1
            if dup > 3:
                return True
        seen.add(key)
    return False


def _rows(items: Sequence[Item]) -> List[_Row]:
    rows: List[_Row] = []
    for it in sorted(items, key=lambda i: (i.top, i.x0)):
        if rows:
            r = rows[-1]
            overlap = min(r.bottom, it.bottom) - max(r.top, it.top)
            if overlap > 0.5 * min(it.h, r.line_h):
                r.items.append(it)
                r.bottom = max(r.bottom, it.bottom)
                if it.obj is None:
                    r.line_h = min(r.line_h, it.h)
                continue
        rows.append(_Row([it], it.top, it.bottom, it.h))
    return rows


def _free(items: Sequence[Item], lo: float, hi: float) -> List[Tuple[float, float]]:
    spans = sorted((i.x0, i.x1) for i in items)
    free, cur = [], lo
    for a, b in spans:
        if a > cur:
            free.append((cur, a))
        cur = max(cur, b)
    if cur < hi:
        free.append((cur, hi))
    return free


def _intersect(a, b) -> List[Tuple[float, float]]:
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _row_chunks(row: _Row) -> List[Chunk]:
    tables = sorted((i for i in row.items if i.obj is not None), key=lambda i: i.top)
    words = [i for i in row.items if i.obj is None]
    if not tables:
        return [[_line(words)]] if words else []
    out: List[Chunk] = []
    left = [w for w in words if w.x1 <= tables[0].x0]
    rest = [w for w in words if w not in left]
    for r in _rows(left):
        out.append([_line(r.items)])
    out.extend(t.obj for t in tables if not (isinstance(t.obj, Table) and not t.obj.cells))
    for r in _rows(rest):
        out.append([_line(r.items)])
    return out


def _line(words: List[Item]) -> Line:
    words = sorted(words, key=lambda w: w.x0)
    sizes = [w.size for w in words if w.size]
    return Line(" ".join(w.text for w in words), min(w.x0 for w in words), max(w.x1 for w in words),
                min(w.top for w in words), max(w.bottom for w in words),
                statistics.median(sizes) if sizes else 0.0, len(words))


def _merge_line_chunks(chunks: List[Chunk]) -> List[Chunk]:
    out: List[Chunk] = []
    for c in chunks:
        if isinstance(c, list) and out and isinstance(out[-1], list):
            out[-1].extend(c)
        else:
            out.append(c)
    return out


# ---------------------------------------------------------------------- tables


def _cluster(values: List[float], tol: float = 2.5) -> List[float]:
    out: List[float] = []
    for v in sorted(values):
        if out and v - out[-1] <= tol:
            continue
        out.append(v)
    return out


def _nearest(arr: List[float], v: float) -> int:
    return min(range(len(arr)), key=lambda k: abs(arr[k] - v))


def _build_table(cells_bbox, words: List[dict]) -> Table:
    xs = _cluster([c[0] for c in cells_bbox] + [c[2] for c in cells_bbox])
    ys = _cluster([c[1] for c in cells_bbox] + [c[3] for c in cells_bbox])
    cell_words: List[List[dict]] = [[] for _ in cells_bbox]
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        best, best_area = None, None
        for k, (x0, top, x1, bottom) in enumerate(cells_bbox):
            if x0 - 1 <= cx <= x1 + 1 and top - 1 <= cy <= bottom + 1:
                area = (x1 - x0) * (bottom - top)
                if best_area is None or area < best_area:
                    best, best_area = k, area
        if best is not None:
            cell_words[best].append(w)
    seen = set()
    cells: List[TableCell] = []
    for (x0, top, x1, bottom), ws in zip(cells_bbox, cell_words):
        c0, c1, r0, r1 = _nearest(xs, x0), _nearest(xs, x1), _nearest(ys, top), _nearest(ys, bottom)
        if (r0, c0) in seen:
            continue
        seen.add((r0, c0))
        text = _cell_text(ws)
        cells.append(TableCell(row=r0, col=c0, rowspan=max(1, r1 - r0), colspan=max(1, c1 - c0),
                               blocks=[Paragraph(text)] if text else []))
    return Table(cells=sorted(cells, key=lambda c: (c.row, c.col)))


def _cell_text(words: List[dict]) -> str:
    if not words:
        return ""
    items = [Item(w["x0"], w["x1"], w["top"], w["bottom"], w["text"]) for w in words]
    return "\n".join(_line(r.items).text for r in _rows(items))


# ---------------------------------------------------------------------- text assembly


def _body_size(sizes: Counter) -> float:
    if not sizes:
        return 0.0
    return sizes.most_common(1)[0][0]


def _paragraphs(lines: List[Line], body: float) -> List[Block]:
    out: List[Block] = []
    cur: List[Line] = []

    def flush():
        if not cur:
            return
        text = ""
        for ln_ in cur:
            if not text:
                text = ln_.text
            elif text.endswith("-") and len(text) > 1 and text[-2].isalpha() and ln_.text[:1].islower():
                text = text[:-1] + ln_.text
            else:
                text += " " + ln_.text
        size = statistics.median(l.size for l in cur)
        ratio = size / body if body else 1.0
        if ratio >= 1.18 and len(text) <= 150 and len(cur) <= 3:
            out.append(Heading(text, 1 if ratio >= 1.8 else 2 if ratio >= 1.4 else 3))
        elif _BULLET_RE.match(text):
            out.append(ListItem(_LIST_STRIP_RE.sub("", text)))
        else:
            out.append(Paragraph(text))
        cur.clear()

    # Line pitch differs wildly between fonts (Malgun Gothic leading ~2x font size, Latin ~1.35x), so a
    # paragraph break is a pitch that is clearly larger than the pitch of its neighbouring lines.
    pitch_at = [lines[k + 1].top - lines[k].top for k in range(len(lines) - 1)]

    for idx, ln_ in enumerate(lines):
        if cur:
            prev = cur[-1]
            lh = min(prev.h, ln_.h)
            pitch = ln_.top - prev.top
            neighbours = [p for p in (pitch_at[idx - 2] if idx >= 2 and len(cur) >= 2 else None,
                                      pitch_at[idx] if idx < len(pitch_at) else None)
                          if p is not None and 0 < p < 4 * lh]
            big_gap = (pitch > 1.2 * min(neighbours)) if neighbours else False
            big_gap = big_gap or (ln_.top - prev.bottom > 1.5 * lh)
            new_para = (
                big_gap
                or pitch < -0.5 * lh  # jumped back up: different column/fragment
                or abs(ln_.size - prev.size) > 0.6
                or _BULLET_RE.match(ln_.text) is not None
                or (len(cur) > 1 and ln_.x0 > prev.x0 + 1.5 * lh)  # first-line indent of next paragraph
            )
            if new_para:
                flush()
        cur.append(ln_)
    flush()
    return out


def _running_key(text: str) -> str:
    text = text.strip().lower()
    # digits are wildcards only in short lines ("- 3 -", "page 3 of 9"); long lines must repeat verbatim
    return re.sub(r"\d+", "#", text) if len(text) <= 40 else text


def _strip_running_lines(pages: List[Tuple[float, List[Chunk]]]) -> None:
    """Remove page headers/footers: lines in the top/bottom 8% that repeat on >=50% of pages."""
    counts: Counter = Counter()
    for height, chunks in pages:
        keys = set()
        for c in chunks:
            if isinstance(c, list):
                for l in c:
                    if l.top < height * 0.08 or l.bottom > height * 0.92:
                        keys.add(_running_key(l.text))
        counts.update(keys)
    threshold = max(3, int(len(pages) * 0.5))
    repeated = {k for k, v in counts.items() if v >= threshold and k}
    if not repeated:
        return
    for height, chunks in pages:
        for c in chunks:
            if isinstance(c, list):
                c[:] = [l for l in c if not ((l.top < height * 0.08 or l.bottom > height * 0.92)
                                             and _running_key(l.text) in repeated)]


# ---------------------------------------------------------------------- metadata / errors


def _pdf_date(v: str) -> str:
    m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", v or "")
    if not m:
        return v
    y, mo, d, h, mi, s = (g or "00" for g in m.groups())
    return f"{y}-{mo if mo != '00' else '01'}-{d if d != '00' else '01'}T{h}:{mi}:{s}"


def _metadata(pdf) -> dict:
    raw = pdf.metadata or {}

    def s(key):
        v = raw.get(key)
        if v is None:
            return None
        if isinstance(v, bytes):
            v = v.decode("utf-8", "ignore")
        return str(v).strip() or None

    return {
        "title": s("Title"), "author": s("Author"), "subject": s("Subject"), "keywords": s("Keywords"),
        "creator": s("Creator"), "producer": s("Producer"),
        "created": _pdf_date(s("CreationDate") or ""), "modified": _pdf_date(s("ModDate") or ""),
        "encrypted": bool(getattr(pdf.doc, "encryption", None)),
    }


def _translate(exc: BaseException) -> Exception:
    chain, e = [], exc
    while e is not None and len(chain) < 5:
        chain.append(f"{type(e).__name__}: {e}")
        e = e.__cause__ or e.__context__
    joined = " | ".join(chain)
    if re.search(r"password|encrypt", joined, re.I):
        return EncryptedDocumentError("PDF requires a password (pass PdfParser(password=...))")
    return CorruptedDocumentError(joined)
