"""Prepares site/ for the in-browser reference runner (Pyodide).

    python site/build_bundle.py

- site/docparser.zip      the docparser package + pdfplumber (pure Python; vendored because its wheel
  pins pypdfium2, which has no browser build, and micropip cannot skip that dependency reliably)
- site/wheels/*.whl       pure-Python wheels for dependencies that PyPI only ships as source (odfpy),
  listed in site/wheels/index.json

Run it before previewing site/ locally (python -m http.server -d site); the Pages workflow runs it too.
"""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
SOURCE_ONLY = ["odfpy==1.4.1"]
VENDORED = "pdfplumber==0.11.10"

tmp = SITE / "_vendor_tmp"
shutil.rmtree(tmp, ignore_errors=True)
subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "--quiet", "-d", str(tmp), VENDORED], check=True)

bundle = SITE / "docparser.zip"
with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in sorted((ROOT / "docparser").rglob("*.py")):
        zf.write(f, f.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(next(tmp.glob("*.whl"))) as whl:
        for n in whl.namelist():
            if n.startswith("pdfplumber/") and not n.endswith("/"):
                zf.writestr(n, whl.read(n))
shutil.rmtree(tmp)
print(f"wrote {bundle.relative_to(ROOT)} ({bundle.stat().st_size // 1024} KB)")

wheels = SITE / "wheels"
shutil.rmtree(wheels, ignore_errors=True)
wheels.mkdir()
subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--quiet", "-w", str(wheels), *SOURCE_ONLY],
               check=True)
names = sorted(p.name for p in wheels.glob("*.whl"))
(wheels / "index.json").write_text(json.dumps(names), encoding="utf-8")
print("wheels:", ", ".join(names))
