"""로컬 OCR 벤치 — PaddleOCR / RapidOCR(PP-OCR ONNX) 한국어 성능 + CPU·RAM·가상 메모리.

    # OCR 전용 환경 (Python 3.12):
    #   uv venv .venv-ocr --python 3.12
    #   uv pip install --python .venv-ocr/Scripts/python.exe -e . pypdfium2 paddlepaddle paddleocr rapidocr rapidfuzz psutil
    python bench/make_ocr_pdfs.py test_file                      # 정답 PDF (Windows + Word, .venv)
    python bench/ocr_bench.py --pages 6 --max-images 40         # 엔진마다 따로 프로세스로 돌리고 합친다

A. 페이지 OCR (정답 있음) — make_ocr_pdfs.py 로 만든 PDF 의 텍스트층 = 정답
   clean: 200dpi 컬러 / scan: 150dpi 흑백 · 0.6° 기울임 · 흐림 · 잡음 · JPEG q45 (스캔본 흉내)
   지표: CER(공백 제외 글자 편집거리 ÷ 정답 글자 수, 낮을수록 좋음) · 글자 바이그램 F1(순서 무관) · 쪽당 초
B. 문서 속 이미지 OCR (정답 없음) — docparser 가 꺼낸 이미지(150px 이상)
   글자 수 · 평균 신뢰도 · paddle 과의 일치(F1) · 장당 초 + 갤러리 HTML(눈으로 확인)
C. 자원 — 엔진마다 별도 프로세스로 측정 (0.25초 간격)
   RAM = 작업 집합(RSS) 최대 · 가상 메모리 = 커밋된 전용 메모리(Windows private / Linux VMS) 최대
   CPU = 프로세스 CPU 평균·최대(코어 수 단위, 1.0 = 코어 1개 100%) · 모델 로딩 시간·메모리 증가분

산출물: compare_out/ocr/ocr_report.md · ocr_report.json · gallery.html · runs/<엔진>.json
"""
from __future__ import annotations

import argparse
import html
import io
import json
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np
import pdfplumber
import psutil
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from rapidfuzz.distance import Levenshtein

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docparser import DocumentParserRegistry  # noqa: E402

OUT = ROOT / "compare_out" / "ocr"
RUNS = OUT / "runs"
LOCAL_EXT = {".docx", ".pptx", ".ppt", ".odt", ".hwp", ".hwpx", ".pdf"}
MB = 1024 ** 2


# ----------------------------------------------------------------------------- engines

class Engine:
    name = ""
    config: dict = {}

    def __call__(self, img: np.ndarray) -> list[tuple[list, str, float]]:
        """(box[4 points], text, score) 목록"""
        raise NotImplementedError


class Paddle(Engine):
    def __init__(self, det: str = "mobile") -> None:
        from paddleocr import PaddleOCR
        # - Windows + paddle 3.3: oneDNN 경로에서 NotImplementedError → enable_mkldnn=False
        # - oneDNN 없이 server 검출은 쪽당 ~270초(측정) → 기본은 mobile 검출(~13–30초)
        # - 검출 모델 이름을 지정하면 lang="korean" 이 인식 모델에 적용되지 않아 중국어 모델로 바뀐다
        #   (한글이 통째로 빠짐, 측정) → 인식 모델도 korean 으로 명시
        self.name = "paddle" if det == "mobile" else f"paddle_{det}det"
        self.config = {"det": f"PP-OCRv5_{det}_det", "rec": "korean_PP-OCRv5_mobile_rec",
                       "enable_mkldnn": False, "cpu_threads": 8}
        self.ocr = PaddleOCR(lang="korean", use_doc_orientation_classify=False, use_doc_unwarping=False,
                             use_textline_orientation=False, enable_mkldnn=False, cpu_threads=8,
                             text_detection_model_name=f"PP-OCRv5_{det}_det",
                             text_recognition_model_name="korean_PP-OCRv5_mobile_rec")

    def __call__(self, img):
        res = self.ocr.predict(img)[0]
        return [(np.asarray(p).tolist(), t, float(s))
                for p, t, s in zip(res["rec_polys"], res["rec_texts"], res["rec_scores"])]


class Rapid(Engine):
    def __init__(self, det_version: str) -> None:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
        params = {"Rec.lang_type": LangRec.KOREAN, "Rec.ocr_version": OCRVersion.PPOCRV5,
                  "Rec.model_type": ModelType.MOBILE}
        if det_version == "v5":
            params["Det.ocr_version"] = OCRVersion.PPOCRV5
            params["Det.model_type"] = ModelType.MOBILE
        self.name = f"rapid_det{det_version}"
        self.config = {k: str(v) for k, v in params.items()}
        self.eng = RapidOCR(params=params)

    def __call__(self, img):
        r = self.eng(img)
        if r.boxes is None:
            return []
        return [(np.asarray(b).tolist(), t, float(s)) for b, t, s in zip(r.boxes, r.txts, r.scores)]


def make_engine(name: str) -> Engine:
    if name == "paddle":
        return Paddle("mobile")
    if name == "paddle_serverdet":
        return Paddle("server")
    if name.startswith("rapid_det"):
        return Rapid(name.removeprefix("rapid_det"))
    raise SystemExit(f"unknown engine {name}")


# ----------------------------------------------------------------------------- resource monitor

class Monitor:
    """0.25초마다 프로세스 RSS · 커밋 메모리 · CPU 를 표본으로 남긴다. phase() 로 구간을 나눈다."""

    def __init__(self, interval: float = 0.25) -> None:
        self.p = psutil.Process()
        self.interval = interval
        self.samples: list[tuple[str, float, float, float, float]] = []  # phase, t, rss, commit, cpu(cores)
        self._phase = "idle"
        self._stop = threading.Event()
        self.p.cpu_percent(None)
        self.thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _commit(mi) -> float:
        return float(getattr(mi, "private", 0) or mi.vms)  # Windows: private bytes, 그 밖: VMS

    def snapshot(self) -> dict:
        mi = self.p.memory_info()
        return {"rss_mb": round(mi.rss / MB, 1), "commit_mb": round(self._commit(mi) / MB, 1)}

    def _run(self) -> None:
        while not self._stop.is_set():
            mi = self.p.memory_info()
            cpu = self.p.cpu_percent(None) / 100.0
            self.samples.append((self._phase, time.time(), mi.rss / MB, self._commit(mi) / MB, cpu))
            self._stop.wait(self.interval)

    def start(self) -> "Monitor":
        self.thread.start()
        return self

    def phase(self, name: str) -> None:
        self._phase = name

    def stop(self) -> None:
        self._stop.set()
        self.thread.join()

    def summary(self) -> dict:
        out = {}
        for ph in dict.fromkeys(s[0] for s in self.samples):
            ss = [s for s in self.samples if s[0] == ph]
            if not ss:
                continue
            cpus = [s[4] for s in ss[1:]] or [0.0]
            out[ph] = {"seconds": round(ss[-1][1] - ss[0][1], 1),
                       "rss_peak_mb": round(max(s[2] for s in ss), 1),
                       "commit_peak_mb": round(max(s[3] for s in ss), 1),
                       "cpu_avg_cores": round(statistics.mean(cpus), 2),
                       "cpu_peak_cores": round(max(cpus), 2)}
        return out


# ----------------------------------------------------------------------------- text & metrics

def reading_order(items: list[tuple[list, str, float]]) -> str:
    """상자를 줄 단위(세로 중심)로 묶고 줄 안에서 왼쪽→오른쪽."""
    if not items:
        return ""
    rows = []
    for box, text, _ in items:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        rows.append((sum(ys) / len(ys), max(ys) - min(ys), min(xs), text))
    rows.sort()
    lines, cur, cur_y, cur_h = [], [], None, None
    for y, h, x, t in rows:
        if cur and abs(y - cur_y) > max(cur_h, h) * 0.5:
            lines.append(" ".join(tt for _, tt in sorted(cur)))
            cur = []
        if not cur:
            cur_y, cur_h = y, h
        cur.append((x, t))
    lines.append(" ".join(tt for _, tt in sorted(cur)))
    return "\n".join(lines)


def squash(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", s or ""))


def cer(gt: str, hyp: str) -> float | None:
    g, h = squash(gt), squash(hyp)
    return Levenshtein.distance(g, h) / len(g) if g else None


def bigram_f1(a: str, b: str) -> float | None:
    a, b = squash(a), squash(b)
    if len(a) < 2 and len(b) < 2:
        return None
    ca, cb = Counter(a[i:i + 2] for i in range(len(a) - 1)), Counter(b[i:i + 2] for i in range(len(b) - 1))
    inter = sum((ca & cb).values())
    if not inter:
        return 0.0
    p, r = inter / max(sum(cb.values()), 1), inter / max(sum(ca.values()), 1)
    return 2 * p * r / (p + r)


# ----------------------------------------------------------------------------- inputs

def render(pdf_path: Path, page_no: int, dpi: int) -> Image.Image:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return pdf[page_no - 1].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        pdf.close()


def scanify(img: Image.Image, seed: int) -> Image.Image:
    rnd = np.random.default_rng(seed)
    g = img.convert("L").rotate(0.6, expand=True, fillcolor=255, resample=Image.BICUBIC)
    g = g.filter(ImageFilter.GaussianBlur(0.7))
    arr = np.asarray(g).astype(np.float32) + rnd.normal(0, 8, (g.height, g.width))
    g = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    g.save(buf, format="JPEG", quality=45)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def page_inputs(pdf_dir: Path, n_pages: int):
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        with pdfplumber.open(pdf_path) as pdf:
            gts = [(p.extract_text() or "") for p in pdf.pages[:n_pages]]
        for i, gt in enumerate(gts, 1):
            if len(squash(gt)) < 30:
                continue
            yield pdf_path.stem, i, "clean", gt, render(pdf_path, i, 200)
            yield pdf_path.stem, i, "scan", gt, scanify(render(pdf_path, i, 150), seed=i)


def image_inputs(doc_dir: Path, min_px: int, limit: int):
    reg = DocumentParserRegistry.default(extract_images=True)
    for f in sorted(doc_dir.iterdir()):
        if f.suffix.lower() not in LOCAL_EXT:
            continue
        doc = reg.parse(f)
        if not doc.success:
            continue
        picked = 0
        for im in doc.images:
            if picked >= limit:
                break
            try:
                pil = Image.open(io.BytesIO(im.data)).convert("RGB")
            except Exception:  # emf/wmf 등 Pillow 가 못 여는 형식
                continue
            if min(pil.size) < min_px:
                continue
            picked += 1
            slug = re.sub(r"[^0-9A-Za-z가-힣]+", "_", f.stem).strip("_")[:40]  # Windows: 끝 공백·특수문자 금지
            dst = OUT / "images" / slug / Path(im.name).with_suffix(".png").name
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                pil.save(dst)
            yield f.stem, im.name, dst, pil


# ----------------------------------------------------------------------------- single-engine run

def run_engine(name: str, args) -> dict:
    mon = Monitor().start()
    base = mon.snapshot()
    mon.phase("load")
    t = time.perf_counter()
    eng = make_engine(name)
    load_sec = time.perf_counter() - t
    after_load = mon.snapshot()

    pages = []
    if not args.skip_pages:
        mon.phase("pages")
        for doc, page, variant, gt, img in page_inputs(args.pdf_dir, args.pages):
            t = time.perf_counter()
            try:
                items, err = eng(np.asarray(img)), None
            except Exception as e:  # noqa: BLE001
                items, err = [], f"{type(e).__name__}: {e}"
            sec = time.perf_counter() - t
            hyp = reading_order(items)
            pages.append({"doc": doc, "page": page, "variant": variant, "gt_chars": len(squash(gt)),
                          "cer": cer(gt, hyp), "f1": bigram_f1(gt, hyp), "sec": round(sec, 2), "error": err,
                          "hyp": hyp})
            print(f"[page] {name:12s} {doc[:18]} p{page} {variant:5s} CER {pages[-1]['cer']:.3f} "
                  f"F1 {pages[-1]['f1']:.3f} {sec:.1f}s", flush=True)

    images = []
    if not args.skip_images:
        mon.phase("images")
        for doc, img_name, path, pil in image_inputs(args.docs, args.min_px, args.max_images):
            t = time.perf_counter()
            try:
                items, err = eng(np.asarray(pil)), None
            except Exception as e:  # noqa: BLE001
                items, err = [], f"{type(e).__name__}: {e}"
            text = reading_order(items)
            images.append({"doc": doc, "image": img_name, "path": str(path.relative_to(OUT)).replace("\\", "/"),
                           "w": pil.width, "h": pil.height, "text": text, "chars": len(squash(text)),
                           "boxes": len(items), "error": err,
                           "mean_score": round(statistics.mean([s for _, _, s in items]), 3) if items else None,
                           "sec": round(time.perf_counter() - t, 2)})
            print(f"[image] {name:12s} {doc[:18]} {img_name} {pil.size} chars {images[-1]['chars']}", flush=True)
    mon.stop()
    return {"engine": name, "config": eng.config, "load_sec": round(load_sec, 1),
            "mem_base": base, "mem_after_load": after_load, "resources": mon.summary(),
            "pages": pages, "images": images}


# ----------------------------------------------------------------------------- report

def page_summary(run: dict) -> list[dict]:
    out = []
    for v in ("clean", "scan"):
        rs = [r for r in run["pages"] if r["variant"] == v and r["cer"] is not None]
        if not rs:
            continue
        tot = sum(r["gt_chars"] for r in rs)
        out.append({"variant": v, "pages": len(rs),
                    "cer_weighted": sum(r["cer"] * r["gt_chars"] for r in rs) / tot,
                    "cer_median": statistics.median(r["cer"] for r in rs),
                    "f1_mean": statistics.mean(r["f1"] for r in rs),
                    "sec_per_page": statistics.mean(r["sec"] for r in rs),
                    "errors": sum(1 for r in rs if r["error"])})
    return out


def write_outputs(runs: list[dict]) -> None:
    names = [r["engine"] for r in runs]
    paddle = next((r for r in runs if r["engine"] == "paddle"), None)
    ptext = {(i["doc"], i["image"]): i["text"] for i in (paddle or {}).get("images", [])}
    sysinfo = (f"{platform.system()} {platform.release()} · {platform.processor() or platform.machine()} · "
               f"논리 코어 {psutil.cpu_count()} / 물리 {psutil.cpu_count(logical=False)} · "
               f"RAM {psutil.virtual_memory().total / 2**30:.1f} GB · Python {platform.python_version()}")
    L = ["# 로컬 OCR 벤치 (한국어)", "", f"_{time.strftime('%Y-%m-%d %H:%M')}_ · {sysinfo}", "",
         "## A. 페이지 OCR — 정답(PDF 텍스트층) 대비", "",
         "CER = 공백 제외 글자 편집거리 ÷ 정답 글자 수 (낮을수록 좋음, 글자 수 가중) · F1 = 글자 바이그램(순서 무관)", "",
         "| 엔진 | 조건 | 쪽 | CER(가중) | CER(중앙) | F1 | 쪽당 초 | 오류 |", "|---|---|---|---|---|---|---|---|"]
    for r in runs:
        for s in page_summary(r):
            L.append(f"| {r['engine']} | {s['variant']} | {s['pages']} | {s['cer_weighted']:.3f} | {s['cer_median']:.3f} | "
                     f"{s['f1_mean']:.3f} | {s['sec_per_page']:.1f} | {s['errors']} |")
    L += ["", "clean = 200dpi 컬러 렌더링 · scan = 150dpi 흑백 · 기울임 0.6° · 흐림 · 잡음 · JPEG q45", "",
          "## B. 문서 속 이미지 OCR — 정답 없음", "",
          "| 엔진 | 이미지 | 글자 5자 이상 | 총 글자 | 평균 신뢰도 | 장당 초 | paddle 과 일치(F1) |", "|---|---|---|---|---|---|---|"]
    for r in runs:
        im = r["images"]
        if not im:
            continue
        agree = [bigram_f1(ptext[(i["doc"], i["image"])], i["text"]) for i in im
                 if r["engine"] != "paddle" and (i["doc"], i["image"]) in ptext]
        agree = [a for a in agree if a is not None]
        scores = [i["mean_score"] for i in im if i["mean_score"]]
        L.append(f"| {r['engine']} | {len(im)} | {sum(1 for i in im if i['chars'] >= 5)} | {sum(i['chars'] for i in im)} | "
                 f"{statistics.mean(scores) if scores else 0:.3f} | {statistics.mean(i['sec'] for i in im):.2f} | "
                 f"{f'{statistics.mean(agree):.3f}' if agree else '—'} |")
    L += ["", "## C. 자원 사용량 (엔진마다 별도 프로세스)", "",
          "RAM = 작업 집합(RSS) 최대 · 가상 메모리 = 커밋된 전용 메모리(Windows private bytes) 최대 · "
          "CPU = 코어 수 단위 평균/최대 (1.0 = 코어 1개를 100%)", "",
          "| 엔진 | 모델 로딩 | 로딩 후 RAM / 가상 | 구간 | 시간(초) | RAM 최대 | 가상 메모리 최대 | CPU 평균 | CPU 최대 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        res = r["resources"]
        first = True
        for ph in ("load", "pages", "images"):
            if ph not in res:
                continue
            s = res[ph]
            head = (f"| {r['engine']} | {r['load_sec']}s | {r['mem_after_load']['rss_mb']:.0f} / "
                    f"{r['mem_after_load']['commit_mb']:.0f} MB") if first else "| | |"
            first = False
            L.append(f"{head} | {ph} | {s['seconds']} | {s['rss_peak_mb']:.0f} MB | {s['commit_peak_mb']:.0f} MB | "
                     f"{s['cpu_avg_cores']} | {s['cpu_peak_cores']} |")
    L += ["", "## 설정", ""] + [f"- **{r['engine']}**: `{json.dumps(r['config'], ensure_ascii=False)}`" for r in runs]
    L += ["", "엔진별 이미지 결과는 `gallery.html` 에서 이미지와 나란히 확인."]
    (OUT / "ocr_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (OUT / "ocr_report.json").write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")

    # 갤러리
    by_img: dict = {}
    for r in runs:
        for i in r["images"]:
            by_img.setdefault((i["doc"], i["image"]), {"meta": i, "eng": {}})["eng"][r["engine"]] = i
    if not by_img:
        return
    parts = ["<!doctype html><meta charset=utf-8><title>OCR 이미지 갤러리</title>",
             "<style>body{font-family:'Malgun Gothic',sans-serif;margin:16px}"
             ".r{display:grid;grid-template-columns:minmax(200px,420px) repeat(%d,1fr);gap:12px;"
             "border-top:1px solid #ccc;padding:12px 0}img{max-width:100%%}pre{white-space:pre-wrap;"
             "font-size:12px;background:#f6f6f6;padding:6px;margin:0}h4{margin:0 0 4px}</style>" % len(names),
             "<h2>문서 속 이미지 OCR — 엔진별 결과 (정답 없음, 눈으로 확인)</h2>"]
    for (doc, img), d in by_img.items():
        m = d["meta"]
        cells = [f"<div><img src='{html.escape(m['path'])}'><div>{html.escape(doc[:40])} · {html.escape(img)} · "
                 f"{m['w']}×{m['h']}</div></div>"]
        for n in names:
            e = d["eng"].get(n, {})
            meta = f"글자 {e.get('chars')} · 신뢰도 {e.get('mean_score')} · {e.get('sec')}s"
            cells.append(f"<div><h4>{n}</h4><div>{html.escape(meta)}</div>"
                         f"<pre>{html.escape(e.get('text') or e.get('error') or '')}</pre></div>")
        parts.append("<div class=r>" + "".join(cells) + "</div>")
    (OUT / "gallery.html").write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", type=Path, default=OUT / "pdf")
    ap.add_argument("--docs", type=Path, default=ROOT / "test_file")
    ap.add_argument("--pages", type=int, default=6, help="PDF 마다 앞에서 몇 쪽")
    ap.add_argument("--engines", nargs="+", default=["paddle", "rapid_detv5"])
    ap.add_argument("--min-px", type=int, default=150)
    ap.add_argument("--max-images", type=int, default=40, help="문서마다 최대 이미지 수")
    ap.add_argument("--skip-pages", action="store_true")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--single", action="store_true", help="(내부) 엔진 하나만 이 프로세스에서 실행")
    ap.add_argument("--report-only", action="store_true", help="runs/*.json 으로 보고서만 다시 만든다")
    args = ap.parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)

    if args.single:
        run = run_engine(args.engines[0], args)
        (RUNS / f"{run['engine']}.json").write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
        return

    if not args.report_only:
        passthru = [a for a in sys.argv[1:] if a not in args.engines and a != "--engines"]
        for name in args.engines:   # 자원 측정이 섞이지 않게 엔진마다 새 프로세스
            print(f"=== {name}", flush=True)
            subprocess.run([sys.executable, __file__, "--single", "--engines", name, *passthru], check=False)
    runs = [json.loads((RUNS / f"{n}.json").read_text(encoding="utf-8")) for n in args.engines
            if (RUNS / f"{n}.json").exists()]
    write_outputs(runs)
    print((OUT / "ocr_report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
