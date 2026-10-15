"""Synthetic fixtures built byte-by-byte (no Office / Hancom software): a minimal Compound File writer
and an HWPX (OWPML) package writer. Used for HWPX and for edge cases (encrypted OOXML / HWPX) that
office apps cannot easily produce.
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------- Compound File (CFB v3)

SECTOR = 512
FREESECT, ENDOFCHAIN, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF


def write_cfb(path: Path, streams: Dict[str, bytes]) -> None:
    """Write an OLE file. Keys are 'Name' or 'Storage/Name' (one level of storages).

    Streams are zero-padded to >= 4096 bytes so everything lives in regular sectors (no mini stream).
    """
    tree: Dict[str, Dict[str, bytes]] = {"": {}}
    for key, data in streams.items():
        if len(data) < 4096:
            data = data + b"\x00" * (4096 - len(data))
        if "/" in key:
            storage, name = key.split("/", 1)
            tree.setdefault(storage, {})[name] = data
        else:
            tree[""][key] = data

    entries: List[dict] = [{"name": "Root Entry", "type": 5, "children": []}]
    for storage, files in tree.items():
        parent = 0
        if storage:
            entries.append({"name": storage, "type": 1, "children": []})
            parent = len(entries) - 1
            entries[0]["children"].append(parent)
        for name, data in files.items():
            entries.append({"name": name, "type": 2, "data": data})
            entries[parent]["children"].append(len(entries) - 1)

    data_sectors = [(-(-len(e["data"]) // SECTOR)) if e["type"] == 2 else 0 for e in entries]
    dir_sectors = -(-len(entries) * 128 // SECTOR)
    n_fat = 1
    while True:
        total = n_fat + dir_sectors + sum(data_sectors)
        if -(-total // 128) <= n_fat:
            break
        n_fat += 1
    fat = [FREESECT] * (n_fat * 128)
    for i in range(n_fat):
        fat[i] = FATSECT
    pos = n_fat
    dir_start = pos
    for i in range(dir_sectors):
        fat[pos + i] = pos + i + 1 if i < dir_sectors - 1 else ENDOFCHAIN
    pos += dir_sectors
    for e, n in zip(entries, data_sectors):
        if e["type"] != 2:
            continue
        e["start"] = pos
        for i in range(n):
            fat[pos + i] = pos + i + 1 if i < n - 1 else ENDOFCHAIN
        pos += n

    # siblings: degenerate tree, children chained through the right-sibling pointer
    for e in entries:
        e.setdefault("left", NOSTREAM)
        e.setdefault("right", NOSTREAM)
        e.setdefault("child", NOSTREAM)
    for e in entries:
        kids = e.get("children") or []
        if kids:
            e["child"] = kids[0]
            for a, b in zip(kids, kids[1:]):
                entries[a]["right"] = b

    header = bytearray(512)
    header[0:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", header, 44, n_fat)
    struct.pack_into("<I", header, 48, dir_start)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<II", header, 60, ENDOFCHAIN, 0)
    struct.pack_into("<II", header, 68, ENDOFCHAIN, 0)
    difat = [i for i in range(n_fat)] + [FREESECT] * (109 - n_fat)
    struct.pack_into("<109I", header, 76, *difat)

    dir_bytes = bytearray()
    for e in entries:
        d = bytearray(128)
        name = e["name"].encode("utf-16-le")
        d[0:len(name)] = name
        struct.pack_into("<H", d, 64, len(name) + 2)
        d[66], d[67] = e["type"], 1
        struct.pack_into("<III", d, 68, e["left"], e["right"], e["child"])
        start = e.get("start", ENDOFCHAIN if e["type"] != 1 else 0)
        struct.pack_into("<II", d, 116, start, len(e.get("data", b"")))
        dir_bytes += d
    dir_bytes += b"\x00" * (dir_sectors * SECTOR - len(dir_bytes))
    for k in range(len(entries) * 128, dir_sectors * SECTOR, 128):  # unused entries
        struct.pack_into("<III", dir_bytes, k + 68, NOSTREAM, NOSTREAM, NOSTREAM)

    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(struct.pack(f"<{len(fat)}I", *fat))
        fh.write(dir_bytes)
        for e, n in zip(entries, data_sectors):
            if e["type"] == 2:
                fh.write(e["data"] + b"\x00" * (n * SECTOR - len(e["data"])))


# ---------------------------------------------------------------------- HWPX

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HS = "http://www.hancom.co.kr/hwpml/2011/section"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
HC = "http://www.hancom.co.kr/hwpml/2011/core"


def build_hwpx(path: Path, section_xml: str, encrypted: bool = False,
               images: Optional[Dict[str, bytes]] = None) -> None:
    images = images or {}
    items = "".join(f'<opf:item id="{k}" href="BinData/{k}.png" media-type="image/png" isEmbeded="1"/>' for k in images)
    hpf = f"""<?xml version="1.0" encoding="UTF-8"?>
<opf:package xmlns:opf="http://www.idpf.org/2007/opf/" version="" unique-identifier="" id="">
 <opf:metadata><opf:title>HWPX 테스트</opf:title><opf:meta name="creator" content="text">홍길동</opf:meta></opf:metadata>
 <opf:manifest><opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>
  <opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>{items}</opf:manifest>
 <opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="yes"/></opf:spine>
</opf:package>"""
    header = f"""<?xml version="1.0" encoding="UTF-8"?>
<hh:head xmlns:hh="{HH}" version="1.4" secCnt="1"><hh:refList><hh:styles itemCnt="3">
<hh:style id="0" type="PARA" name="바탕글" engName="Normal"/>
<hh:style id="1" type="PARA" name="개요 1" engName="Outline 1"/>
<hh:style id="2" type="PARA" name="개요 2" engName="Outline 2"/>
</hh:styles></hh:refList></hh:head>"""
    manifest = '<?xml version="1.0" encoding="UTF-8"?><odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
    if encrypted:
        manifest += '<odf:file-entry odf:full-path="Contents/section0.xml"><odf:encryption-data/></odf:file-entry>'
    manifest += "</odf:manifest>"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip")
        zf.writestr("version.xml", '<?xml version="1.0"?><hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" major="5"/>')
        zf.writestr("Contents/content.hpf", hpf)
        zf.writestr("Contents/header.xml", header)
        zf.writestr("Contents/section0.xml", section_xml)
        zf.writestr("META-INF/manifest.xml", manifest)
        for k, data in images.items():
            zf.writestr(f"BinData/{k}.png", data)


def hwpx_p(*runs: str, style: int = 0) -> str:
    return f'<hp:p paraPrIDRef="0" styleIDRef="{style}">' + "".join(runs) + "</hp:p>"


def hwpx_t(text: str) -> str:
    return f"<hp:run charPrIDRef=\"0\"><hp:t>{text}</hp:t></hp:run>"


def hwpx_tc(r: int, c: int, content: str, rs: int = 1, cs: int = 1) -> str:
    return (f'<hp:tc header="0"><hp:subList>{content}</hp:subList>'
            f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/></hp:tc>')


def hwpx_tbl(rows: int, cols: int, trs: List[List[str]], caption: str = "") -> str:
    cap = f"<hp:caption><hp:subList>{hwpx_p(hwpx_t(caption))}</hp:subList></hp:caption>" if caption else ""
    body = "".join("<hp:tr>" + "".join(tr) + "</hp:tr>" for tr in trs)
    return f'<hp:run charPrIDRef="0"><hp:tbl rowCnt="{rows}" colCnt="{cols}">{cap}{body}</hp:tbl></hp:run>'


def hwpx_section(paragraphs: List[str]) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?><hs:sec xmlns:hs="{HS}" xmlns:hp="{HP}" xmlns:hc="{HC}">'
            + "".join(paragraphs) + "</hs:sec>")
