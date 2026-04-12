"""Reference-entry layout normalization helpers."""

from __future__ import annotations

import re
from pathlib import Path

from .headings import extract_markdown_headings, extract_page_style_lines, match_headings_to_source_lines
from .models import ConversionContext
from .text import sanitize_contents_entry

REFERENCE_ENTRY_TITLE_MIN_SIZE = 16.0
REFERENCE_ENTRY_LEFT_X_MAX = 90.0
REFERENCE_ENTRY_HEADER_Y_MAX = 35.0
REFERENCE_ENTRY_FIELD_MAX_SIZE = 14.5
REFERENCE_ENTRY_FIELD_MAX_LEN = 24
REFERENCE_ENTRY_VALUE_GAP_MIN = 45.0
REFERENCE_ENTRY_ROW_Y_TOLERANCE = 6.0
REFERENCE_ENTRY_MIN_FIELD_ROWS = 3
REFERENCE_ENTRY_SIGNATURE_MAX_GAP = 70.0
REFERENCE_ENTRY_SIGNATURE_MIN_SIZE_DROP = 3.0
REFERENCE_ENTRY_SIGNATURE_X_TOLERANCE = 18.0
REFERENCE_ENTRY_FIELD_TITLE_MAX_SIZE = 12.5
REFERENCE_ENTRY_FIELD_TITLE_MAX_WORDS = 4


def normalized_token(text: str) -> str:
    """Normalize text to an alphanumeric token for coarse structural matching."""
    return re.sub(r"[^a-z0-9]+", "", sanitize_contents_entry(text).lower())


def looks_like_reference_entry_title(line: dict[str, float | str]) -> bool:
    """Return True when a source line looks like a reference entry title."""
    text = sanitize_contents_entry(str(line.get("text", "")))
    if not text:
        return False
    if float(line.get("x0", 9999.0)) > REFERENCE_ENTRY_LEFT_X_MAX:
        return False
    if float(line.get("y0", 9999.0)) <= REFERENCE_ENTRY_HEADER_Y_MAX:
        return False
    if float(line.get("size", 0.0)) < REFERENCE_ENTRY_TITLE_MIN_SIZE:
        return False
    return True


def has_reference_value_partner(
    line: dict[str, float | str],
    page_lines: list[dict[str, float | str]],
) -> bool:
    """Return True when a left-column line has a matching value line to the right."""
    label_x = float(line.get("x0", 0.0))
    label_y = float(line.get("y0", 0.0))
    for candidate in page_lines:
        if candidate is line:
            continue
        if float(candidate.get("x0", 0.0)) < label_x + REFERENCE_ENTRY_VALUE_GAP_MIN:
            continue
        if abs(float(candidate.get("y0", 0.0)) - label_y) > REFERENCE_ENTRY_ROW_Y_TOLERANCE:
            continue
        return True
    return False


def looks_like_reference_field_label(
    line: dict[str, float | str],
    page_lines: list[dict[str, float | str]] | None = None,
) -> bool:
    """Return True when a source line behaves like a reference-entry field label."""
    text = sanitize_contents_entry(str(line.get("text", "")))
    if not text:
        return False
    if float(line.get("x0", 9999.0)) > REFERENCE_ENTRY_LEFT_X_MAX:
        return False
    size = float(line.get("size", 9999.0))
    if size > REFERENCE_ENTRY_FIELD_MAX_SIZE:
        return False
    if len(text) > REFERENCE_ENTRY_FIELD_MAX_LEN:
        return False
    if text.endswith((".", "!", "?")):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return False
    if len(words) > REFERENCE_ENTRY_FIELD_TITLE_MAX_WORDS:
        return False
    if size > REFERENCE_ENTRY_FIELD_TITLE_MAX_SIZE and text != text.upper():
        return False
    if page_lines is None:
        return text == text.upper()
    return has_reference_value_partner(line, page_lines)


def page_looks_like_reference_entries(lines: list[dict[str, float | str]]) -> bool:
    """Return True when the page structure resembles repeated reference entries."""
    titles = [line for line in lines if looks_like_reference_entry_title(line)]
    if not titles:
        return False

    field_rows = 0
    for line in lines:
        if looks_like_reference_field_label(line, lines):
            field_rows += 1

    return field_rows >= REFERENCE_ENTRY_MIN_FIELD_ROWS


def looks_like_signature_for_title(
    line: dict[str, float | str],
    title_line: dict[str, float | str],
) -> bool:
    """Return True when a smaller line under a title looks like its signature/prototype."""
    text = sanitize_contents_entry(str(line.get("text", "")))
    if "(" not in text:
        return False

    line_x0 = float(line.get("x0", 0.0))
    title_x0 = float(title_line.get("x0", 0.0))
    if abs(line_x0 - title_x0) > REFERENCE_ENTRY_SIGNATURE_X_TOLERANCE:
        return False

    line_y0 = float(line.get("y0", 0.0))
    title_y0 = float(title_line.get("y0", 0.0))
    if not (title_y0 < line_y0 <= title_y0 + REFERENCE_ENTRY_SIGNATURE_MAX_GAP):
        return False

    line_size = float(line.get("size", 0.0))
    title_size = float(title_line.get("size", 0.0))
    if title_size - line_size < REFERENCE_ENTRY_SIGNATURE_MIN_SIZE_DROP:
        return False

    title_token = normalized_token(str(title_line.get("text", "")))
    line_token = normalized_token(text)
    return bool(title_token and line_token and title_token in line_token)


def normalize_reference_entry_headings(
    md_text: str,
    context: ConversionContext,
) -> str:
    """Keep only entry titles as headings inside structurally detected reference runs."""
    headings = extract_markdown_headings(md_text)
    if not headings:
        return md_text

    source_matches = match_headings_to_source_lines(
        headings,
        context.pdf_path,
        context.page_numbers,
        style_cache=context.style_cache,
    )
    if not source_matches:
        return md_text

    page_cache: dict[int, list[dict[str, float | str]]] = {}
    page_is_reference: dict[int, bool] = {}
    lines = md_text.splitlines()
    reference_title_indices: set[int] = set()

    for heading_idx, heading in enumerate(headings):
        source = source_matches.get(heading_idx)
        if not source:
            continue

        page_no = int(source.get("page_no", 0))
        if page_no <= 0:
            continue

        if page_no not in page_cache:
            page_cache[page_no] = extract_page_style_lines(
                Path(context.pdf_path),
                page_no,
                context.style_cache,
            )
            page_is_reference[page_no] = page_looks_like_reference_entries(page_cache[page_no])

        if not page_is_reference.get(page_no):
            continue

        if looks_like_reference_entry_title(source):
            reference_title_indices.add(heading_idx)

    active_reference_level: int | None = None
    active_title_source: dict[str, float | str] | None = None

    for heading_idx, heading in enumerate(headings):
        source = source_matches.get(heading_idx)
        page_no = int(source.get("page_no", 0)) if source else 0

        if heading_idx in reference_title_indices:
            active_reference_level = heading.original_level
            active_title_source = source
            continue

        if active_reference_level is None:
            continue

        if heading.original_level < active_reference_level:
            active_reference_level = None
            active_title_source = None
            continue

        if source and page_no > 0:
            if page_no not in page_cache:
                page_cache[page_no] = extract_page_style_lines(
                    Path(context.pdf_path),
                    page_no,
                    context.style_cache,
                )
                page_is_reference[page_no] = page_looks_like_reference_entries(page_cache[page_no])

            if page_is_reference.get(page_no) and looks_like_reference_entry_title(source):
                active_reference_level = heading.original_level
                active_title_source = source
                continue

        demote = False
        if source and page_no > 0 and page_is_reference.get(page_no):
            if looks_like_reference_field_label(source, page_cache[page_no]):
                demote = True
            elif active_title_source and looks_like_signature_for_title(source, active_title_source):
                demote = True

        if demote or heading.original_level >= active_reference_level:
            lines[heading.line_idx] = heading.text

    return "\n".join(lines)
