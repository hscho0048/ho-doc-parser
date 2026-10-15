"""mac_results/<시각>/ 를 읽어 results.md 표로 정리한다 (표준 라이브러리만 사용).

    python3 mac_test/summarize.py mac_results/20261007-1500
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def parse_time(text: str) -> dict:
    """macOS `/usr/bin/time -l` 또는 GNU `time -v` 출력 → wall · cpu 초, 최대 RAM(MB)."""
    out: dict = {}
    m = re.search(r"([\d.]+)\s+real\s+([\d.]+)\s+user\s+([\d.]+)\s+sys", text)          # macOS
    if m:
        out["wall"], out["cpu"] = float(m[1]), float(m[2]) + float(m[3])
        r = re.search(r"(\d+)\s+maximum resident set size", text)
        if r:
            out["rss_mb"] = int(r[1]) / 1024 / 1024                                         # 바이트
    m = re.search(r"Elapsed \(wall clock\) time.*?:\s*(?:(\d+):)?(\d+):([\d.]+)", text)    # GNU
    if m:
        out["wall"] = int(m[1] or 0) * 3600 + int(m[2]) * 60 + float(m[3])
        u = re.search(r"User time \(seconds\):\s*([\d.]+)", text)
        s = re.search(r"System time \(seconds\):\s*([\d.]+)", text)
        out["cpu"] = float(u[1] if u else 0) + float(s[1] if s else 0)
        r = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", text)
        if r:
            out["rss_mb"] = int(r[1]) / 1024
    out["failed"] = bool(re.search(r"\bFAILED\b|Traceback|failed", text)) and "wall" not in out
    return out


def doc_meta(dir_: Path, name: str) -> dict:
    stem = Path(name).stem
    js = dir_ / f"{stem}.json"
    if not js.exists():
        return {}
    try:
        return json.loads(js.read_text(encoding="utf-8")).get("metadata", {})
    except Exception:
        return {}


def status(dir_: Path, name: str) -> str:
    log = dir_ / f"{name}.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    if " ok" in text:
        return "성공"
    m = re.search(r"(FAILED|failed)[:\s]*(.*)", text)
    return ("실패: " + m[2].strip()[:60]) if m else ("실패" if text else "—")


def rows(base: Path, env: str):
    for mode in ("plain", "ocr"):
        root = base / env / mode
        if not root.exists():
            continue
        for fmt_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for tf in sorted(fmt_dir.glob("*.time")):
                name = tf.name[:-5]
                t = parse_time(tf.read_text(encoding="utf-8", errors="replace"))
                m = doc_meta(fmt_dir, name)
                cores = t["cpu"] / t["wall"] if t.get("wall") else 0
                yield {"env": env, "mode": "OCR 포함" if mode == "ocr" else "OCR 없이", "fmt": fmt_dir.name.upper(),
                       "file": name, "status": status(fmt_dir, name), "wall": t.get("wall"), "rss": t.get("rss_mb"),
                       "cores": cores, "chars": m.get("char_count"), "tables": m.get("table_count"),
                       "images": m.get("image_count"), "ocr": m.get("ocr_images"),
                       "pages": m.get("page_count") or m.get("slide_count") or m.get("sheet_count"),
                       "needs_ocr": len(m.get("needs_ocr_pages") or [])}


def fmt(v, spec="", dash="—"):
    return dash if v is None else format(v, spec)


def main() -> None:
    base = Path(sys.argv[1])
    lines = ["# 맥 형식별 테스트 결과", ""]
    sysf = base / "system.txt"
    if sysf.exists():
        lines += ["```", sysf.read_text(encoding="utf-8").strip(), "```", ""]
    for env, title in (("native", "A) 맥에서 직접"), ("docker", "B) Docker (linux/arm64)")):
        rs = list(rows(base, env))
        if not rs:
            continue
        lines += [f"## {title}", "",
                  "| 형식 | 파일 | 방식 | 결과 | 시간(초) | 최대 RAM(MB) | 평균 코어 | 쪽/슬라이드 | 글자 | 표 | 그림 | OCR 된 그림 | 스캔 쪽 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in sorted(rs, key=lambda r: (r["fmt"], r["file"], r["mode"])):
            lines.append(f"| {r['fmt']} | {r['file']} | {r['mode']} | {r['status']} | {fmt(r['wall'], '.1f')} | "
                         f"{fmt(r['rss'], '.0f')} | {r['cores']:.1f} | {fmt(r['pages'])} | {fmt(r['chars'], ',')} | "
                         f"{fmt(r['tables'])} | {fmt(r['images'])} | {fmt(r['ocr'])} | {r['needs_ocr'] or ''} |")
        lines.append("")
    for f, title in (("paddle_docker.txt", "C) PaddleOCR — Docker (paddle 3.2.2)"),
                     ("paddle_native.txt", "C) PaddleOCR — 맥에서 직접")):
        p = base / f
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            keep = [l for l in text.splitlines() if l.startswith(("paddle ", "load ", "PAGE", "FIGURE"))]
            t = parse_time(text)
            if t.get("wall"):
                keep.append(f"전체 {t['wall']:.1f}s · 최대 RAM {t.get('rss_mb', 0):.0f} MB · 평균 코어 {t['cpu'] / t['wall']:.1f}")
            lines += [f"## {title}", "", "```", *(keep or ["(결과 없음 — 로그 확인)"]), "```", ""]
    (base / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"→ {base / 'results.md'}")


if __name__ == "__main__":
    main()
