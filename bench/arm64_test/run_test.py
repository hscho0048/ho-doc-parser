"""arm64 컨테이너 안에서 PaddleOCR 한국어 동작 확인 — 페이지 1장(정답 있음) + 문서 속 그림 1장."""
import argparse
import platform
import re
import time
import unicodedata

import numpy as np
from PIL import Image


def squash(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", s or ""))


def make_ocr():
    import paddle
    from paddleocr import PaddleOCR
    print(f"paddle {paddle.__version__} · {platform.machine()} · {platform.platform()}", flush=True)
    return PaddleOCR(lang="korean", use_doc_orientation_classify=False, use_doc_unwarping=False,
                     use_textline_orientation=False, enable_mkldnn=False, cpu_threads=4,
                     text_detection_model_name="PP-OCRv5_mobile_det",
                     text_recognition_model_name="korean_PP-OCRv5_mobile_rec")


def run(ocr, path):
    img = np.asarray(Image.open(path).convert("RGB"))
    t = time.perf_counter()
    r = ocr.predict(img)[0]
    return r["rec_texts"], time.perf_counter() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download-only", action="store_true")
    a = ap.parse_args()
    t = time.perf_counter()
    ocr = make_ocr()
    print(f"load {time.perf_counter() - t:.1f}s", flush=True)
    if a.download_only:
        return
    from rapidfuzz.distance import Levenshtein
    gt = open("assets/page1_gt.txt", encoding="utf-8").read()
    texts, sec = run(ocr, "assets/page1.png")
    hyp = "".join(texts)
    cer = Levenshtein.distance(squash(gt), squash(hyp)) / len(squash(gt))
    print(f"PAGE  lines {len(texts)} · CER {cer:.3f} · {sec:.1f}s (에뮬레이션이라 시간은 참고 안 됨)")
    print("PAGE  head:", " / ".join(texts[:8]))
    texts, sec = run(ocr, "assets/figure.png")
    print(f"FIGURE lines {len(texts)} · {sec:.1f}s")
    print("FIGURE head:", " / ".join(texts[:10]))


if __name__ == "__main__":
    main()
