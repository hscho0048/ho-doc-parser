"""Upstage Document Parse-compatible output.

    {
      "api": "docparser", "model": "docparser-1.x",
      "elements": [{"id": 0, "category": "heading1", "page": 1,
                    "content": {"text": "...", "html": "<h1>...</h1>", "markdown": "# ..."}}, ...],
      "content": {"text": "...", "html": "...", "markdown": "..."},
      "usage": {"pages": 3},
      "metadata": {...}
    }

Categories follow Upstage: heading1, paragraph, list, table, caption, figure, footnote
(+ "level" on headings, "image" = saved file path on figures). `page` is null for formats without
fixed pages (DOCX, ODT, HWPX) — their page breaks are decided at render time.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

from .model import (Heading, Image, ListItem, PageMarker, Paragraph, Table, image_markdown, render_table,
                    render_table_html)

_FOOTNOTE = re.compile(r"^\[\^[^\]]+\]:")
_COMMENT = re.compile(r"^\[\^c(\d+|:[^\]]+)\]:")  # Word comments [^c2] / Excel cell notes [^c:Sheet!B5]
_PAGINATED = {"pdf", "pptx", "ppt"}


def _esc(text: str) -> str:
    return html.escape(text.strip(), quote=False).replace("\n", "<br>")  # quotes only matter in attributes


def _table_text(t: Table) -> str:
    return "\n".join(" | ".join(cell for cell in row) for row in t.grid())


def to_elements(doc) -> Dict[str, Any]:
    from . import __version__

    image_dir = doc.metadata.get("image_dir") or ""
    paginated = doc.file_type in _PAGINATED
    elements: List[Dict[str, Any]] = []
    page: Optional[int] = 1 if paginated else None

    def add(category: str, text: str, html_: str, markdown: str, **extra) -> None:
        elements.append({"id": len(elements), "category": category, "page": page,
                         "content": {"text": text, "html": html_, "markdown": markdown}, **extra})

    blocks = doc.blocks
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if isinstance(b, PageMarker):
            m = re.search(r"\d+", b.label)
            page = int(m.group()) if (m and paginated) else page
        elif isinstance(b, Heading):
            lvl = max(1, min(b.level, 6))
            add("heading1", b.text, f"<h{lvl}>{_esc(b.text)}</h{lvl}>", f"{'#' * lvl} {b.text}", level=b.level)
        elif isinstance(b, ListItem):
            items = []
            while i < len(blocks) and isinstance(blocks[i], ListItem):
                items.append(blocks[i])
                i += 1
            i -= 1
            tag = "ol" if items[0].ordered else "ul"
            add("list", "\n".join(x.text for x in items),
                f"<{tag}>" + "".join(f"<li>{_esc(x.text)}</li>" for x in items) + f"</{tag}>",
                "\n".join(f"{'  ' * x.level}{'1.' if x.ordered else '-'} {x.text}" for x in items))
        elif isinstance(b, Table):
            if b.caption:
                add("caption", b.caption, f"<caption>{_esc(b.caption)}</caption>", b.caption)
            body = Table(cells=b.cells)  # caption is emitted as its own element
            add("table", _table_text(body), render_table_html(body, image_dir), render_table(body, image_dir))
        elif isinstance(b, Image):
            if b.name:
                src = f"{image_dir}/{b.name}" if image_dir else b.name
                inner = f'<img src="{html.escape(src)}" alt="{html.escape(b.alt)}">'
                if b.ocr_text.strip():
                    inner += f"<p>{_esc(b.ocr_text)}</p>"
                add("figure", b.ocr_text.strip(), f"<figure>{inner}</figure>", image_markdown(b, image_dir),
                    image=src, alt=b.alt, size=[b.width, b.height])
        elif isinstance(b, Paragraph) and b.text.strip():
            t = b.text.strip()
            cat = "comment" if _COMMENT.match(t) else "footnote" if _FOOTNOTE.match(t) else "paragraph"
            add(cat, b.text, f"<p>{_esc(b.text)}</p>", b.text)
        i += 1

    return {
        "api": "docparser",
        "model": f"docparser-{__version__}",
        "elements": elements,
        "content": {
            "text": "\n\n".join(e["content"]["text"] for e in elements if e["content"]["text"]),
            "html": "\n".join(e["content"]["html"] for e in elements),
            "markdown": doc.text,
        },
        "usage": {"pages": doc.metadata.get("page_count") or doc.metadata.get("slide_count")},
        "metadata": doc.metadata,
    }
