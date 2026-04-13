"""Shared page-type classification helpers."""

from __future__ import annotations

import re

from .document import extract_page_style_lines
from .models import ConversionContext
from .text import looks_like_contents_heading, sanitize_contents_entry

CONTENTS_ENTRY_MAX_SIZE = 14.5
CONTENTS_ENTRY_MIN_COUNT = 4


def looks_like_contents_page(lines: list[dict[str, float | str]]) -> bool:
    """Return True when the source page is a visible contents page."""
    heading_idx = next(
        (idx for idx, line in enumerate(lines) if looks_like_contents_heading(str(line.get("text", "")))),
        None,
    )
    if heading_idx is None:
        return False

    entries = 0
    for line in lines[heading_idx + 1 :]:
        text = sanitize_contents_entry(str(line.get("text", "")))
        if not text:
            continue
        if float(line.get("size", 9999.0)) > CONTENTS_ENTRY_MAX_SIZE:
            continue
        if len(text) > 110:
            continue
        if text.endswith((".", "!", "?")):
            continue
        if not re.search(r"[A-Za-z]", text):
            continue
        entries += 1

    return entries >= CONTENTS_ENTRY_MIN_COUNT


def page_is_contents(context: ConversionContext, page_no: int) -> bool:
    """Classify one page as a visible contents page using shared cached style lines."""
    lines = extract_page_style_lines(context.pdf_path, page_no, context.style_cache)
    return looks_like_contents_page(lines)
