"""Image helpers: format sniffing, de-duplication/naming and conversion to web-viewable formats."""
from __future__ import annotations

import hashlib
import io
import re
import struct
from typing import Dict, List, Optional

from .model import Block, Image, iter_images

_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"), (b"BM", "bmp"),
    (b"II*\x00", "tif"), (b"MM\x00*", "tif"), (b"\xd7\xcd\xc6\x9a", "wmf"), (b"RIFF", "webp"),
    (b"\x01\x00\x09\x00", "wmf"), (b"%PDF", "pdf"),
]
VIEWABLE = {"png", "jpg", "gif", "webp", "svg"}


def sniff_ext(data: bytes, fallback: str = "bin") -> str:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if len(data) > 44 and data[40:44] == b" EMF":
        return "emf"
    head = data[:256].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048].lower()):
        return "svg"
    fb = (fallback or "bin").lower().lstrip(".")
    return {"jpeg": "jpg", "tiff": "tif"}.get(fb, fb)


def dib_to_bmp(dib: bytes) -> bytes:
    """Prefix a BITMAPFILEHEADER to a packed DIB (as stored by Office binary formats)."""
    if len(dib) < 40:
        return dib
    header_size, = struct.unpack_from("<I", dib, 0)
    bit_count, = struct.unpack_from("<H", dib, 14)
    colors_used = struct.unpack_from("<I", dib, 32)[0] if header_size >= 36 else 0
    palette = (colors_used or (1 << bit_count if bit_count <= 8 else 0)) * 4
    offset = 14 + header_size + palette
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, offset) + dib


def _to_png(data: bytes) -> Optional[bytes]:
    try:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(data)) as im:
            if im.format in ("WMF", "EMF"):
                im.load(dpi=144) if hasattr(im, "load") else None
            out = io.BytesIO()
            (im if im.mode in ("RGB", "RGBA", "L", "P") else im.convert("RGBA")).save(out, "PNG")
            return out.getvalue()
    except Exception:  # Pillow renders WMF/EMF on Windows only; keep the original elsewhere
        return None


def _safe(stem: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", stem, flags=re.UNICODE).strip("_")
    return (s or "document")[:80]


def finalize_images(blocks: List[Block], stem: str, convert: bool = True) -> Dict[str, object]:
    """Assign stable unique file names (identical pictures share one file). Returns metadata."""
    by_hash: Dict[str, str] = {}
    converted: Dict[str, bytes] = {}
    count = 0
    for img in iter_images(blocks):
        digest = hashlib.sha1(img.data).hexdigest()
        if digest in by_hash:
            img.name = by_hash[digest]
            img.data = converted.get(digest, img.data)
            continue
        ext = sniff_ext(img.data, img.ext)
        if convert and ext in ("bmp", "tif", "wmf", "emf"):
            png = _to_png(img.data)
            if png:
                img.data, ext = png, "png"
        if img.width is None:
            _fill_size(img)
        count += 1
        img.ext = ext
        img.name = f"img_{count:03d}.{ext}"
        by_hash[digest] = img.name
        converted[digest] = img.data
    return {"image_count": count, "image_dir": f"{_safe(stem)}_images" if count else None}


def _fill_size(img: Image) -> None:
    try:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(img.data)) as im:
            img.width, img.height = im.size
    except Exception:
        pass
