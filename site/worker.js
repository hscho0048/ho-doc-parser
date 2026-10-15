// Runs the docparser package inside Pyodide, off the main thread.
// Messages in:  {type: "parse", id, name, data: ArrayBuffer, options} | {type: "zip", id, jobs}
// Messages out: {type: "status", text} | {type: "ready", version} | {type: "result", id, result, files}
//               {type: "zip", id, data} | {type: "error", id, error}
importScripts("https://cdn.jsdelivr.net/pyodide/v0.27.2/full/pyodide.js");

// Pyodide-built packages (native code) and pure-Python wheels from PyPI.
// pdfplumber comes inside docparser.zip (see site/build_bundle.py): its only native dependency, pypdfium2,
// is used just to render non-JPEG PDF pictures, which docparser skips when it is missing.
const PYODIDE_PACKAGES = ["micropip", "lxml", "Pillow", "cryptography"];
const PYPI_PACKAGES = ["pdfminer.six==20260107", "openpyxl", "charset-normalizer", "python-docx", "python-pptx", "XlsxWriter",
                       "olefile", "defusedxml", "typing_extensions", "et-xmlfile"];
// odfpy is only published as source on PyPI; site/build_bundle.py builds its wheel into site/wheels/.

let py = null;

async function boot() {
  postMessage({type: "status", text: "Python 런타임 불러오는 중…"});
  py = await loadPyodide();
  postMessage({type: "status", text: "파서 의존성 설치 중… (처음 한 번만 오래 걸립니다)"});
  await py.loadPackage(PYODIDE_PACKAGES);
  const micropip = py.pyimport("micropip");
  const local = await fetch("wheels/index.json", {cache: "no-cache"}).then(r => r.ok ? r.json() : []);
  // one at a time: micropip 0.8 can trip over shared dependencies when installing a list concurrently
  for (const req of [...PYPI_PACKAGES, ...local.map(n => new URL("wheels/" + n, self.location).href)]) {
    try {
      await micropip.install(req);
    } catch (e) {
      throw new Error(`${req} 설치 실패: ${e}`);
    }
  }
  postMessage({type: "status", text: "파서 불러오는 중…"});
  const bundle = await fetch("docparser.zip", {cache: "no-cache"});
  if (!bundle.ok) throw new Error("docparser.zip을 찾을 수 없습니다 (site/build_bundle.py 실행 필요)");
  py.unpackArchive(await bundle.arrayBuffer(), "zip", {extractDir: "/app"});
  await py.runPythonAsync(`
import sys
sys.path.insert(0, "/app")
import docparser, docparser.web
`);
  const version = py.runPython("docparser.__version__");
  const notice = py.runPython("docparser.HWP_NOTICE");
  postMessage({type: "ready", version, notice});
}

const ready = boot().catch(e => postMessage({type: "fatal", error: String(e)}));

function readTree(dir) {
  // every file under dir as {relative path: Uint8Array}
  const out = {};
  const walk = (d, rel) => {
    for (const n of py.FS.readdir(d)) {
      if (n === "." || n === "..") continue;
      const p = d + "/" + n, r = rel ? rel + "/" + n : n;
      if (py.FS.isDir(py.FS.stat(p).mode)) walk(p, r);
      else out[r] = py.FS.readFile(p);
    }
  };
  walk(dir, "");
  return out;
}

onmessage = async ({data: msg}) => {
  await ready;
  if (!py) return;
  try {
    if (msg.type === "parse") {
      const dir = "/jobs/" + msg.id;
      const fn = py.pyimport("docparser.web").parse_upload;
      const bytes = py.toPy(new Uint8Array(msg.data));
      const json = fn(msg.name, bytes, dir, JSON.stringify(msg.options || {}));
      bytes.destroy();
      const result = JSON.parse(json);
      const files = result.success ? readTree(dir) : {};
      postMessage({type: "result", id: msg.id, result, files});
    } else if (msg.type === "zip") {
      const fn = py.pyimport("docparser.web").zip_results;
      const out = fn(JSON.stringify(msg.jobs));
      const data = out.toJs();
      out.destroy();
      postMessage({type: "zip", id: msg.id, data}, [data.buffer]);
    }
  } catch (e) {
    postMessage({type: "error", id: msg.id, error: String(e)});
  }
};
