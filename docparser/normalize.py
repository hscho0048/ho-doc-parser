"""Text clean-up applied to every extracted document."""
from __future__ import annotations

import re
import unicodedata

try:  # optional: repairs mojibake such as "Ã©" -> "é" (Apache-2.0)
    import ftfy  # type: ignore
except ImportError:  # pragma: no cover
    ftfy = None

# C0/C1 controls except \t and \n, the replacement char, zero-width chars, BOM, lone surrogates
_JUNK = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f�​‎‏⁠﻿\ud800-\udfff]"
)
# exotic spaces -> regular space (U+3000 ideographic space included)
_SPACES = re.compile("[   -   　]")
_INNER_WS = re.compile(r"(?<=\S)[ \t]{2,}")
_MANY_NL = re.compile(r"\n{3,}")


def normalize_text(text: str, fix_mojibake: bool = True) -> str:
    if not text:
        return ""
    if fix_mojibake and ftfy is not None:
        text = ftfy.fix_text(text, normalization="NFC")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n").replace(" ", "\n")
    # NFC (not NFKC): keeps Hangul composed without rewriting full-width / compatibility characters
    text = unicodedata.normalize("NFC", text)
    text = _JUNK.sub("", text)
    text = _SPACES.sub(" ", text).replace("\t", " ")
    lines = [_INNER_WS.sub(" ", line).rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    return _MANY_NL.sub("\n\n", text).strip()


def clean_inline(text: str) -> str:
    """Normalisation for a single text run (keeps intentional newlines)."""
    return normalize_text(text, fix_mojibake=False)
