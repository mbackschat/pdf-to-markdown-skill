"""Shared document-access helpers for the PDF-to-Markdown converter."""

from __future__ import annotations

import re
from pathlib import Path

from .models import ConversionContext, OutlineEntry
from .text import looks_like_contents_heading, sanitize_contents_entry


def selected_pages_1based(page_count: int, page_numbers: list[int] | None) -> list[int]:
    """Return selected pages as 1-based page numbers."""
    if page_numbers is not None:
        return [page + 1 for page in page_numbers]
    return list(range(1, page_count + 1))


def get_pdf_page_count(pdf_path: Path) -> int:
    """Return the number of pages in a PDF."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    try:
        return doc.page_count
    finally:
        doc.close()


def detect_text_pages(pdf_path: Path, page_numbers: list[int] | None) -> tuple[int, int]:
    """Return (pages_with_text, pages_without_text) for the selected pages."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    try:
        selected = page_numbers if page_numbers is not None else list(range(doc.page_count))
        with_text = 0
        without_text = 0
        for page_no in selected:
            text = doc.load_page(page_no).get_text("text")
            if re.search(r"\S", text):
                with_text += 1
            else:
                without_text += 1
        return with_text, without_text
    finally:
        doc.close()


def extract_page_style_lines(
    pdf_path: Path,
    page_no: int,
    style_cache: dict[int, list[dict[str, float | str]]],
) -> list[dict[str, float | str]]:
    """Extract positioned lines plus font-size metadata using PyMuPDF text dict output."""
    import pymupdf

    cache_key = page_no
    if cache_key in style_cache:
        return style_cache[cache_key]

    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc.load_page(page_no - 1)
        data = page.get_text("dict")
    finally:
        doc.close()

    lines: list[dict[str, float | str]] = []
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if str(span.get("text", "")).strip()]
            if not spans:
                continue

            text = "".join(str(span.get("text", "")) for span in spans).strip()
            if not text:
                continue

            bbox = line.get("bbox", (0, 0, 0, 0))
            lines.append(
                {
                    "x0": float(bbox[0]),
                    "y0": float(bbox[1]),
                    "x1": float(bbox[2]),
                    "y1": float(bbox[3]),
                    "text": text,
                    "size": max(float(span.get("size", 0.0)) for span in spans),
                    "flags": max(int(span.get("flags", 0)) for span in spans),
                }
            )

    style_cache[cache_key] = lines
    return lines


def extract_page_word_lines(
    pdf_path: Path,
    page_no: int,
    geometry_cache: dict[int, list[dict[str, float | str]]],
) -> list[dict[str, float | str]]:
    """Extract positioned lines for one page using PyMuPDF word geometry."""
    import pymupdf

    cache_key = -page_no
    if cache_key in geometry_cache:
        return geometry_cache[cache_key]

    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc.load_page(page_no - 1)
        words = page.get_text("words", sort=True)
    finally:
        doc.close()

    grouped: dict[tuple[int, int], list[tuple[float, float, float, float, str]]] = {}
    for word in words:
        x0, y0, x1, y1, text, block_no, line_no, _word_no = word[:8]
        if not str(text).strip():
            continue
        grouped.setdefault((int(block_no), int(line_no)), []).append(
            (float(x0), float(y0), float(x1), float(y1), str(text))
        )

    lines: list[dict[str, float | str]] = []
    for (_block_no, _line_no), entries in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1]),
    ):
        entries.sort(key=lambda item: item[0])
        text = " ".join(entry[4] for entry in entries).rstrip()
        if not text:
            continue
        lines.append(
            {
                "x0": min(entry[0] for entry in entries),
                "y0": min(entry[1] for entry in entries),
                "x1": max(entry[2] for entry in entries),
                "y1": max(entry[3] for entry in entries),
                "text": text,
                "words": [{"x0": entry[0], "x1": entry[2], "text": entry[4]} for entry in entries],
            }
        )

    geometry_cache[cache_key] = lines
    return lines


def extract_pdf_outline(pdf_path: Path, page_numbers: list[int] | None = None) -> list[OutlineEntry]:
    """Read the PDF outline/bookmark tree, optionally filtered to selected pages."""
    import pymupdf

    selected_pages = set(selected_pages_1based(0, page_numbers)) if page_numbers is not None else None
    doc = pymupdf.open(str(pdf_path))
    try:
        outline = doc.get_toc()
    finally:
        doc.close()

    entries: list[OutlineEntry] = []
    for level, title, page in outline:
        cleaned_title = sanitize_contents_entry(title)
        if not cleaned_title or looks_like_contents_heading(cleaned_title):
            continue
        if selected_pages is not None and page not in selected_pages:
            continue
        entries.append(OutlineEntry(level=level, title=cleaned_title, page=page))

    return entries


def get_cached_outline(context: ConversionContext) -> list[OutlineEntry]:
    """Return the cached PDF outline for this conversion context."""
    if context.outline is None:
        context.outline = extract_pdf_outline(context.pdf_path, context.page_numbers)
    return context.outline
