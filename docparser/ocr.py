"""Optional OCR for text inside pictures (fills Image.ocr_text). Fully local, Apache-2.0 stack.

Default engine: RapidOCR (onnxruntime) running PaddleOCR's PP-OCRv5 *mobile* models
(detector ~4.6 MB, Korean recognizer ~13 MB). Measured on a 4-image sample on a CPU-only laptop:

    PaddleOCR, default server/medium models   > 8 GB RAM (aborted)
    PaddleOCR, PP-OCRv5 mobile models          1.5 GB RAM, ~29 s / image
    RapidOCR,  PP-OCRv5 mobile models (this)   ~0.57 GB RAM, ~3.6 s / image   (same models, same text)
    RapidOCR,  + det_max_side=1280             ~0.49 GB RAM, ~2.8 s / image   (misses some small text)

    pip install rapidocr onnxruntime

    from docparser import parse_document
    from docparser.ocr import RapidOcrEngine, ocr_document
    doc = parse_document("report.pdf")
    ocr_document(doc, RapidOcrEngine())       # fills image.ocr_text, re-renders doc.text
"""
from __future__ import annotations

import io
import logging
from typing import Callable, List, Optional, Sequence, Tuple

from .model import Image, iter_images

logger = logging.getLogger("docparser")

Line = Tuple[Sequence[Sequence[float]], str, float]  # (4-point box, text, score)


class RapidOcrEngine:
    """RapidOCR with PP-OCRv5 mobile models. Created lazily; one instance is reused for all images."""

    def __init__(self, lang: str = "korean", det_max_side: Optional[int] = None, threads: int = -1,
                 min_score: float = 0.5) -> None:
        self.lang = lang
        self.det_max_side = det_max_side
        self.threads = threads
        self.min_score = min_score
        self._engine = None

    def _load(self):
        try:
            from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR
        except ImportError as exc:  # pragma: no cover
            raise ImportError("OCR needs: pip install rapidocr onnxruntime") from exc
        params = {
            "Det.lang_type": LangDet.CH, "Det.model_type": ModelType.MOBILE, "Det.ocr_version": OCRVersion.PPOCRV5,
            "Rec.lang_type": getattr(LangRec, self.lang.upper()), "Rec.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Global.use_cls": False, "Global.log_level": "error", "Global.text_score": self.min_score,
            "EngineConfig.onnxruntime.intra_op_num_threads": self.threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1 if self.threads > 0 else -1,
        }
        if self.det_max_side:
            params.update({"Det.limit_type": "max", "Det.limit_side_len": int(self.det_max_side)})
        self._engine = RapidOCR(params=params)

    def __call__(self, data: bytes) -> List[Line]:
        if self._engine is None:
            self._load()
        import numpy as np
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(data)) as im:
            arr = np.asarray(im.convert("RGB"))[:, :, ::-1]  # RGB -> BGR for OpenCV
        res = self._engine(arr)
        if res is None or res.boxes is None or not res.txts:
            return []
        return [(box.tolist(), txt, float(score)) for box, txt, score in zip(res.boxes, res.txts, res.scores)]


def lines_to_text(lines: List[Line]) -> str:
    """Group detected boxes into visual lines (top-to-bottom, then left-to-right)."""
    items = []
    for box, text, _ in lines:
        ys, xs = [p[1] for p in box], [p[0] for p in box]
        items.append((min(ys), max(ys), min(xs), text.strip()))
    items = [i for i in items if i[3]]
    items.sort(key=lambda i: (i[0], i[2]))
    rows: List[List[tuple]] = []
    for it in items:
        if rows:
            top, bottom = min(r[0] for r in rows[-1]), max(r[1] for r in rows[-1])
            overlap = min(bottom, it[1]) - max(top, it[0])
            if overlap > 0.5 * min(it[1] - it[0], bottom - top):
                rows[-1].append(it)
                continue
        rows.append([it])
    return "\n".join(" ".join(i[3] for i in sorted(r, key=lambda i: i[2])) for r in rows)


def ocr_document(doc, engine: Callable[[bytes], List[Line]], min_px: int = 32,
                 progress: Optional[Callable[[int, int], None]] = None) -> int:
    """OCR every unique picture of a parsed document; returns the number of images with text found.

    Pictures smaller than `min_px` on a side (icons, bullets) are skipped. Duplicated pictures share the
    result. Failures are logged and skipped — OCR never makes a successful parse fail.
    """
    images = doc.images
    cache = {}
    found = 0
    for k, img in enumerate(images, 1):
        if progress:
            progress(k, len(images))
        if img.width and img.height and min(img.width, img.height) < min_px:
            continue
        try:
            cache[img.name] = lines_to_text(engine(img.data))
        except Exception as exc:
            logger.warning("OCR failed for %s: %s", img.name, exc)
            cache[img.name] = ""
        found += bool(cache[img.name])
    for img in iter_images(doc.blocks):  # same picture used several times -> same text
        img.ocr_text = cache.get(img.name, img.ocr_text)
    doc.metadata["ocr_images"] = found
    doc.refresh_text()
    return found
