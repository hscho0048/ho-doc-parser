"""CLI batch mode: folders, parallel workers, non-clashing output names, _summary.json."""
import json
from pathlib import Path

from docparser.__main__ import main
from docparser.batch import output_names

from conftest import office_fixture


def test_output_names_never_clash():
    names = output_names([Path("a/x.pdf"), Path("b/x.docx"), Path("c/y.pdf"), Path("d/y.pdf")])
    assert names[Path("a/x.pdf")] == "x_pdf" and names[Path("b/x.docx")] == "x_docx"
    assert len(set(names.values())) == 4


def test_parallel_batch_saves_every_file(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    for name in ("complex.docx", "complex.pdf", "form.pdf"):
        (src / name).write_bytes(office_fixture(name).read_bytes())
    out = tmp_path / "out"

    assert main([str(src), "--out", str(out), "--jobs", "2"]) == 0

    summary = json.loads((out / "_summary.json").read_text(encoding="utf-8"))
    assert [s["output"] for s in summary] == ["complex_docx", "complex_pdf", "form"]
    assert all(s["success"] for s in summary)
    for s in summary:
        assert (out / f"{s['output']}.md").is_file() and (out / f"{s['output']}.json").is_file()
    # same stem, different formats -> separate image folders, and the markdown links point at them
    md = (out / "complex_pdf.md").read_text(encoding="utf-8")
    assert "complex_pdf_images/" in md and (out / "complex_pdf_images").is_dir()
    assert (out / "complex_docx_images").is_dir()
