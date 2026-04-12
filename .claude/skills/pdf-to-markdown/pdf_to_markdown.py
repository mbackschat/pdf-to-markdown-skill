#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pymupdf4llm",
#   "ocrmac; platform_system == 'Darwin'",
#   "rapidocr_onnxruntime; platform_system != 'Darwin'",
# ]
# ///
"""Convert PDF files to Markdown with a digital-first extraction pipeline.

PyMuPDF4LLM is used as the primary extractor for born-digital PDFs because it
preserves document structure better than OCR-oriented pipelines. OCR remains
available as a fallback for scanned PDFs, with Apple Vision preferred on macOS
and RapidOCR preferred elsewhere.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from urllib.parse import quote

DECIMAL_PAGE_RE = r"[A-Za-z]?\d+(?:[.-]\d+)*"
ROMAN_PAGE_RE = r"M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{1,3})"
PAGE_MARKER_RE = rf"(?:{DECIMAL_PAGE_RE}|{ROMAN_PAGE_RE})"


def parse_page_range(page_range_str: str) -> tuple[int, int]:
    """Parse a page range string like '1-50' into a 1-based inclusive tuple."""
    parts = page_range_str.split("-")
    if len(parts) == 1:
        page = int(parts[0])
        return (page, page)
    if len(parts) == 2:
        return (int(parts[0]), int(parts[1]))
    raise ValueError(f"Invalid page range: {page_range_str}")


def build_page_numbers(page_range: tuple[int, int] | None, page_count: int) -> list[int] | None:
    """Convert a 1-based inclusive page range to 0-based page numbers."""
    if page_range is None:
        return None

    start, end = page_range
    if start < 1 or end < start or end > page_count:
        raise ValueError(f"Invalid page range {start}-{end} for document with {page_count} pages")
    return list(range(start - 1, end))


def sanitize_stem(stem: str) -> str:
    """Create a filesystem-safe stem for output files."""
    stem = re.sub(r"[^\w-]", "_", stem).strip("_")
    return re.sub(r"_+", "_", stem)


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace for fuzzy matching."""
    return re.sub(r"\s+", " ", text).strip()


def strip_wrapping_markup(text: str) -> str:
    """Strip simple wrapping markdown emphasis from a text fragment."""
    text = text.strip()
    while text:
        original = text
        for marker in ("**", "__", "*", "_", "`"):
            if text.startswith(marker) and text.endswith(marker) and len(text) > len(marker) * 2:
                text = text[len(marker) : -len(marker)].strip()
                break
        if text == original:
            break
    return text


def cleanup_heading_markup(md_text: str) -> str:
    """Normalize headings emitted by PyMuPDF4LLM such as ## _**Heading**_."""
    lines = md_text.splitlines()
    cleaned: list[str] = []
    in_code = False

    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            cleaned.append(line)
            continue

        if not in_code:
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match:
                hashes, text = match.groups()
                cleaned.append(f"{hashes} {strip_wrapping_markup(text)}")
                continue

        cleaned.append(line)

    return "\n".join(cleaned)


def fix_headings(md_text: str) -> str:
    """Fix flat heading hierarchy and remove repeated running headers."""
    lines = md_text.split("\n")
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$")

    # Step 1: detect repeated headings, ignoring fenced code blocks.
    in_code = False
    heading_texts: list[str] = []
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = heading_re.match(line)
        if match:
            heading_texts.append(match.group(2).strip())

    counts = Counter(heading_texts)
    running_headers = {text for text, count in counts.items() if count >= 3}

    if running_headers:
        cleaned: list[str] = []
        skip_next_blank = False
        in_code = False
        for line in lines:
            if line.startswith("```"):
                in_code = not in_code
                skip_next_blank = False
                cleaned.append(line)
                continue
            if in_code:
                cleaned.append(line)
                continue

            match = heading_re.match(line)
            if match and match.group(2).strip() in running_headers:
                skip_next_blank = True
                continue
            if skip_next_blank and line.strip() == "":
                skip_next_blank = False
                continue
            skip_next_blank = False
            cleaned.append(line)
        lines = cleaned

    # Step 2: use TOC entries to determine main section headings.
    toc_entries: set[str] = set()
    in_toc = False
    in_code = False
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue

        match = heading_re.match(line)
        if match:
            text = match.group(2).strip()
            if re.search(r"(?i)table of content|inhaltsverzeichnis|contents", text):
                in_toc = True
                continue
            if in_toc:
                in_toc = False

        if in_toc:
            toc_match = re.match(r"\|\s*(.+?)\s*\|", line)
            if toc_match:
                entry = toc_match.group(1).strip()
                entry = re.sub(r"[.\s·…]+\d*\s*$", "", entry).strip()
                entry_no_num = re.sub(r"^\d+(\.\d+)*\.?\s*", "", entry).strip()
                if entry and len(entry) > 1:
                    toc_entries.add(entry)
                if entry_no_num and len(entry_no_num) > 1:
                    toc_entries.add(entry_no_num)
            list_match = re.match(r"[-*]\s+(.+)", line)
            if list_match:
                entry = list_match.group(1).strip()
                entry = re.sub(r"[.\s·…]+\d*\s*$", "", entry).strip()
                if entry and len(entry) > 1:
                    toc_entries.add(entry)

    if not toc_entries:
        return "\n".join(lines)

    # Step 3: normalize heading levels, still ignoring fenced code blocks.
    result: list[str] = []
    in_code = False
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue

        match = heading_re.match(line)
        if not match:
            result.append(line)
            continue

        text = match.group(2).strip()
        text_clean = re.sub(r"[.:]+$", "", text).strip()
        if text_clean in toc_entries or text in toc_entries:
            result.append(f"## {text}")
        else:
            result.append(f"### {text}")

    return "\n".join(result)


def map_lang_codes(langs: list[str], target: str) -> str:
    """Map short language codes to backend-specific OCR language labels."""
    tess_lang_map = {
        "en": "eng",
        "de": "deu",
        "fr": "fra",
        "es": "spa",
        "it": "ita",
        "pt": "por",
        "nl": "nld",
        "ja": "jpn",
        "zh": "chi_sim",
    }
    mac_lang_map = {
        "en": "en-US",
        "de": "de-DE",
        "fr": "fr-FR",
        "es": "es-ES",
        "it": "it-IT",
        "pt": "pt-BR",
        "nl": "nl-NL",
        "ja": "ja-JP",
        "zh": "zh-Hans",
    }

    if target == "mac":
        return ",".join(mac_lang_map.get(lang, lang) for lang in langs)
    if target == "tesseract":
        return "+".join(tess_lang_map.get(lang, lang) for lang in langs)
    return ",".join(langs)


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


def pick_ocr_backend(engine: str) -> str | None:
    """Choose an installed OCR backend."""
    if engine == "mac":
        if sys.platform == "darwin":
            try:
                import ocrmac  # noqa: F401
            except ImportError:
                return None
            return "mac"
        return None
    if engine == "rapidocr":
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            return None
        return "rapidocr"
    if engine == "tesseract":
        try:
            import pymupdf

            if pymupdf.get_tessdata():
                return "tesseract"
        except Exception:
            return None
        return None
    if engine == "auto":
        if sys.platform == "darwin":
            backend = pick_ocr_backend("mac")
            if backend:
                return backend
        backend = pick_ocr_backend("rapidocr")
        if backend:
            return backend
        backend = pick_ocr_backend("tesseract")
        if backend:
            return backend
    return None


def extract_page_lines_from_bbox_layout(
    pdf_path: Path,
    page_no: int,
    xml_cache: dict[int, list[dict[str, float | str]]],
) -> list[dict[str, float | str]]:
    """Extract positioned lines for one page using pdftotext -bbox-layout."""
    if page_no in xml_cache:
        return xml_cache[page_no]

    result = subprocess.run(
        [
            "pdftotext",
            "-bbox-layout",
            "-f",
            str(page_no),
            "-l",
            str(page_no),
            str(pdf_path),
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    root = ET.fromstring(result.stdout)
    lines: list[dict[str, float | str]] = []
    for line in root.iter():
        if not line.tag.endswith("line"):
            continue
        words = []
        for word in line:
            if not str(word.tag).endswith("word"):
                continue
            if word.text:
                words.append(word.text)
        text = " ".join(words).rstrip()
        if not text:
            continue
        lines.append(
            {
                "x0": float(line.attrib["xMin"]),
                "y0": float(line.attrib["yMin"]),
                "x1": float(line.attrib["xMax"]),
                "y1": float(line.attrib["yMax"]),
                "text": text,
            }
        )

    xml_cache[page_no] = lines
    return lines


def line_overlaps_box(line: dict[str, float | str], box: list[int]) -> bool:
    """Return True when a pdftotext line meaningfully overlaps a page box."""
    x0, y0, x1, y1 = box
    line_x0 = float(line["x0"])
    line_y0 = float(line["y0"])
    line_x1 = float(line["x1"])
    line_y1 = float(line["y1"])

    line_center_y = (line_y0 + line_y1) / 2.0
    if line_center_y < y0 - 1 or line_center_y > y1 + 1:
        return False

    overlap_x = max(0.0, min(x1, line_x1) - max(x0, line_x0))
    line_width = max(1.0, line_x1 - line_x0)
    return overlap_x / line_width >= 0.35


def recover_box_lines(
    pdf_path: Path,
    page_no: int,
    box: list[int],
    xml_cache: dict[int, list[dict[str, float | str]]],
) -> list[str]:
    """Recover text lines for a page box using pdftotext coordinates."""
    if shutil.which("pdftotext") is None:
        return []

    try:
        lines = extract_page_lines_from_bbox_layout(pdf_path, page_no, xml_cache)
    except (subprocess.SubprocessError, ET.ParseError):
        return []

    selected = [line for line in lines if line_overlaps_box(line, box)]
    if not selected:
        return []

    box_left = box[0]
    recovered: list[str] = []
    for line in selected:
        indent = max(0, int(round((float(line["x0"]) - box_left) / 6.0)))
        recovered.append((" " * min(indent, 20)) + str(line["text"]).rstrip())
    return recovered


def looks_like_preformatted(snippet: str, recovered_lines: list[str], box: list[int]) -> bool:
    """Heuristic for deciding whether a recovered multi-line block is code-like."""
    lines = [line.rstrip() for line in recovered_lines if line.strip()]
    joined = " ".join(lines)
    symbol_count = sum(joined.count(char) for char in "{}[]<>=();")
    code_markers = [
        r"#pragma",
        r"<[^>]+>",
        r"\bstruct\b",
        r"\bint\b",
        r"\bchar\b",
        r"\bvoid\b",
        r"\bmove(\.\w+)?\b",
        r"\blea\b",
        r"\bjsr\b",
        r"\brts\b",
    ]
    marker_hits = sum(1 for pattern in code_markers if re.search(pattern, joined, re.IGNORECASE))
    indented_box = box[0] >= 95
    if not lines:
        return False

    shortish_lines = max(len(line) for line in lines) <= 100
    sentence_like = sum(line.endswith((".", "!", "?")) for line in lines)
    non_sentence_lines = len(lines) - sentence_like

    if len(lines) == 1:
        return indented_box and (marker_hits >= 1 or symbol_count >= 6) and shortish_lines

    if symbol_count >= 4 and shortish_lines:
        return True
    if marker_hits >= 1 and indented_box:
        return True
    if marker_hits >= 2:
        return True

    snippet_norm = normalize_whitespace(snippet)
    recovered_norm = normalize_whitespace("\n".join(lines))
    has_code_punctuation = bool(re.search(r"[{}[\]<>#;=]", joined))
    return (
        indented_box
        and shortish_lines
        and has_code_punctuation
        and non_sentence_lines >= len(lines) - 1
        and snippet_norm == recovered_norm
    )


def restore_code_blocks_in_chunk(
    page_text: str,
    page_boxes: list[dict],
    pdf_path: Path,
    page_no: int,
    xml_cache: dict[int, list[dict[str, float | str]]],
) -> str:
    """Replace flattened code-like page boxes with fenced multi-line blocks."""
    replacements: list[tuple[int, int, str]] = []

    for box_info in page_boxes:
        if box_info.get("class") != "text":
            continue

        start, stop = box_info["pos"]
        snippet = page_text[start:stop]
        recovered_lines = recover_box_lines(pdf_path, page_no, box_info["bbox"], xml_cache)
        recovered_text = "\n".join(recovered_lines).strip("\n")
        if not recovered_text:
            continue
        if normalize_whitespace(snippet) != normalize_whitespace(recovered_text):
            continue
        if not looks_like_preformatted(snippet, recovered_lines, box_info["bbox"]):
            continue

        replacements.append((start, stop, recovered_text))

    if not replacements:
        return page_text

    merged: list[tuple[int, int, str]] = []
    for start, stop, recovered_text in sorted(replacements):
        if merged and not page_text[merged[-1][1] : start].strip():
            prev_start, _prev_stop, prev_text = merged[-1]
            merged[-1] = (prev_start, stop, f"{prev_text}\n{recovered_text}")
        else:
            merged.append((start, stop, recovered_text))

    updated = page_text
    for start, stop, recovered_text in sorted(merged, reverse=True):
        replacement = f"```\n{recovered_text}\n```\n\n"
        updated = updated[:start] + replacement + updated[stop:]
    return updated


def make_image_refs_relative(md_text: str, images_dir: Path, output_dir: Path) -> str:
    """Rewrite absolute image references to relative, URL-encoded paths."""
    if not images_dir.exists():
        return md_text

    rel_images = str(images_dir.relative_to(output_dir))
    abs_images = str(images_dir)
    md_text = md_text.replace(abs_images, rel_images)

    def encode_image_path(match: re.Match[str]) -> str:
        alt = match.group(1)
        path = quote(match.group(2), safe="/")
        return f"![{alt}]({path})"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", encode_image_path, md_text)


def normalize_inline_spacing(text: str) -> str:
    """Clean up OCR / extractor spacing artifacts in normal prose."""
    text = re.sub(r"[ \t]+", " ", text.strip())
    text = re.sub(r"\s+([,;:!?])(?=\s|$)", r"\1", text)
    text = re.sub(r"\s+(\.)(?=\s|$)", r"\1", text)
    text = re.sub(r"([(\[{])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]}])", r"\1", text)
    text = re.sub(r"([*_`])\s+([,.;:!?])", r"\1\2", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text


def strip_markdown_inline(text: str) -> str:
    """Remove lightweight inline markdown wrappers while keeping visible text."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`]+", "", text)
    return normalize_inline_spacing(text)


def looks_like_contents_heading(text: str) -> bool:
    """Return True when a heading is some variant of 'contents'."""
    token = re.sub(r"[^a-z0-9]+", "", strip_markdown_inline(text).lower())
    return token in {"contents", "tableofcontent", "tableofcontents"}


def sanitize_contents_entry(text: str) -> str:
    """Clean visible TOC entry text before matching or rendering."""
    text = strip_markdown_inline(text)
    text = re.sub(rf"\s*\({PAGE_MARKER_RE}\)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\.{2,}", " ", text)
    text = re.sub(r"[•·]+", " ", text)
    text = normalize_inline_spacing(text)
    text = text.strip(" -.:;")
    return text


def slugify_heading(text: str) -> str:
    """Create a GitHub-style heading slug."""
    text = strip_markdown_inline(text).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-{2,}", "-", text)
    return text


def heading_match_keys(text: str) -> list[str]:
    """Generate matching keys for headings and TOC entries."""
    text = sanitize_contents_entry(text)
    stripped_number = re.sub(r"^\d+(?:\.\d+)*\s+", "", text)
    keys = {text.lower()}
    if stripped_number != text:
        keys.add(stripped_number.lower())

    compact = re.sub(r"[^a-z0-9]+", "", stripped_number.lower())
    if compact:
        keys.add(compact)

    no_apostrophes = stripped_number.lower().replace("'", "").replace("’", "")
    if no_apostrophes:
        keys.add(no_apostrophes)
        compact_no_apostrophes = re.sub(r"[^a-z0-9]+", "", no_apostrophes)
        if compact_no_apostrophes:
            keys.add(compact_no_apostrophes)

    return [key for key in keys if key]


def extract_contents_entries_from_text(text: str) -> list[str]:
    """Extract TOC entry titles from a flattened contents line."""
    flattened = strip_markdown_inline(text).replace("…", ".")
    flattened = re.sub(
        rf"(?<![\w/])({PAGE_MARKER_RE})(?![\w/])\s+(?=(?:Chapter|Appendix|Foreword|Index|Bibliography|[A-Z]))",
        r"\1\n",
        flattened,
        flags=re.IGNORECASE,
    )

    entries: list[str] = []
    parts = flattened.splitlines()
    had_split = len(parts) > 1
    for part in parts:
        part = normalize_inline_spacing(part)
        if not part:
            continue

        matches = list(
            re.finditer(
                rf"(.+?)(?:\s*[.\u2026]{{2,}}\s*|\s+)(?<![\w/])({PAGE_MARKER_RE})(?![\w/])$",
                part,
                flags=re.IGNORECASE,
            )
        )
        if not matches:
            if had_split:
                cleaned_tail = sanitize_contents_entry(part)
                if (
                    cleaned_tail
                    and len(cleaned_tail) > 2
                    and not looks_like_contents_heading(cleaned_tail)
                    and not re.fullmatch(r"[.\-•_ ]+", cleaned_tail)
                ):
                    entries.append(cleaned_tail)
            continue

        best_match = max(matches, key=lambda match: len(match.group(1).strip()))
        cleaned = sanitize_contents_entry(best_match.group(1))
        if cleaned and not re.fullmatch(r"[.\-•_ ]+", cleaned):
            entries.append(cleaned)

    return entries


def clean_markdown_tables(md_text: str) -> str:
    """Trim noisy OCR table rows and normalize obvious table artifacts."""
    lines = md_text.splitlines()
    cleaned: list[str] = []
    i = 0

    while i < len(lines):
        if not lines[i].startswith("|"):
            cleaned.append(lines[i].rstrip())
            i += 1
            continue

        block: list[str] = []
        while i < len(lines) and lines[i].startswith("|"):
            block.append(lines[i].rstrip())
            i += 1

        def is_sep(line: str) -> bool:
            return bool(re.match(r"^\|(?:\s*:?-+:?\s*\|)+\s*$", line))

        rows = [
            [normalize_inline_spacing(cell.replace("<br>", " ").strip()) for cell in line.strip("|").split("|")]
            for line in block
            if not is_sep(line)
        ]
        if not rows:
            continue

        max_cols = max(len(row) for row in rows)
        rows = [row + [""] * (max_cols - len(row)) for row in rows]

        keep_cols = []
        for col_idx in range(max_cols):
            column = [row[col_idx] for row in rows]
            if any(re.search(r"[A-Za-z0-9]", cell) for cell in column):
                keep_cols.append(col_idx)
        if not keep_cols:
            continue

        rows = [[row[col] for col in keep_cols] for row in rows]
        if len(rows[0]) >= 2:
            duplicate_pairs = sum(
                1 for row in rows if len(row) >= 2 and row[0] and row[0] == row[1]
            )
            if duplicate_pairs >= max(2, len(rows) // 2):
                rows = [[row[0], *row[2:]] for row in rows]

        rows = [
            row
            for row in rows
            if any(re.search(r"[A-Za-z0-9]", cell) for cell in row)
            and not all(re.fullmatch(r"[.\-•_ ]+", cell or "") for cell in row)
        ]
        if not rows:
            continue

        header = rows[0]
        if all(not cell for cell in header):
            header = [f"Col {idx + 1}" for idx in range(len(rows[0]))]
            rows = [header] + rows[1:]

        cleaned.append("|" + "|".join(header) + "|")
        cleaned.append("|" + "|".join("---" for _ in header) + "|")
        for row in rows[1:]:
            cleaned.append("|" + "|".join(row) + "|")

    return "\n".join(cleaned)


def convert_contents_tables_to_lists(md_text: str) -> str:
    """Convert OCR-heavy contents tables into plain bullet lists."""
    lines = md_text.splitlines()
    out: list[str] = []
    i = 0

    def is_table_line(line: str) -> bool:
        return line.startswith("|")

    def is_sep(line: str) -> bool:
        return bool(re.match(r"^\|(?:\s*:?-+:?\s*\|)+\s*$", line))

    def split_br(cell: str) -> list[str]:
        parts = [normalize_inline_spacing(part) for part in re.split(r"<br\s*/?>", cell)]
        return [part for part in parts if part and not re.fullmatch(r"[.\-•_ ]+", part)]

    def pageish(value: str) -> bool:
        return bool(re.fullmatch(PAGE_MARKER_RE, value, re.IGNORECASE))

    while i < len(lines):
        out.append(lines[i].rstrip())
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", lines[i])
        i += 1
        if not heading_match or not looks_like_contents_heading(heading_match.group(2)):
            continue

        blank_buffer: list[str] = []
        while i < len(lines) and not lines[i].strip():
            blank_buffer.append(lines[i])
            i += 1

        if i >= len(lines) or not is_table_line(lines[i]):
            out.extend(blank_buffer)
            continue

        table_block: list[str] = []
        while i < len(lines) and is_table_line(lines[i]):
            table_block.append(lines[i].rstrip())
            i += 1

        rows = [
            [cell.strip() for cell in row.strip("|").split("|")]
            for row in table_block
            if not is_sep(row)
        ]
        bullets: list[str] = []
        seen: set[str] = set()

        for row in rows:
            if not any(re.search(r"[A-Za-z0-9]", cell) for cell in row):
                continue

            nonempty = [cell for cell in row if re.search(r"[A-Za-z0-9]", cell)]
            if not nonempty:
                continue

            last_parts = split_br(nonempty[-1])
            page_parts = last_parts if last_parts and pageish(last_parts[-1]) else []
            text_cells = nonempty[:-1] if page_parts else nonempty
            text_parts: list[str] = []
            for cell in text_cells:
                text_parts.extend(split_br(cell))
            if not text_parts:
                continue

            if page_parts and len(page_parts) == len(text_parts):
                pairs = zip(text_parts, page_parts)
            else:
                pairs = ((text, "") for text in text_parts)

            for text, page in pairs:
                text = normalize_inline_spacing(text)
                if not text or re.fullmatch(r"[.\-•_ ]+", text):
                    continue
                item = f"- {text}" if not page else f"- {text} ({page})"
                if item not in seen:
                    bullets.append(item)
                    seen.add(item)

        if bullets:
            out.append("")
            out.extend(bullets)
            out.append("")
        else:
            out.extend(blank_buffer)
            out.extend(table_block)

    return "\n".join(out)


def fix_definition_bullets(md_text: str) -> str:
    """Merge OCR-split term/definition bullet pairs into one cleaner line."""
    lines = md_text.splitlines()
    result: list[str] = []
    i = 0
    in_code = False

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            in_code = not in_code
            result.append(line)
            i += 1
            continue
        if in_code or line.startswith("|"):
            result.append(line)
            i += 1
            continue

        term_match = re.match(r"^-\s+(.+?)\s*$", line.strip())
        if term_match:
            term = normalize_inline_spacing(term_match.group(1))
            j = i + 1
            blanks = 0
            while j < len(lines) and not lines[j].strip() and blanks < 2:
                blanks += 1
                j += 1

            if j < len(lines):
                def_match = re.match(r"^\s{2,}-\s+(.+?)\s*$", lines[j])
                if def_match and len(term) <= 40 and not term.endswith((".", ":", ";", "!", "?")):
                    definition = normalize_inline_spacing(def_match.group(1))
                    result.append(f"- {term}: {definition}")
                    i = j + 1
                    continue

        result.append(line)
        i += 1

    return "\n".join(result)


def normalize_prose_lines(md_text: str) -> str:
    """Normalize spacing and bullet artifacts outside fenced code blocks."""
    lines = md_text.splitlines()
    cleaned: list[str] = []
    in_code = False

    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("```"):
            in_code = not in_code
            cleaned.append(stripped)
            continue
        if in_code:
            cleaned.append(stripped)
            continue
        if not stripped:
            cleaned.append("")
            continue
        if stripped.startswith("|"):
            cleaned.append(stripped)
            continue

        stripped = re.sub(r"^-\s+[•·]\s+", "- ", stripped)
        stripped = re.sub(r"^-\s*-\s+", "- ", stripped)
        stripped = re.sub(r"^\s*[•·]\s+", "- ", stripped)
        cleaned.append(normalize_inline_spacing(stripped))

    return "\n".join(cleaned)


def remove_redundant_page_title_headings(md_text: str) -> str:
    """Drop repeated running-title headings when they are immediately followed by another heading."""
    lines = md_text.splitlines()
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    seen: set[str] = set()
    result: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        match = heading_re.match(line)
        if not match:
            result.append(line)
            i += 1
            continue

        text = normalize_inline_spacing(match.group(2))
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        next_is_heading = j < len(lines) and bool(heading_re.match(lines[j]))

        if text in seen and next_is_heading:
            i += 1
            continue

        seen.add(text)
        result.append(line)
        i += 1

    return "\n".join(result)


def split_option_bullet_runs(md_text: str) -> str:
    """Split collapsed compiler-option bullets into one bullet per option."""
    lines = md_text.splitlines()
    result: list[str] = []
    in_code = False
    option_re = re.compile(r"(?<!\S)(-[A-Z0-9][A-Za-z0-9-]*)\s+")

    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue

        stripped = line.strip()
        if not stripped.startswith("- -"):
            result.append(line)
            continue

        content = stripped[2:].strip()
        matches = list(option_re.finditer(content))
        if not matches:
            result.append(line)
            continue

        parts: list[str] = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
            chunk = normalize_inline_spacing(content[start:end])
            option = match.group(1)
            rest = normalize_inline_spacing(chunk[len(option) :].strip())
            parts.append(f"- `{option}` {rest}".rstrip())

        result.extend(parts)

    return "\n".join(result)


def expand_contents_paragraphs(md_text: str) -> str:
    """Expand flattened contents paragraphs into one bullet per entry."""
    lines = md_text.splitlines()
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    result: list[str] = []
    in_contents = False

    def append_entries(entries: list[str]) -> None:
        if not entries:
            return
        if result and result[-1] != "" and not result[-1].lstrip().startswith("- "):
            result.append("")
        result.extend(f"- {entry}" for entry in entries)

    for line in lines:
        match = heading_re.match(line)
        if match:
            heading_text = strip_markdown_inline(match.group(2))
            if looks_like_contents_heading(heading_text):
                in_contents = True
                result.append(line)
                continue

            if in_contents:
                entries = extract_contents_entries_from_text(heading_text)
                if entries:
                    append_entries(entries)
                    continue
                in_contents = False

            result.append(line)
            continue

        if in_contents and not line.lstrip().startswith("- "):
            matches = extract_contents_entries_from_text(line)
            if matches:
                append_entries(matches)
                continue
        elif in_contents and line.lstrip().startswith("- "):
            matches = extract_contents_entries_from_text(line.lstrip()[2:].strip())
            if matches:
                append_entries(matches)
                continue

        result.append(line)

    return "\n".join(result)


def link_contents_entries(md_text: str) -> str:
    """Convert contents bullets into internal markdown links when headings exist."""
    lines = md_text.splitlines()
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

    anchor_map: dict[str, str] = {}
    for line in lines:
        match = heading_re.match(line)
        if not match:
            continue
        heading_text = strip_markdown_inline(match.group(2))
        if looks_like_contents_heading(heading_text):
            continue
        slug = slugify_heading(heading_text)
        if not slug:
            continue
        for key in heading_match_keys(heading_text):
            anchor_map.setdefault(key, slug)

    result: list[str] = []
    in_contents = False

    for line in lines:
        match = heading_re.match(line)
        if match:
            heading_text = strip_markdown_inline(match.group(2))
            in_contents = looks_like_contents_heading(heading_text)
            result.append(line)
            continue

        if not in_contents or not line.lstrip().startswith("- "):
            result.append(line)
            continue

        entry = sanitize_contents_entry(line.lstrip()[2:].strip())
        if not entry:
            continue
        if looks_like_contents_heading(entry):
            continue

        slug = None
        for key in heading_match_keys(entry):
            if key in anchor_map:
                slug = anchor_map[key]
                break

        if slug:
            result.append(f"- [{entry}](#{slug})")
        else:
            result.append(f"- {entry}")

    return "\n".join(result)


def get_ocr_function(backend: str | None, langs: list[str]):
    """Return an OCR callback compatible with PyMuPDF4LLM."""
    if backend == "mac":
        return build_ocrmac_function(langs)
    if backend == "rapidocr":
        from pymupdf4llm.ocr import rapidocr_api

        return rapidocr_api.exec_ocr
    if backend == "tesseract":
        from pymupdf4llm.ocr import tesseract_api

        return tesseract_api.exec_ocr
    return None


def build_ocrmac_function(langs: list[str]):
    """Build a PyMuPDF4LLM OCR adapter backed by Apple Vision."""
    import pymupdf
    from PIL import Image
    from ocrmac.ocrmac import text_from_image

    font = pymupdf.Font("helv")
    fontname = "F0"

    def adjust_width(text: str, fontsize: float, rect: pymupdf.Rect) -> pymupdf.Matrix:
        width = font.text_length(text, fontsize)
        return pymupdf.Matrix(rect.width / width, 1) if width > 0 else pymupdf.Matrix(1, 1)

    def exec_ocr(page, dpi=300, pixmap=None, language="eng", keep_ocr_text=False):
        if pixmap is None:
            pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)

        image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
        preferred_languages = [lang for lang in map_lang_codes(langs, "mac").split(",") if lang]
        results = text_from_image(
            image,
            recognition_level="accurate",
            language_preference=preferred_languages or None,
            confidence_threshold=0.15,
            detail=True,
        )

        if not results:
            return

        page.insert_font(fontname=fontname, fontbuffer=font.buffer)
        matrix = pymupdf.Rect(pixmap.irect).torect(page.rect)

        for text, confidence, bbox in results:
            if not text or not text.strip():
                continue

            x, y, width, height = bbox
            rect = pymupdf.Rect(
                x * pixmap.width,
                (1.0 - (y + height)) * pixmap.height,
                (x + width) * pixmap.width,
                (1.0 - y) * pixmap.height,
            ) * matrix

            fontsize = max(rect.height, 6)
            mat = adjust_width(text, fontsize, rect)
            page.insert_text(
                rect.bl + (0, -0.15 * fontsize),
                text,
                fontsize=fontsize,
                fontname=fontname,
                render_mode=0,
                morph=(rect.bl, mat),
            )

    return exec_ocr


def extract_markdown(
    pdf_path: Path,
    page_numbers: list[int] | None,
    force_ocr: bool,
    backend: str | None,
    langs: list[str],
    images_dir: Path,
) -> str:
    """Extract Markdown from a PDF using PyMuPDF4LLM and optional OCR preprocessing."""
    import pymupdf4llm

    chunks = pymupdf4llm.to_markdown(
        str(pdf_path),
        pages=page_numbers,
        page_chunks=True,
        write_images=True,
        image_path=str(images_dir),
        use_ocr=bool(backend),
        force_ocr=force_ocr,
        ocr_language=map_lang_codes(langs, "tesseract"),
        ocr_function=get_ocr_function(backend, langs),
        header=False,
        footer=False,
        show_progress=False,
    )

    xml_cache: dict[int, list[dict[str, float | str]]] = {}
    page_texts: list[str] = []
    for chunk in chunks:
        page_no = int(chunk["metadata"]["page_number"])
        page_text = chunk["text"]
        page_text = restore_code_blocks_in_chunk(page_text, chunk["page_boxes"], pdf_path, page_no, xml_cache)
        page_texts.append(page_text.strip())

    return "\n\n".join(text for text in page_texts if text).strip() + "\n"


def cleanup_markdown(md_text: str, pdf_path: Path, images_dir: Path, output_path: Path) -> str:
    """Apply markdown cleanup after extraction."""
    md_text = cleanup_heading_markup(md_text)
    md_text = fix_headings(md_text)
    md_text = remove_redundant_page_title_headings(md_text)
    md_text = convert_contents_tables_to_lists(md_text)
    md_text = clean_markdown_tables(md_text)
    md_text = fix_definition_bullets(md_text)
    md_text = normalize_prose_lines(md_text)
    md_text = split_option_bullet_runs(md_text)
    md_text = expand_contents_paragraphs(md_text)
    md_text = link_contents_entries(md_text)
    md_text = make_image_refs_relative(md_text, images_dir, output_path.parent)
    md_text = re.sub(r"\n{3,}", "\n\n", md_text).strip() + "\n"
    return md_text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert PDF to Markdown using PyMuPDF4LLM with optional OCR fallback."
    )
    parser.add_argument("pdf_path", help="Path to a PDF file or folder containing PDFs")
    parser.add_argument(
        "-o",
        "--output",
        help="Output .md file path (default: next to the source PDF)",
    )
    parser.add_argument("--pages", help="Page range, e.g. '1-50' (default: all)")
    parser.add_argument(
        "--ocr",
        "--scan",
        action="store_true",
        help="Force OCR for scanned PDFs or broken text layers",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=["auto", "mac", "rapidocr", "tesseract"],
        default="auto",
        help="OCR backend to use when OCR is needed (default: mac on macOS, rapidocr elsewhere)",
    )
    parser.add_argument(
        "--langs",
        default="en",
        help="Comma-separated language codes (default: en)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Unused compatibility flag kept for the existing skill interface",
    )
    args = parser.parse_args()

    input_path = Path(args.pdf_path).resolve()
    if not input_path.exists():
        print(f"Error: Path not found: {input_path}")
        sys.exit(1)

    if input_path.is_dir():
        pdf_files = sorted(input_path.glob("*.pdf"))
        if not pdf_files:
            print(f"Error: No PDF files found in {input_path}")
            sys.exit(1)
        print(f"Found {len(pdf_files)} PDF(s) in {input_path}")
        if args.output:
            print("Warning: --output is ignored in batch mode (one .md per PDF)")
    else:
        if input_path.suffix.lower() != ".pdf":
            print(f"Error: Not a PDF file: {input_path}")
            sys.exit(1)
        pdf_files = [input_path]

    langs = [lang.strip() for lang in args.langs.split(",") if lang.strip()]
    batch_start = time.time()

    import pymupdf

    for file_idx, pdf_path in enumerate(pdf_files, 1):
        doc = pymupdf.open(str(pdf_path))
        try:
            page_range = parse_page_range(args.pages) if args.pages else None
            page_numbers = build_page_numbers(page_range, doc.page_count)
        except ValueError as exc:
            doc.close()
            print(f"Error: {exc}")
            sys.exit(1)
        finally:
            if not doc.is_closed:
                doc.close()

        with_text, without_text = detect_text_pages(pdf_path, page_numbers)
        needs_ocr = args.ocr or without_text > 0
        backend = pick_ocr_backend(args.ocr_engine) if needs_ocr else None

        if needs_ocr and backend is None:
            print(f"Error: OCR is required for {pdf_path.name}, but no OCR backend is installed.")
            print("Available backends for this skill are Apple Vision (ocrmac), RapidOCR, or Tesseract.")
            sys.exit(1)

        stem = sanitize_stem(pdf_path.stem)
        if args.output and len(pdf_files) == 1:
            output_path = Path(args.output).resolve()
        else:
            output_path = pdf_path.parent / f"{stem}.md"
        images_dir = output_path.parent / f"{stem}_images"

        if len(pdf_files) > 1:
            print(f"\n[{file_idx}/{len(pdf_files)}] {pdf_path.name}")
        print("PDF to Markdown Converter (PyMuPDF4LLM)")
        print("=" * 40)
        print(f"  Input:  {pdf_path}")
        print(f"  Output: {output_path}")
        print(f"  Images: {images_dir}/")
        if page_range:
            print(f"  Pages:  {page_range[0]}-{page_range[1]}")
        print(f"  Text pages:    {with_text}")
        print(f"  Image-only pages: {without_text}")
        print(f"  OCR requested: {args.ocr}")
        print(f"  OCR backend:   {backend or 'disabled'}")

        start_time = time.time()
        print("\nConverting...", flush=True)

        images_dir.mkdir(parents=True, exist_ok=True)

        try:
            md_text = extract_markdown(
                pdf_path=pdf_path,
                page_numbers=page_numbers,
                force_ocr=args.ocr,
                backend=backend,
                langs=langs,
                images_dir=images_dir,
            )
        except Exception as exc:
            print(f"\nError converting {pdf_path.name}: {exc}")
            if len(pdf_files) > 1:
                print("  Skipping this file...")
                continue
            sys.exit(1)

        print("  Post-processing markdown...")
        md_text = cleanup_markdown(md_text, pdf_path, images_dir, output_path)
        output_path.write_text(md_text, encoding="utf-8")

        image_count = len(list(images_dir.glob("*")))
        if image_count == 0:
            images_dir.rmdir()

        elapsed = time.time() - start_time
        output_size_kb = output_path.stat().st_size / 1024
        print("\nDone!")
        print(f"  Markdown: {output_path} ({output_size_kb:.1f} KB)")
        if image_count > 0:
            print(f"  Images:   {image_count} files in {images_dir}/")
        else:
            print("  Images:   none extracted")
        print(f"  Time:     {elapsed:.1f}s")

    if len(pdf_files) > 1:
        batch_elapsed = time.time() - batch_start
        print(f"\n{'=' * 40}")
        print(f"Batch complete: {len(pdf_files)} files in {batch_elapsed:.1f}s")


if __name__ == "__main__":
    main()
