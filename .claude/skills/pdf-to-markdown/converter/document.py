"""Shared PyMuPDF document access helpers."""

from __future__ import annotations

from pathlib import Path

_DOC_CACHE: dict[Path, object] = {}


def get_document(pdf_path: Path):
    """Return a cached PyMuPDF document handle for the current process."""
    import pymupdf

    resolved = pdf_path.resolve()
    doc = _DOC_CACHE.get(resolved)
    if doc is None or getattr(doc, "is_closed", False):
        doc = pymupdf.open(str(resolved))
        _DOC_CACHE[resolved] = doc
    return doc


def close_cached_documents() -> None:
    """Close any cached PyMuPDF document handles."""
    for path, doc in list(_DOC_CACHE.items()):
        try:
            if not getattr(doc, "is_closed", True):
                doc.close()
        finally:
            _DOC_CACHE.pop(path, None)
