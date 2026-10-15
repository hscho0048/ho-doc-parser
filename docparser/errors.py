class DocParserError(Exception):
    """Base error. Parsers raise these; BaseParser converts them into a failed ParsedDocument."""


class EncryptedDocumentError(DocParserError):
    """Password protected / DRM / distribution-locked document."""


class UnsupportedFormatError(DocParserError):
    """File type is recognised but this variant cannot be parsed (e.g. an unsupported variant)."""


class CorruptedDocumentError(DocParserError):
    """Container or stream is damaged."""


class EmptyDocumentError(DocParserError):
    """Zero-byte file."""
