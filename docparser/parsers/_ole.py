"""Small helpers shared by the OLE (Compound File) based parsers."""
from __future__ import annotations

from typing import Any, Dict

import olefile


def decode_ole_str(value: Any, codepage: Any = None) -> Any:
    if not isinstance(value, bytes):
        return value
    encodings = []
    if isinstance(codepage, int) and codepage > 0:
        encodings.append("utf-8" if codepage == 65001 else f"cp{codepage}")
    encodings += ["utf-8", "cp949", "cp1252"]
    for enc in encodings:
        try:
            return value.decode(enc).rstrip("\x00").strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return value.decode("latin-1", "replace").rstrip("\x00").strip()


def summary_metadata(ole: olefile.OleFileIO) -> Dict[str, Any]:
    """Standard \\x05SummaryInformation fields (title/author/dates/page count)."""
    try:
        m = ole.get_metadata()
    except Exception:
        return {}
    cp = getattr(m, "codepage", None)
    out = {
        "title": decode_ole_str(m.title, cp),
        "author": decode_ole_str(m.author, cp),
        "subject": decode_ole_str(m.subject, cp),
        "keywords": decode_ole_str(m.keywords, cp),
        "last_modified_by": decode_ole_str(m.last_saved_by, cp),
        "created": m.create_time.isoformat() if m.create_time else None,
        "modified": m.last_saved_time.isoformat() if m.last_saved_time else None,
    }
    if isinstance(m.num_pages, int) and m.num_pages > 0:
        out["page_count"] = m.num_pages
    return out
