"""Document model shared by every parser, plus Markdown/HTML rendering."""
from __future__ import annotations

import html
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union


@dataclass
class Heading:
    text: str
    level: int = 1
    kind: str = "heading"


@dataclass
class Paragraph:
    text: str
    kind: str = "paragraph"


@dataclass
class ListItem:
    text: str
    level: int = 0
    ordered: bool = False
    kind: str = "list_item"


@dataclass
class PageMarker:
    """Page / slide boundary. Rendered as an HTML comment so it does not pollute prose."""

    label: str
    kind: str = "page"


@dataclass
class Image:
    """Embedded picture at its position in the document. `name` is assigned by BaseParser."""

    data: bytes = field(repr=False)
    ext: str = ""            # png / jpg / gif / bmp / emf / wmf / tif ... (detected from bytes if empty)
    alt: str = ""
    name: str = ""
    width: Optional[int] = None   # pixels, when known
    height: Optional[int] = None
    ocr_text: str = ""            # text found inside the picture (filled by docparser.ocr, optional)
    kind: str = "image"

    @property
    def text(self) -> str:  # images carry no prose
        return ""


@dataclass
class TableCell:
    row: int
    col: int
    blocks: List["Block"] = field(default_factory=list)
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False

    @property
    def text(self) -> str:
        return "\n".join(t for t in (_inline_text(b) for b in self.blocks) if t)


@dataclass
class Table:
    cells: List[TableCell] = field(default_factory=list)
    caption: str = ""
    kind: str = "table"

    @property
    def n_rows(self) -> int:
        return max((c.row + c.rowspan for c in self.cells), default=0)

    @property
    def n_cols(self) -> int:
        return max((c.col + c.colspan for c in self.cells), default=0)

    @property
    def is_complex(self) -> bool:
        """Merged or nested cells cannot be expressed in a GFM table."""
        for c in self.cells:
            if c.rowspan > 1 or c.colspan > 1:
                return True
            if any(isinstance(b, Table) for b in c.blocks):
                return True
        return False

    def grid(self) -> List[List[str]]:
        """Dense row x col text grid (merged areas repeat the origin text)."""
        g = [["" for _ in range(self.n_cols)] for _ in range(self.n_rows)]
        for c in self.cells:
            for r in range(c.row, c.row + c.rowspan):
                for k in range(c.col, c.col + c.colspan):
                    g[r][k] = c.text
        return g


Block = Union[Heading, Paragraph, ListItem, PageMarker, Table, Image]


def _inline_text(block: Block) -> str:
    if isinstance(block, Table):
        return "\n".join(" | ".join(row) for row in block.grid())
    if isinstance(block, Image):
        return block.ocr_text  # text found inside a picture counts as cell text (empty without OCR)
    if isinstance(block, PageMarker):
        return ""
    return block.text


def iter_images(blocks: List[Block]) -> Iterator[Image]:
    for b in blocks:
        if isinstance(b, Image):
            yield b
        elif isinstance(b, Table):
            for c in b.cells:
                yield from iter_images(c.blocks)


# --------------------------------------------------------------------------- rendering


def _img_ref(img: Image, image_dir: str) -> str:
    return f"{image_dir}/{img.name}" if image_dir else img.name


def render_markdown(blocks: List[Block], image_dir: str = "images") -> str:
    out: List[str] = []
    prev_list = False
    for b in blocks:
        if isinstance(b, Heading):
            piece = f"{'#' * max(1, min(b.level, 6))} {b.text.strip()}"
        elif isinstance(b, Paragraph):
            piece = b.text.strip()
        elif isinstance(b, ListItem):
            marker = "1." if b.ordered else "-"
            piece = f"{'  ' * b.level}{marker} {b.text.strip()}"
        elif isinstance(b, PageMarker):
            piece = f"<!-- {b.label} -->"
        elif isinstance(b, Image):
            piece = image_markdown(b, image_dir)
        elif isinstance(b, Table):
            piece = render_table(b, image_dir)
        else:  # pragma: no cover - defensive
            continue
        if not piece.strip():
            continue
        is_list = isinstance(b, ListItem)
        # consecutive list items stay tight, everything else is separated by a blank line
        out.append(("\n" if (is_list and prev_list) else "\n\n") + piece if out else piece)
        prev_list = is_list
    return "".join(out).strip() + ("\n" if out else "")


def image_markdown(img: Image, image_dir: str = "images") -> str:
    """![alt](path), followed by the picture's OCR text as a quote block when available."""
    if not img.name:
        return ""
    piece = f"![{_md_alt(img.alt)}]({_img_ref(img, image_dir)})"
    if img.ocr_text.strip():
        piece += "\n\n" + "\n".join(f"> {line}" for line in img.ocr_text.strip().splitlines() if line.strip())
    return piece


def _md_alt(alt: str) -> str:
    return " ".join(alt.split()).replace("[", "(").replace("]", ")")


def render_table(table: Table, image_dir: str = "images") -> str:
    if not table.cells:
        return ""
    has_images = any(True for _ in iter_images([table]))
    body = render_table_html(table, image_dir) if (table.is_complex or has_images) else _render_table_gfm(table)
    if table.caption:
        return f"**{table.caption.strip()}**\n\n{body}"
    return body


def _md_cell(text: str) -> str:
    return text.strip().replace("|", "\\|").replace("\n", "<br>")


def _render_table_gfm(table: Table) -> str:
    g = table.grid()
    if not g:
        return ""
    lines = ["| " + " | ".join(_md_cell(x) for x in g[0]) + " |",
             "| " + " | ".join("---" for _ in g[0]) + " |"]
    for row in g[1:]:
        lines.append("| " + " | ".join(_md_cell(x) for x in row) + " |")
    return "\n".join(lines)


def render_table_html(table: Table, image_dir: str = "images") -> str:
    rows: Dict[int, List[TableCell]] = {}
    for c in table.cells:
        rows.setdefault(c.row, []).append(c)
    parts = ["<table>"]
    for r in range(table.n_rows):
        parts.append("<tr>")
        for c in sorted(rows.get(r, []), key=lambda x: x.col):
            tag = "th" if c.is_header else "td"
            attrs = ""
            if c.rowspan > 1:
                attrs += f' rowspan="{c.rowspan}"'
            if c.colspan > 1:
                attrs += f' colspan="{c.colspan}"'
            parts.append(f"<{tag}{attrs}>{_html_cell(c, image_dir)}</{tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _html_cell(cell: TableCell, image_dir: str) -> str:
    pieces = []
    for b in cell.blocks:
        if isinstance(b, Table):
            pieces.append(render_table_html(b, image_dir))
        elif isinstance(b, Image):
            if b.name:
                pieces.append(f'<img src="{html.escape(_img_ref(b, image_dir))}" alt="{html.escape(b.alt)}">')
                if b.ocr_text.strip():
                    pieces.append(html.escape(b.ocr_text.strip(), quote=False).replace("\n", "<br>"))
        elif not isinstance(b, PageMarker) and b.text.strip():
            pieces.append(html.escape(b.text.strip(), quote=False).replace("\n", "<br>"))  # quotes: attributes only
    return "<br>".join(pieces)


# --------------------------------------------------------------------------- result


@dataclass
class ParsedDocument:
    text: str
    file_type: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error_message: Optional[str] = None
    blocks: List[Block] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def tables(self) -> List[Table]:
        return [b for b in self.blocks if isinstance(b, Table)]

    @property
    def images(self) -> List[Image]:
        """Unique images (the same picture may appear several times in blocks)."""
        seen, out = set(), []
        for img in iter_images(self.blocks):
            if img.name not in seen:
                seen.add(img.name)
                out.append(img)
        return out

    def save(self, out_dir: Union[str, Path], md_name: Optional[str] = None) -> Path:
        """Write <out_dir>/<md_name>.md and the images next to it (paths match the markdown links)."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = md_name or Path(self.metadata.get("file_name", "document")).stem
        image_dir = self.metadata.get("image_dir")
        if image_dir and md_name:
            from .images import _safe
            if image_dir != f"{_safe(md_name)}_images":   # a.pdf and a.docx in one folder must not share a_images/
                self.metadata["image_dir"] = image_dir = f"{_safe(md_name)}_images"
                self.refresh_text()
        md_path = out_dir / f"{stem}.md"
        md_path.write_text(self.text, encoding="utf-8")
        if image_dir and self.images:
            (out_dir / image_dir).mkdir(parents=True, exist_ok=True)
            for img in self.images:
                (out_dir / image_dir / img.name).write_bytes(img.data)
        return md_path

    def refresh_text(self) -> None:
        """Re-render `text` from blocks (e.g. after OCR filled Image.ocr_text)."""
        from .normalize import normalize_text
        self.text = normalize_text(render_markdown(self.blocks, self.metadata.get("image_dir") or ""))
        self.metadata["char_count"] = len(self.text)

    def to_elements(self) -> Dict[str, Any]:
        """Upstage Document Parse-style structure: {"elements": [...], "content": {...}, "usage": {...}}."""
        from .export import to_elements
        return to_elements(self)

    def to_dict(self, include_blocks: bool = False) -> Dict[str, Any]:
        d = asdict(self)
        if include_blocks:
            _strip_image_bytes(d["blocks"])
        else:
            d.pop("blocks")
        return d

    @classmethod
    def failure(cls, file_type: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> "ParsedDocument":
        return cls(text="", file_type=file_type, metadata=metadata or {}, success=False, error_message=message)


def _strip_image_bytes(obj: Any) -> None:
    if isinstance(obj, dict):
        if obj.get("kind") == "image" and "data" in obj:
            obj["size_bytes"] = len(obj.pop("data") or b"")
        for v in obj.values():
            _strip_image_bytes(v)
    elif isinstance(obj, list):
        for v in obj:
            _strip_image_bytes(v)
