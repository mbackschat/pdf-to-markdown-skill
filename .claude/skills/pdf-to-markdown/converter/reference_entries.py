"""Reference-entry layout normalization helpers."""

from __future__ import annotations

import re
from pathlib import Path

from .document import extract_page_style_lines
from .headings import extract_markdown_headings, match_headings_to_source_lines
from .models import ConversionContext
from .page_types import looks_like_contents_page
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
DENSE_LABEL_MAX_SIZE = 10.5
DENSE_LABEL_MAX_LEN = 42
DENSE_LABEL_MIN_COUNT = 6
DENSE_LABEL_CLUSTER_TOLERANCE = 20.0
DENSE_LABEL_MIN_CLUSTERS = 2
UNMATCHED_LABEL_MAX_LEN = 40
CAPTION_HEADING_MAX_SIZE = 12.5


def normalized_token(text: str) -> str:
    """Normalize text to an alphanumeric token for coarse structural matching."""
    return re.sub(r"[^a-z0-9]+", "", sanitize_contents_entry(text).lower())


def cluster_x_positions(x_positions: list[float], tolerance: float) -> list[float]:
    """Cluster nearby x positions into broad layout bands."""
    clusters: list[float] = []
    for x in sorted(x_positions):
        if not clusters or abs(x - clusters[-1]) > tolerance:
            clusters.append(x)
        else:
            clusters[-1] = (clusters[-1] + x) / 2.0
    return clusters


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


def looks_like_dense_short_label(
    line: dict[str, float | str],
    page_lines: list[dict[str, float | str]],
) -> bool:
    """Return True when a line behaves like a short label in a dense non-hierarchical region."""
    text = sanitize_contents_entry(str(line.get("text", "")))
    if not text:
        return False
    size = float(line.get("size", 9999.0))
    if size > DENSE_LABEL_MAX_SIZE:
        return False
    if len(text) > DENSE_LABEL_MAX_LEN:
        return False
    if text.endswith((".", "!", "?")):
        return False
    if re.match(r"^\d+(?:\.\d+)+\b", text):
        return False

    candidate_lines = [
        candidate
        for candidate in page_lines
        if float(candidate.get("size", 9999.0)) <= DENSE_LABEL_MAX_SIZE
        and len(sanitize_contents_entry(str(candidate.get("text", "")))) <= DENSE_LABEL_MAX_LEN
        and re.search(r"[A-Za-z]", str(candidate.get("text", "")))
    ]
    if len(candidate_lines) < DENSE_LABEL_MIN_COUNT:
        return False

    x_clusters = cluster_x_positions(
        [float(candidate.get("x0", 0.0)) for candidate in candidate_lines],
        DENSE_LABEL_CLUSTER_TOLERANCE,
    )
    if len(x_clusters) < DENSE_LABEL_MIN_CLUSTERS:
        return False

    return True


def looks_like_structured_body_heading_text(text: str) -> bool:
    """Return True for chapter/appendix or multi-level numbered headings."""
    cleaned = sanitize_contents_entry(text)
    return bool(
        re.match(r"^(chapter|appendix)\b", cleaned, re.IGNORECASE)
        or re.match(r"^(?:[A-Z]\.)?\d+(?:\.\d+)+\b", cleaned)
    )


def demote_unmatched_label_heading_runs(
    md_text: str,
    source_matches: dict[int, dict[str, float | str]],
) -> str:
    """Demote unmatched short label-like heading runs that are likely list items or controls."""
    headings = extract_markdown_headings(md_text)
    if not headings:
        return md_text

    lines = md_text.splitlines()
    demote_indices: set[int] = set()

    def cleaned_label(text: str) -> str:
        text = re.sub(r"^[^A-Za-z0-9]+", "", text).strip()
        return sanitize_contents_entry(text)

    def unmatched_upper_label(candidate_idx: int | None) -> bool:
        if candidate_idx is None or candidate_idx in source_matches:
            return False
        candidate_cleaned = cleaned_label(headings[candidate_idx].text)
        return bool(
            candidate_cleaned
            and len(candidate_cleaned) <= UNMATCHED_LABEL_MAX_LEN
            and candidate_cleaned == candidate_cleaned.upper()
            and not re.match(r"^\d+(?:\.\d+)+\b", candidate_cleaned)
        )

    for idx, heading in enumerate(headings):
        if idx in source_matches:
            continue

        cleaned = cleaned_label(heading.text)
        if not cleaned:
            continue
        if len(cleaned) > UNMATCHED_LABEL_MAX_LEN:
            continue
        if re.match(r"^\d+(?:\.\d+)+\b", cleaned):
            continue
        words = re.findall(r"[A-Za-z]+", cleaned)
        if len(words) < 2:
            continue
        if cleaned != cleaned.upper():
            continue

        neighbors = 0
        if unmatched_upper_label(idx - 1 if idx > 0 else None):
            neighbors += 1
        if unmatched_upper_label(idx + 1 if idx + 1 < len(headings) else None):
            neighbors += 1

        if neighbors == 0:
            continue

        demote_indices.add(idx)

    for idx in demote_indices:
        lines[headings[idx].line_idx] = cleaned_label(headings[idx].text)

    return "\n".join(lines)


def next_nonblank_line(lines: list[str], start_idx: int) -> str:
    """Return the next nonblank line after start_idx, or an empty string."""
    for idx in range(start_idx + 1, len(lines)):
        if lines[idx].strip():
            return lines[idx].strip()
    return ""


def looks_like_captionish_heading_context(
    heading_text: str,
    source: dict[str, float | str] | None,
    next_line: str,
) -> bool:
    """Return True when a heading behaves like a caption or local label, not hierarchy."""
    if looks_like_structured_body_heading_text(heading_text):
        return False
    if not source:
        return False
    if float(source.get("size", 9999.0)) > CAPTION_HEADING_MAX_SIZE:
        return False
    if not next_line:
        return False

    if heading_text.endswith(":") and (
        next_line.startswith("- ")
        or next_line.startswith("|")
        or next_line.startswith("```")
    ):
        return True

    if next_line.startswith("|") or next_line.startswith("![]("):
        return True

    return False


def normalize_reference_entry_headings(
    md_text: str,
    context: ConversionContext,
) -> str:
    """Demote headings in structurally non-hierarchical regions while keeping real entry titles."""
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

    md_text = demote_unmatched_label_heading_runs(md_text, source_matches)
    headings = extract_markdown_headings(md_text)
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
    page_contents_flags: dict[int, bool] = {}
    lines = md_text.splitlines()
    reference_titles_by_page: dict[int, list[dict[str, float | str]]] = {}

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
            page_contents_flags[page_no] = looks_like_contents_page(page_cache[page_no])

        if page_is_reference.get(page_no) and looks_like_reference_entry_title(source):
            reference_titles_by_page.setdefault(page_no, []).append(source)

    for heading_idx, heading in enumerate(headings):
        source = source_matches.get(heading_idx)
        page_no = int(source.get("page_no", 0)) if source else 0

        if source and page_no > 0:
            if page_no not in page_cache:
                page_cache[page_no] = extract_page_style_lines(
                    Path(context.pdf_path),
                    page_no,
                    context.style_cache,
                )
                page_is_reference[page_no] = page_looks_like_reference_entries(page_cache[page_no])
                page_contents_flags[page_no] = looks_like_contents_page(page_cache[page_no])

            if page_contents_flags.get(page_no):
                if not looks_like_structured_body_heading_text(heading.text):
                    lines[heading.line_idx] = heading.text
                    continue

            if looks_like_dense_short_label(source, page_cache[page_no]):
                lines[heading.line_idx] = heading.text
                continue

            next_line = next_nonblank_line(lines, heading.line_idx)
            if looks_like_captionish_heading_context(heading.text, source, next_line):
                lines[heading.line_idx] = heading.text
                continue

        if not source or page_no <= 0 or not page_is_reference.get(page_no):
            continue

        if looks_like_reference_entry_title(source):
            continue

        if looks_like_reference_field_label(source, page_cache[page_no]):
            lines[heading.line_idx] = heading.text
            continue

        title_sources = reference_titles_by_page.get(page_no, [])
        nearest_title = None
        source_y0 = float(source.get("y0", 0.0))
        for title_source in title_sources:
            if float(title_source.get("y0", 0.0)) < source_y0:
                nearest_title = title_source
        if nearest_title and looks_like_signature_for_title(source, nearest_title):
            lines[heading.line_idx] = heading.text

    return "\n".join(lines)
