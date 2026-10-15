"""File type detection: magic bytes first, then container inspection, extension last."""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional

import olefile

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"

EXTENSION_MAP = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".docm": "docx",
    ".pptx": "pptx",
    ".pptm": "pptx",
    ".ppt": "ppt",
    ".odt": "odt",
    ".hwpx": "hwpx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
}

# pseudo types used to report problems found during sniffing
ENCRYPTED_OOXML = "encrypted-ooxml"
LEGACY_DOC = "doc"


def detect_by_extension(path: Path) -> Optional[str]:
    return EXTENSION_MAP.get(path.suffix.lower())


def detect_by_content(path: Path) -> Optional[str]:
    with open(path, "rb") as fh:
        head = fh.read(2048)
    if not head:
        return None
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(OLE_MAGIC):
        return _sniff_ole(path)
    if head.startswith(ZIP_MAGIC):
        return _sniff_zip(path)
    return None


def _sniff_ole(path: Path) -> Optional[str]:
    try:
        with olefile.OleFileIO(str(path)) as ole:
            if ole.exists("PowerPoint Document"):
                return "ppt"
            if ole.exists("EncryptionInfo") or ole.exists("EncryptedPackage"):
                return ENCRYPTED_OOXML
            if ole.exists("WordDocument"):
                return LEGACY_DOC
    except Exception:
        return None
    return None


def _sniff_zip(path: Path) -> Optional[str]:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "mimetype" in names:
                mime = zf.read("mimetype")[:100].decode("ascii", "ignore").strip()
                if mime == "application/vnd.oasis.opendocument.text":
                    return "odt"
                if "hwp" in mime:  # application/hwp+zip
                    return "hwpx"
            if "word/document.xml" in names:
                return "docx"
            if "ppt/presentation.xml" in names:
                return "pptx"
            if "xl/workbook.xml" in names:
                return "xlsx"
            if any(n.startswith("Contents/section") for n in names):
                return "hwpx"
            if "content.xml" in names:
                return "odt"
    except zipfile.BadZipFile:
        return None
    return None


def detect_file_type(path: Path) -> Optional[str]:
    return detect_by_content(path) or detect_by_extension(path)
