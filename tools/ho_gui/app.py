"""ho reference runner — local GUI for parsing documents.

    python tools/ho_gui/app.py                # opens http://127.0.0.1:8770 in the browser
    python tools/ho_gui/app.py --port 9000 --no-browser

Runs only on this computer (bound to 127.0.0.1). Parsing is local; each file runs in its own worker
process so time / peak RAM / CPU cores are measured per file. Nothing is sent over the network.
Use the .venv-ocr interpreter to have the OCR option available.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))


def _has(*modules: str) -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in modules)


def _venv_python(name: str):
    for rel in ("Scripts/python.exe", "bin/python"):   # Windows, macOS/Linux
        p = ROOT / name / rel
        if p.is_file():
            return p
    return None


def _ensure_interpreter() -> None:
    """Started with a Python that lacks the parser packages (or OCR)? Re-run with the project's
    .venv-ocr / .venv if there is one, so `python tools/ho_gui/app.py` works from any shell."""
    deps = _has("docx", "pptx", "pdfplumber", "openpyxl", "odf", "olefile")
    ocr = _has("rapidocr", "onnxruntime")
    if deps and ocr:
        return
    here = Path(sys.executable).resolve()
    for name in (".venv-ocr", ".venv") if not deps else (".venv-ocr",):
        py = _venv_python(name)
        if py is not None and py.resolve() != here:
            print(f"{sys.executable} 에는 {'파서 패키지' if not deps else 'OCR 패키지'}가 없어 {py} 로 다시 실행합니다.")
            try:
                sys.exit(subprocess.call([str(py), str(Path(__file__).resolve()), *sys.argv[1:]]))
            except KeyboardInterrupt:   # Ctrl+C reaches the child too; it cleans up its own temp folder
                sys.exit(0)
    if not deps:
        sys.exit(f"{sys.executable} 에 파서 패키지가 없습니다. 프로젝트 폴더에서 가상환경을 만들고 설치하세요:\n"
                 "  python -m venv .venv   그리고   pip install -r requirements-ocr.txt   (README '로컬 설치' 참고)")


if __name__ == "__main__":
    _ensure_interpreter()

from docparser import HWP_NOTICE, __version__  # noqa: E402
from docparser.detect import EXTENSION_MAP  # noqa: E402

MAX_UPLOAD = 200 * 1024 * 1024
JOBS_ROOT = Path(tempfile.mkdtemp(prefix="ho_gui_"))
JOBS: dict = {}
LOCK = threading.Lock()


def ocr_available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        import rapidocr  # noqa: F401
        return True
    except ImportError:
        return False


def safe_name(name: str) -> str:
    name = Path(unquote(name)).name
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[:180] or "document"


# ---------------------------------------------------------------------- jobs


def run_parse(name: str, data: bytes, opts: dict) -> dict:
    job = uuid.uuid4().hex[:10]
    jdir = JOBS_ROOT / job
    src = jdir / "src" / name
    src.parent.mkdir(parents=True)
    src.write_bytes(data)
    out = jdir / "out"
    if opts.get("ocr") and not ocr_available():
        opts["ocr"] = False
    proc = subprocess.run([sys.executable, str(HERE / "worker.py"), str(src), str(out), json.dumps(opts)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    rfile = out / "result.json"
    if not rfile.exists():
        result = {"success": False, "error": f"worker failed: {proc.stderr[-800:]}", "file_type": None}
    else:
        result = json.loads(rfile.read_text(encoding="utf-8"))
    result.update({"job": job, "name": name, "size": len(data), "options": opts})
    for img in result.get("images") or []:
        img["url"] = f"/api/job/{job}/file/{img['path']}"
    image_dir = (result.get("metadata") or {}).get("image_dir")
    if image_dir:  # let the page load extracted pictures
        for el in (result.get("elements") or {}).get("elements", []):
            if el.get("image"):
                el["image_url"] = f"/api/job/{job}/file/{el['image']}"
        if result.get("elements"):
            result["elements"]["content"]["html"] = re.sub(
                r'src="(' + re.escape(image_dir) + r'/[^"]+)"', rf'src="/api/job/{job}/file/\1"',
                result["elements"]["content"]["html"])
    with LOCK:
        JOBS[job] = {"src": src, "out": out, "result": result}
    return result


# ---------------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "ho-gui"

    def log_message(self, fmt, *args):  # quiet console
        pass

    def _send(self, code, body: bytes, ctype: str, extra: dict = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/info":
            return self._json({"version": __version__, "ocr": ocr_available(), "notice": HWP_NOTICE,
                               "formats": sorted(EXTENSION_MAP), "python": sys.executable})
        m = re.match(r"^/api/job/([0-9a-f]{10})$", u.path)
        if m and m.group(1) in JOBS:
            return self._json(JOBS[m.group(1)]["result"])
        m = re.match(r"^/api/job/([0-9a-f]{10})/file/(.+)$", u.path)
        if m and m.group(1) in JOBS:
            base = JOBS[m.group(1)]["out"].resolve()
            target = (base / unquote(m.group(2))).resolve()
            if base not in target.parents or not target.is_file():
                return self._json({"error": "not found"}, 404)
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix in (".md", ".json"):
                ctype += "; charset=utf-8"
            dl = {"Content-Disposition": "attachment"} if "download" in q else {}
            return self._send(200, target.read_bytes(), ctype, dl)
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/parse":
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_UPLOAD:
                    return self._json({"error": "파일이 비었거나 200MB를 넘습니다"}, 413)
                data = self.rfile.read(length)
                opts = {"ocr": q.get("ocr") == "1", "threads": int(q.get("threads") or 2),
                        "revisions": q.get("revisions", "accept"), "comments": q.get("comments", "1") == "1",
                        "images": q.get("images", "1") == "1", "hidden_sheets": q.get("hidden") == "1",
                        "pdf_password": q.get("pdf_password") or None}
                return self._json(run_parse(safe_name(q.get("name", "document")), data, opts))
        except Exception as exc:  # report to the page instead of dropping the connection
            return self._json({"state": "error", "error": f"{type(exc).__name__}: {exc}"}, 500)
        self._json({"error": "not found"}, 404)


def main() -> int:
    ap = argparse.ArgumentParser(description="ho reference runner (local GUI)")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"ho reference runner: {url}  (Ctrl+C to stop)  OCR {'on' if ocr_available() else 'off'}")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        shutil.rmtree(JOBS_ROOT, ignore_errors=True)  # uploaded copies and outputs are temporary
    return 0


if __name__ == "__main__":
    sys.exit(main())
