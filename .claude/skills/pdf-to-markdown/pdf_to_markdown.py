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
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdfmd_models import ConversionContext, MarkdownHeading, OutlineEntry, Region
from pdfmd_ocr import get_ocr_function, map_lang_codes, resolve_ocr_resolution

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


def remove_running_headers(md_text: str) -> str:
    """Remove repeated running headers while leaving heading depth untouched."""
    lines = md_text.split("\n")
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$")

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

    if not running_headers:
        return "\n".join(lines)

    result: list[str] = []
    skip_next_blank = False
    in_code = False
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            skip_next_blank = False
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue

        match = heading_re.match(line)
        if match and match.group(2).strip() in running_headers:
            skip_next_blank = True
            continue
        if skip_next_blank and line.strip() == "":
            skip_next_blank = False
            continue
        skip_next_blank = False
        result.append(line)

    return "\n".join(result)


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
        words: list[dict[str, float | str]] = []
        for word in line:
            if not str(word.tag).endswith("word"):
                continue
            if word.text:
                words.append(
                    {
                        "x0": float(word.attrib["xMin"]),
                        "x1": float(word.attrib["xMax"]),
                        "text": word.text,
                    }
                )
        text = " ".join(str(word["text"]) for word in words).rstrip()
        if not text:
            continue
        lines.append(
            {
                "x0": float(line.attrib["xMin"]),
                "y0": float(line.attrib["yMin"]),
                "x1": float(line.attrib["xMax"]),
                "y1": float(line.attrib["yMax"]),
                "text": text,
                "words": words,
            }
        )

    xml_cache[page_no] = lines
    return lines


def extract_page_lines_from_pymupdf(
    pdf_path: Path,
    page_no: int,
    xml_cache: dict[int, list[dict[str, float | str]]],
) -> list[dict[str, float | str]]:
    """Extract positioned lines for one page using PyMuPDF word geometry."""
    cache_key = -page_no
    if cache_key in xml_cache:
        return xml_cache[cache_key]

    import pymupdf

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
    for (_block_no, _line_no), entries in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
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
                "words": [
                    {"x0": entry[0], "x1": entry[2], "text": entry[4]}
                    for entry in entries
                ],
            }
        )

    xml_cache[cache_key] = lines
    return lines


def extract_page_style_lines(
    pdf_path: Path,
    page_no: int,
    style_cache: dict[int, list[dict[str, float | str]]],
) -> list[dict[str, float | str]]:
    """Extract positioned lines plus font-size metadata using PyMuPDF text dict output."""
    cache_key = page_no
    if cache_key in style_cache:
        return style_cache[cache_key]

    import pymupdf

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


def extract_page_line_infos(
    pdf_path: Path,
    page_no: int,
    xml_cache: dict[int, list[dict[str, float | str]]],
) -> list[dict[str, float | str]]:
    """Extract positioned lines, preferring pdftotext and falling back to PyMuPDF."""
    try:
        lines = extract_page_lines_from_bbox_layout(pdf_path, page_no, xml_cache)
        if lines:
            return lines
    except (subprocess.SubprocessError, ET.ParseError, OSError):
        pass
    return extract_page_lines_from_pymupdf(pdf_path, page_no, xml_cache)


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


def recover_box_line_infos(
    pdf_path: Path,
    page_no: int,
    box: list[int],
    xml_cache: dict[int, list[dict[str, float | str]]],
) -> list[dict[str, float | str]]:
    """Recover positioned lines for a page box using layout coordinates."""
    try:
        lines = extract_page_line_infos(pdf_path, page_no, xml_cache)
    except Exception:
        return []

    selected = [line for line in lines if line_overlaps_box(line, box)]
    return sorted(selected, key=lambda line: (float(line["y0"]), float(line["x0"])))


def cluster_lines_by_row(
    line_infos: list[dict[str, float | str]],
    y_tolerance: float = 5.0,
) -> list[list[dict[str, float | str]]]:
    """Group positioned lines into visual rows."""
    rows: list[list[dict[str, float | str]]] = []
    row_centers: list[float] = []

    for line in sorted(line_infos, key=lambda item: (float(item["y0"]), float(item["x0"]))):
        center_y = (float(line["y0"]) + float(line["y1"])) / 2.0
        if rows and abs(center_y - row_centers[-1]) <= y_tolerance:
            rows[-1].append(line)
            row_centers[-1] = (row_centers[-1] * (len(rows[-1]) - 1) + center_y) / len(rows[-1])
        else:
            rows.append([line])
            row_centers.append(center_y)

    for row in rows:
        row.sort(key=lambda item: float(item["x0"]))
    return rows


def strip_list_marker(text: str) -> str:
    """Remove a visual bullet marker from a short label."""
    return re.sub(r"^[•*-]\s*", "", text).strip()


def rows_to_cells(rows: list[list[dict[str, float | str]]]) -> list[list[dict[str, object]]]:
    """Convert clustered row lines into text/x-position cells."""
    if not rows or not any(rows):
        return []

    cooked: list[list[dict[str, object]]] = []
    for row in rows:
        cells: list[dict[str, object]] = []
        for line in row:
            line_words = line.get("words")
            if isinstance(line_words, list) and line_words:
                current_words: list[str] = []
                current_x0 = float(line_words[0]["x0"])
                prev_x1 = float(line_words[0]["x0"])

                def flush_current() -> None:
                    if not current_words:
                        return
                    raw_text = " ".join(current_words).rstrip()
                    cells.append(
                        {
                            "x0": current_x0,
                            "text": raw_text,
                            "norm": normalize_inline_spacing(raw_text),
                        }
                    )

                for idx, word in enumerate(line_words):
                    word_text = str(word["text"]).strip()
                    if not word_text:
                        continue
                    word_x0 = float(word["x0"])
                    word_x1 = float(word["x1"])
                    if idx == 0:
                        current_words = [word_text]
                        current_x0 = word_x0
                        prev_x1 = word_x1
                        continue

                    gap = word_x0 - prev_x1
                    if gap > 14:
                        flush_current()
                        current_words = [word_text]
                        current_x0 = word_x0
                    else:
                        current_words.append(word_text)
                    prev_x1 = word_x1

                flush_current()
            else:
                raw_text = str(line["text"]).rstrip()
                if not raw_text.strip():
                    continue
                cells.append(
                    {
                        "x0": float(line["x0"]),
                        "text": raw_text,
                        "norm": normalize_inline_spacing(raw_text),
                    }
                )
        if cells:
            cells.sort(key=lambda cell: float(cell["x0"]))
            cooked.append(cells)
    return cooked


def row_text(row: list[dict[str, object]], normalized: bool = False) -> str:
    """Join one visual row into a single text string."""
    key = "norm" if normalized else "text"
    return " ".join(str(cell[key]) for cell in row).strip()


def block_base_x(rows: list[list[dict[str, object]]]) -> float:
    """Return the left-most x-position in a structured block."""
    return min(float(cell["x0"]) for row in rows for cell in row)


def leading_x_positions(rows: list[list[dict[str, object]]]) -> list[float]:
    """Return the leading x-position for each non-empty row."""
    return [float(row[0]["x0"]) for row in rows if row]


def indent_levels(rows: list[list[dict[str, object]]], step: float = 10.0) -> set[int]:
    """Estimate distinct indentation levels within a block."""
    base_x = block_base_x(rows)
    return {
        max(0, int(round((float(row[0]["x0"]) - base_x) / step)))
        for row in rows
        if row
    }


def build_region(
    page_no: int,
    source_class: str,
    start: int,
    stop: int,
    bbox: list[int] | tuple[float, float, float, float],
    snippet: str,
    line_infos: list[dict[str, float | str]],
) -> Region:
    """Build a generic layout region from raw line geometry."""
    return Region(
        page_no=page_no,
        source_class=source_class,
        start=start,
        stop=stop,
        bbox=tuple(float(value) for value in bbox),
        snippet=snippet,
        rows=rows_to_cells(cluster_lines_by_row(line_infos)),
    )


def region_texts(region: Region, normalized: bool = False) -> list[str]:
    """Return one text string per visual row."""
    return [row_text(row, normalized=normalized) for row in region.rows]


def region_avg_words(region: Region) -> float:
    """Return average words per visual row for a region."""
    texts = region_texts(region)
    return sum(len(text.split()) for text in texts) / max(1, len(texts))


def region_is_structured(region: Region) -> bool:
    """Return True when a region has stable non-prose layout signals."""
    if not region.rows:
        return False

    texts = region_texts(region)
    short_rows = sum(1 for text in texts if len(text) <= 72)
    proseish_rows = sum(
        1 for text in texts if len(text.split()) >= 10 and text.endswith((".", "!", "?"))
    )
    punctuation_rows = sum(1 for text in texts if re.search(r"[{}[\]<>:=;/\\|()]", text))
    avg_words = region_avg_words(region)
    indent_count = len(indent_levels(region.rows))
    multi_col = max(len(row) for row in region.rows) >= 2
    indent_span = max(leading_x_positions(region.rows)) - min(leading_x_positions(region.rows))

    return (
        len(region.rows) >= 2
        and short_rows >= max(2, len(texts) - 1)
        and proseish_rows <= 1
        and avg_words <= 8
        and (multi_col or indent_count >= 2 or indent_span >= 12 or punctuation_rows >= 3)
    )


def rows_look_like_definition_table(rows: list[list[dict[str, object]]]) -> bool:
    """Heuristic for 2-column command/description style layouts."""
    if len(rows) < 3 or max(len(row) for row in rows) > 2:
        return False

    paired = [row for row in rows if len(row) == 2]
    if len(paired) < max(3, len(rows) - 1):
        return False

    left_texts = [strip_list_marker(row_text([row[0]], normalized=True)) for row in paired]
    right_texts = [row_text([row[1]], normalized=True) for row in paired]
    left_avg = sum(len(text) for text in left_texts) / len(left_texts)
    right_avg = sum(len(text) for text in right_texts) / len(right_texts)
    joined = " ".join(left_texts + right_texts)
    codeish = bool(re.search(r"[{}[\]#;=]", joined))
    left_x_positions = [float(row[0]["x0"]) for row in paired]
    right_x_positions = [float(row[1]["x0"]) for row in paired]
    aligned_columns = (
        max(left_x_positions) - min(left_x_positions) <= 16
        and max(right_x_positions) - min(right_x_positions) <= 24
    )
    return aligned_columns and left_avg <= 32 and right_avg >= left_avg and not codeish


def render_definition_table(rows: list[list[dict[str, object]]]) -> str:
    """Render a 2-column layout as a Markdown table."""
    table_rows: list[tuple[str, str]] = []
    paired = [row for row in rows if len(row) >= 2]
    if not paired:
        return ""

    right_anchor = min(float(row[1]["x0"]) for row in paired)
    for row in rows:
        if len(row) >= 2:
            left = strip_list_marker(row_text([row[0]], normalized=True))
            right = row_text([row[1]], normalized=True)
            if left and right:
                table_rows.append((left, right))
        elif (
            len(row) == 1
            and table_rows
            and float(row[0]["x0"]) >= right_anchor - 12
        ):
            continuation = row_text(row, normalized=True)
            table_rows[-1] = (table_rows[-1][0], f"{table_rows[-1][1]} {continuation}".strip())

    if len(table_rows) < 2:
        return ""

    out = ["| Item | Description |", "| --- | --- |"]
    for left, right in table_rows:
        out.append(f"| {left} | {right} |")
    return "\n".join(out)


def region_looks_preformatted(region: Region) -> bool:
    """Heuristic for language-agnostic preformatted regions."""
    if not region.rows:
        return False

    texts = region_texts(region)
    normalized_texts = region_texts(region, normalized=True)
    joined = " ".join(texts)
    punctuation_rows = sum(1 for text in texts if re.search(r"[{}[\]<>:=;/\\|()]", text))
    delimiter_rows = sum(1 for text in texts if re.search(r"[_./\\-]", text))
    short_rows = sum(1 for text in texts if len(text) <= 72)
    proseish_rows = sum(
        1 for text in texts if len(text.split()) >= 10 and text.endswith((".", "!", "?"))
    )
    avg_words = sum(len(text.split()) for text in texts) / max(1, len(texts))
    sentence_like = sum(text.endswith((".", "!", "?")) for text in texts)
    aligned_layout = region_is_structured(region)
    snippet_norm = normalize_whitespace(region.snippet)
    rows_norm = normalize_whitespace("\n".join(normalized_texts))

    if not aligned_layout:
        return False
    if (
        len(region.rows) >= 3
        and sentence_like <= 1
        and avg_words <= 6
        and (punctuation_rows >= 1 or delimiter_rows >= 2)
    ):
        return True
    if (
        len(region.rows) >= 4
        and short_rows >= len(region.rows) - 1
        and proseish_rows == 0
        and avg_words <= 5
    ):
        return True
    return (
        punctuation_rows >= 1
        and proseish_rows == 0
        and avg_words <= 6
        and snippet_norm == rows_norm
    )


def render_layout_code_block(rows: list[list[dict[str, object]]]) -> str:
    """Render layout rows as an aligned fenced code block."""
    base_x = block_base_x(rows)
    chars_per_unit = 5.5
    rendered: list[str] = []
    for row in rows:
        line = ""
        for cell in row:
            text = str(cell["text"]).rstrip()
            target_col = max(0, int(round((float(cell["x0"]) - base_x) / chars_per_unit)))
            if not line:
                line = " " * target_col + text
                continue
            if len(line) < target_col:
                line += " " * (target_col - len(line))
            else:
                line += " "
            line += text
        rendered.append(line.rstrip())
    return "\n".join(rendered)


def render_structured_box(
    region: Region,
) -> tuple[str, str] | None:
    """Render a layout-aware replacement for a text region when possible."""
    rows = region.rows
    if not rows:
        return None

    if rows_look_like_definition_table(rows):
        table = render_definition_table(rows)
        if table:
            return ("table", table)

    if region_looks_preformatted(region):
        return ("code", render_layout_code_block(rows))

    return None


def overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
    """Return vertical overlap ratio for two spans."""
    overlap = max(0.0, min(a1, b1) - max(a0, b0))
    base = max(1.0, min(a1 - a0, b1 - b0))
    return overlap / base


def horizontal_overlap_ratio(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """Return horizontal overlap ratio for two bounding boxes."""
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    base = max(1.0, min(a[2] - a[0], b[2] - b[0]))
    return overlap / base


def find_definition_table_groups(page_boxes: list[dict]) -> list[list[int]]:
    """Find groups of left list items plus right description text that form a two-column table."""
    groups: list[list[int]] = []
    i = 0
    while i < len(page_boxes):
        if page_boxes[i].get("class") != "list-item":
            i += 1
            continue

        left_indices: list[int] = []
        while i < len(page_boxes) and page_boxes[i].get("class") == "list-item":
            left_indices.append(i)
            i += 1

        if len(left_indices) < 3:
            continue

        left_boxes = [page_boxes[idx]["bbox"] for idx in left_indices]
        left_x1 = max(box[2] for box in left_boxes)
        left_y0 = min(box[1] for box in left_boxes)
        left_y1 = max(box[3] for box in left_boxes)

        candidate_idx = None
        for j in range(i, min(i + 3, len(page_boxes))):
            box_info = page_boxes[j]
            if box_info.get("class") != "text":
                continue
            bbox = box_info["bbox"]
            if bbox[0] < left_x1 + 60:
                continue
            if overlap_ratio(left_y0, left_y1, bbox[1], bbox[3]) < 0.7:
                continue
            candidate_idx = j
            break

        if candidate_idx is not None:
            groups.append(left_indices + [candidate_idx])

    return groups


def can_group_preformatted_regions(left: Region, right: Region, separator: str) -> bool:
    """Return True when two neighboring text regions likely belong to one preformatted block."""
    if separator.strip():
        return False
    if not left.rows or not right.rows:
        return False

    vertical_gap = right.bbox[1] - left.bbox[3]
    left_edge_delta = abs(left.bbox[0] - right.bbox[0])
    right_edge_delta = abs(left.bbox[2] - right.bbox[2])
    x_overlap = horizontal_overlap_ratio(left.bbox, right.bbox)
    structured_pair = region_is_structured(left) or region_is_structured(right)

    return (
        structured_pair
        and vertical_gap <= 28
        and x_overlap >= 0.6
        and left_edge_delta <= 28
        and right_edge_delta <= 40
        and region_avg_words(left) <= 8
        and region_avg_words(right) <= 8
    )


def restore_code_blocks_in_chunk(
    page_text: str,
    page_boxes: list[dict],
    pdf_path: Path,
    page_no: int,
    xml_cache: dict[int, list[dict[str, float | str]]],
) -> str:
    """Replace flattened code-like page boxes with fenced multi-line blocks."""
    replacements: list[tuple[int, int, str, str]] = []
    consumed: set[int] = set()

    for group in find_definition_table_groups(page_boxes):
        boxes = [page_boxes[idx] for idx in group]
        union_box = [
            min(box["bbox"][0] for box in boxes),
            min(box["bbox"][1] for box in boxes),
            max(box["bbox"][2] for box in boxes),
            max(box["bbox"][3] for box in boxes),
        ]
        start = min(box["pos"][0] for box in boxes)
        stop = max(box["pos"][1] for box in boxes)
        line_infos = recover_box_line_infos(pdf_path, page_no, union_box, xml_cache)
        region = build_region(
            page_no=page_no,
            source_class="group",
            start=start,
            stop=stop,
            bbox=union_box,
            snippet=page_text[start:stop],
            line_infos=line_infos,
        )
        rendered = render_structured_box(region)
        if rendered:
            kind, body = rendered
            if kind == "table":
                replacements.append((start, stop, "table", f"\n\n{body}\n\n"))
                consumed.update(group)
                continue
            if kind == "code":
                replacements.append((start, stop, "code", f"\n\n```\n{body}\n```\n\n"))
                consumed.update(group)

    text_regions: dict[int, Region] = {}
    for idx, box_info in enumerate(page_boxes):
        if idx in consumed or box_info.get("class") != "text":
            continue
        start, stop = box_info["pos"]
        line_infos = recover_box_line_infos(pdf_path, page_no, box_info["bbox"], xml_cache)
        text_regions[idx] = build_region(
            page_no=page_no,
            source_class=str(box_info.get("class", "text")),
            start=start,
            stop=stop,
            bbox=box_info["bbox"],
            snippet=page_text[start:stop],
            line_infos=line_infos,
        )

    idx = 0
    while idx < len(page_boxes):
        if idx in consumed or idx not in text_regions:
            idx += 1
            continue

        group = [idx]
        cursor = idx
        while True:
            next_idx = cursor + 1
            if (
                next_idx >= len(page_boxes)
                or next_idx in consumed
                or next_idx not in text_regions
            ):
                break
            separator = page_text[text_regions[cursor].stop : text_regions[next_idx].start]
            if not can_group_preformatted_regions(text_regions[cursor], text_regions[next_idx], separator):
                break
            group.append(next_idx)
            cursor = next_idx

        if len(group) >= 2:
            boxes = [page_boxes[group_idx] for group_idx in group]
            union_box = [
                min(box["bbox"][0] for box in boxes),
                min(box["bbox"][1] for box in boxes),
                max(box["bbox"][2] for box in boxes),
                max(box["bbox"][3] for box in boxes),
            ]
            start = min(box["pos"][0] for box in boxes)
            stop = max(box["pos"][1] for box in boxes)
            line_infos = recover_box_line_infos(pdf_path, page_no, union_box, xml_cache)
            merged_region = build_region(
                page_no=page_no,
                source_class="merged-text",
                start=start,
                stop=stop,
                bbox=union_box,
                snippet=page_text[start:stop],
                line_infos=line_infos,
            )
            rendered = render_structured_box(merged_region)
            if rendered and rendered[0] == "code":
                replacements.append((start, stop, "code", f"\n\n```\n{rendered[1]}\n```\n\n"))
                consumed.update(group)
        idx += 1

    for idx, box_info in enumerate(page_boxes):
        if idx in consumed:
            continue
        if box_info.get("class") != "text":
            continue

        start, stop = box_info["pos"]
        snippet = page_text[start:stop]
        region = text_regions.get(idx)
        if region is None:
            line_infos = recover_box_line_infos(pdf_path, page_no, box_info["bbox"], xml_cache)
            region = build_region(
                page_no=page_no,
                source_class=str(box_info.get("class", "text")),
                start=start,
                stop=stop,
                bbox=box_info["bbox"],
                snippet=snippet,
                line_infos=line_infos,
            )
        rendered = render_structured_box(region)
        if rendered:
            kind, body = rendered
            if kind == "table":
                replacements.append((start, stop, "table", f"\n\n{body}\n\n"))
                continue
            if kind == "code":
                replacements.append((start, stop, "code", f"\n\n```\n{body}\n```\n\n"))
                continue

    if not replacements:
        return page_text

    merged: list[tuple[int, int, str, str]] = []
    for start, stop, kind, replacement in sorted(replacements):
        if (
            merged
            and kind == "code"
            and merged[-1][2] == "code"
            and not page_text[merged[-1][1] : start].strip()
        ):
            prev_start, _prev_stop, prev_kind, prev_replacement = merged[-1]
            prev_body = prev_replacement.strip()
            body = replacement.strip()
            if prev_body.startswith("```") and prev_body.endswith("```") and body.startswith("```") and body.endswith("```"):
                prev_inner = prev_body[3:-3].strip("\n")
                inner = body[3:-3].strip("\n")
                merged_body = f"\n\n```\n{prev_inner}\n{inner}\n```\n\n"
                merged[-1] = (prev_start, stop, prev_kind, merged_body)
                continue
        merged.append((start, stop, kind, replacement))

    updated = page_text
    for start, stop, _kind, replacement in sorted(merged, reverse=True):
        updated = updated[:start] + replacement + updated[stop:]
    return updated


def make_image_refs_relative(
    md_text: str,
    images_dir: Path,
    output_dir: Path,
    source_images_dir: Path | None = None,
) -> str:
    """Rewrite absolute image references to relative, URL-encoded paths."""
    if not images_dir.exists():
        return md_text

    rel_images = str(images_dir.relative_to(output_dir))
    search_paths: list[str] = []
    for path in [source_images_dir, images_dir]:
        if path is None:
            continue
        for variant in {str(path), str(path.resolve())}:
            if variant and variant not in search_paths:
                search_paths.append(variant)

    for path in sorted(search_paths, key=len, reverse=True):
        md_text = md_text.replace(path, rel_images)

    def encode_image_path(match: re.Match[str]) -> str:
        alt = match.group(1)
        path = quote(match.group(2), safe="/")
        return f"![{alt}]({path})"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", encode_image_path, md_text)


def merge_adjacent_fenced_blocks(md_text: str) -> str:
    """Merge consecutive fenced code blocks separated only by blank lines."""
    pattern = re.compile(r"```\n(.*?)\n```\s*\n+\s*```\n(.*?)\n```", re.DOTALL)
    previous = None
    while md_text != previous:
        previous = md_text
        md_text = pattern.sub(lambda m: f"```\n{m.group(1).rstrip()}\n{m.group(2).lstrip()}\n```", md_text)
    return md_text


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
    text = re.sub(r"[‐‑‒–—―]+", "-", text)
    text = re.sub(rf"\s*\({PAGE_MARKER_RE}\)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\.{2,}", " ", text)
    text = re.sub(r"[•·]+", " ", text)
    text = normalize_inline_spacing(text)
    text = text.strip(" -.:;[](){}")
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
    stripped_number = re.sub(r"^(?:[A-Z]\.)?\d+(?:\.\d+)*[:.)-]?\s*", "", text)
    stripped_chapter = re.sub(r"^(?:chapter|appendix)\s+[A-Z0-9]+[:.)-]?\s*", "", stripped_number, flags=re.IGNORECASE)
    keys = {text.lower()}
    if stripped_number != text:
        keys.add(stripped_number.lower())
    if stripped_chapter != stripped_number:
        keys.add(stripped_chapter.lower())

    for variant in {text.lower(), stripped_number.lower(), stripped_chapter.lower()}:
        compact = re.sub(r"[^a-z0-9]+", "", variant)
        if compact:
            keys.add(compact)

    no_apostrophes = {
        variant.replace("'", "").replace("’", "")
        for variant in {text.lower(), stripped_number.lower(), stripped_chapter.lower()}
        if variant
    }
    for variant in no_apostrophes:
        if variant:
            keys.add(variant)
        compact_no_apostrophes = re.sub(r"[^a-z0-9]+", "", variant)
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


def split_inline_bullet_runs(md_text: str) -> str:
    """Split flattened inline bullet sequences into one bullet per line."""
    lines = md_text.splitlines()
    result: list[str] = []
    in_code = False

    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            result.append(line)
            continue
        if in_code or line.startswith("|"):
            result.append(line)
            continue

        stripped = line.strip()
        if not stripped.startswith("- ") or "•" not in stripped:
            result.append(line)
            continue

        parts = [normalize_inline_spacing(part) for part in re.split(r"\s+[•·]\s+", stripped[2:].strip())]
        parts = [part for part in parts if part]
        if len(parts) <= 1:
            result.append(line)
            continue

        result.extend(f"- {part}" for part in parts)

    return "\n".join(result)


def dedupe_adjacent_bullets(md_text: str) -> str:
    """Drop immediately repeated bullet lines outside fenced code blocks."""
    lines = md_text.splitlines()
    result: list[str] = []
    in_code = False
    recent_bullets: list[str] = []

    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            recent_bullets = []
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue

        stripped = line.strip()
        if stripped.startswith("- "):
            normalized = normalize_inline_spacing(stripped[2:])
            if normalized in recent_bullets:
                continue
            recent_bullets.append(normalized)
            recent_bullets = recent_bullets[-6:]
        elif stripped:
            recent_bullets = []

        result.append(line)

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


def extract_pdf_outline(pdf_path: Path, page_numbers: list[int] | None = None) -> list[OutlineEntry]:
    """Read the PDF outline/bookmark tree, optionally filtered to selected pages."""
    import pymupdf

    selected_pages = {page + 1 for page in page_numbers} if page_numbers is not None else None
    doc = pymupdf.open(str(pdf_path))
    outline = doc.get_toc()
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


def parse_contents_page_title_and_page(text: str) -> tuple[str, int]:
    """Split a visible TOC line into a title and optional page number."""
    stripped = strip_markdown_inline(text).replace("…", ".")
    match = re.search(
        rf"(.+?)(?:\s*[.\u2026]{{2,}}\s*|\s+)(?<![\w/])({PAGE_MARKER_RE})(?![\w/])\s*$",
        stripped,
        flags=re.IGNORECASE,
    )
    if match:
        title = sanitize_contents_entry(match.group(1))
        page_text = match.group(2)
        try:
            page = int(page_text)
        except ValueError:
            page = 0
        return title, page

    return sanitize_contents_entry(stripped), 0


def cluster_indent_levels(x_positions: list[float], tolerance: float = 8.0) -> list[float]:
    """Cluster nearby x positions into indentation bands."""
    if not x_positions:
        return []

    clusters: list[float] = []
    for x in sorted(x_positions):
        if not clusters or abs(x - clusters[-1]) > tolerance:
            clusters.append(x)
        else:
            clusters[-1] = (clusters[-1] + x) / 2.0
    return clusters


def looks_like_toc_title_only_line(text: str) -> bool:
    """Return True for short title-like TOC lines without explicit page markers."""
    if text.rstrip().endswith((".", "!", "?")):
        return False
    cleaned = sanitize_contents_entry(text)
    if not cleaned:
        return False
    if len(cleaned.split()) > 12:
        return False
    if len(cleaned) > 90:
        return False
    if cleaned.endswith((".", "!", "?")):
        return False
    return bool(re.search(r"[A-Za-z]", cleaned))


def extract_contents_outline_from_pdf(
    pdf_path: Path,
    page_numbers: list[int] | None = None,
    style_cache: dict[int, list[dict[str, float | str]]] | None = None,
) -> list[OutlineEntry]:
    """Extract a best-effort outline from visible contents pages using source layout."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    try:
        total_pages = doc.page_count
    finally:
        doc.close()

    selected_pages = [page + 1 for page in page_numbers] if page_numbers is not None else list(range(1, total_pages + 1))
    if style_cache is None:
        style_cache = {}
    entries_by_page: list[tuple[int, list[dict[str, float | str]]]] = []
    in_contents_run = False

    for page_no in selected_pages:
        lines = extract_page_style_lines(pdf_path, page_no, style_cache)
        heading_idx = next(
            (idx for idx, line in enumerate(lines) if looks_like_contents_heading(str(line["text"]))),
            None,
        )

        start_idx = 0
        if heading_idx is not None:
            in_contents_run = True
            start_idx = heading_idx + 1
        elif not in_contents_run:
            continue

        page_entries: list[dict[str, float | str]] = []
        for line in lines[start_idx:]:
            text = str(line["text"]).strip()
            if not text:
                continue

            if looks_like_contents_heading(text):
                continue

            title, page = parse_contents_page_title_and_page(text)
            has_leader = bool(re.search(r"[.\u2026]{4,}", text))
            if not title:
                continue
            if looks_like_contents_heading(title):
                continue

            if not has_leader and page == 0:
                aligned_with_existing = (
                    bool(page_entries)
                    and any(abs(float(line["x0"]) - float(entry["x0"])) <= 12 for entry in page_entries)
                )
                if not (aligned_with_existing and looks_like_toc_title_only_line(title)):
                    if page_entries:
                        break
                    continue

            page_entries.append(
                {
                    "x0": float(line["x0"]),
                    "title": title,
                    "page": page,
                }
            )

        if page_entries:
            entries_by_page.append((page_no, page_entries))
            continue

        if in_contents_run:
            break

    if not entries_by_page:
        return []

    x_positions = [float(entry["x0"]) for _page_no, page_entries in entries_by_page for entry in page_entries]
    indent_bands = cluster_indent_levels(x_positions)
    if not indent_bands:
        return []

    entries: list[OutlineEntry] = []
    seen: set[tuple[int, str]] = set()
    for _page_no, page_entries in entries_by_page:
        for entry in page_entries:
            x0 = float(entry["x0"])
            band_idx = min(range(len(indent_bands)), key=lambda idx: abs(x0 - indent_bands[idx]))
            level = band_idx + 1
            title = str(entry["title"])
            key = (level, title.lower())
            if key in seen:
                continue
            seen.add(key)
            entries.append(OutlineEntry(level=level, title=title, page=int(entry["page"])))

    return entries


def infer_contents_entry_level(text: str, previous_level: int | None = None) -> int:
    """Infer a structural level for a visible TOC entry."""
    cleaned = sanitize_contents_entry(text)
    lower = cleaned.lower()

    if re.match(r"^(chapter|appendix)\b", cleaned, re.IGNORECASE):
        return 1
    if lower in {"foreword", "forward", "preface", "introduction", "bibliography", "index"}:
        return 1

    match = re.match(r"^(?:([A-Z])\.)?(\d+(?:\.\d+)*)\b", cleaned)
    if match:
        appendix_prefix = 1 if match.group(1) else 0
        return len(match.group(2).split(".")) + appendix_prefix

    if previous_level == 1:
        return 2
    if previous_level is not None:
        return previous_level
    return 1


def extract_contents_outline_from_markdown(md_text: str) -> list[OutlineEntry]:
    """Extract a best-effort internal outline from a visible Markdown contents section."""
    lines = md_text.splitlines()
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    entries: list[OutlineEntry] = []
    in_contents = False
    previous_level: int | None = None
    seen: set[tuple[int, str]] = set()

    for line in lines:
        match = heading_re.match(line)
        if match:
            heading_text = strip_markdown_inline(match.group(2))
            if looks_like_contents_heading(heading_text):
                in_contents = True
                previous_level = None
                continue
            if in_contents:
                break

        if not in_contents:
            continue

        bullet_match = re.match(r"^(\s*)-\s+(.+?)\s*$", line)
        if not bullet_match:
            continue

        indent, raw_text = bullet_match.groups()
        text = sanitize_contents_entry(raw_text)
        if not text or looks_like_contents_heading(text):
            continue

        indent_level = len(indent) // 2 + 1 if indent else None
        level = indent_level or infer_contents_entry_level(text, previous_level)
        key = (level, text.lower())
        if key in seen:
            continue

        entries.append(OutlineEntry(level=level, title=text, page=0))
        seen.add(key)
        previous_level = level

    return entries


def extract_markdown_headings(md_text: str) -> list[MarkdownHeading]:
    """Extract headings from generated Markdown while ignoring fenced code blocks."""
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    headings: list[MarkdownHeading] = []
    lines = md_text.splitlines()
    in_code = False

    for line_idx, line in enumerate(lines):
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue

        match = heading_re.match(line)
        if not match:
            continue

        heading_text = strip_markdown_inline(match.group(2))
        if looks_like_contents_heading(heading_text):
            continue

        slug = slugify_heading(heading_text)
        if not slug:
            continue

        headings.append(
            MarkdownHeading(
                line_idx=line_idx,
                text=heading_text,
                keys=heading_match_keys(heading_text),
                slug=slug,
                original_level=len(match.group(1)),
            )
        )

    return headings


def normalized_heading_token(text: str) -> str:
    """Normalize heading text to a compact token for order-preserving source matching."""
    return re.sub(r"[^a-z0-9]+", "", sanitize_contents_entry(text).lower())


def match_headings_to_source_lines(
    headings: list[MarkdownHeading],
    pdf_path: Path,
    page_numbers: list[int] | None = None,
    style_cache: dict[int, list[dict[str, float | str]]] | None = None,
) -> dict[int, dict[str, float | str]]:
    """Match markdown headings to styled PDF lines in document order."""
    if not headings:
        return {}

    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    try:
        page_count = doc.page_count
    finally:
        doc.close()

    selected_pages = [page + 1 for page in page_numbers] if page_numbers is not None else list(range(1, page_count + 1))
    if style_cache is None:
        style_cache = {}
    source_lines: list[dict[str, float | str]] = []

    for page_no in selected_pages:
        for line in extract_page_style_lines(pdf_path, page_no, style_cache):
            token = normalized_heading_token(str(line["text"]))
            if not token:
                continue
            source_lines.append(
                {
                    **line,
                    "page_no": page_no,
                    "token": token,
                }
            )

    matches: dict[int, dict[str, float | str]] = {}
    source_idx = 0
    for heading_idx, heading in enumerate(headings):
        heading_tokens = {normalized_heading_token(heading.text)}
        heading_tokens.update(normalized_heading_token(key) for key in heading.keys)
        heading_tokens.discard("")
        if not heading_tokens:
            continue

        for candidate_idx in range(source_idx, len(source_lines)):
            token = str(source_lines[candidate_idx]["token"])
            if token in heading_tokens:
                matches[heading_idx] = source_lines[candidate_idx]
                source_idx = candidate_idx + 1
                break

    return matches


def map_outline_to_headings(
    outline: list[OutlineEntry], headings: list[MarkdownHeading]
) -> dict[int, OutlineEntry]:
    """Map outline entries to markdown headings in document order."""
    if not outline or not headings:
        return {}

    candidates_by_key: dict[str, list[int]] = {}
    for idx, heading in enumerate(headings):
        for key in heading.keys:
            candidates_by_key.setdefault(key, []).append(idx)

    matches: dict[int, OutlineEntry] = {}
    last_heading_idx = -1

    for entry in outline:
        chosen_idx: int | None = None
        chosen_score: tuple[int, int] | None = None
        entry_clean = sanitize_contents_entry(entry.title)
        entry_is_chapterish = bool(re.match(r"^(chapter|appendix)\b", entry_clean, re.IGNORECASE))
        entry_is_dotted = bool(re.match(r"^(?:[A-Z]\.)?\d+(?:\.\d+)+\b", entry_clean))

        for key in heading_match_keys(entry.title):
            for candidate_idx in candidates_by_key.get(key, []):
                if candidate_idx <= last_heading_idx:
                    continue

                heading = headings[candidate_idx]
                heading_clean = sanitize_contents_entry(heading.text)
                heading_is_chapterish = bool(
                    re.match(r"^(chapter|appendix)\b", heading_clean, re.IGNORECASE)
                )
                heading_is_dotted = bool(re.match(r"^(?:[A-Z]\.)?\d+(?:\.\d+)+\b", heading_clean))

                score = 0
                if entry_is_chapterish and heading_is_chapterish:
                    score += 4
                elif entry_is_chapterish != heading_is_chapterish:
                    score -= 4

                if entry_is_dotted and heading_is_dotted:
                    score += 2

                if heading_clean.lower() == entry_clean.lower():
                    score += 1

                candidate_score = (score, -candidate_idx)
                if chosen_score is None or candidate_score > chosen_score:
                    chosen_idx = candidate_idx
                    chosen_score = candidate_score

            if chosen_idx is not None and chosen_score is not None and chosen_score[0] >= 4:
                break

        if chosen_idx is None:
            continue

        entry.slug = headings[chosen_idx].slug
        matches[chosen_idx] = entry
        last_heading_idx = chosen_idx

    return matches


def infer_heading_rank(text: str, original_level: int) -> int:
    """Infer a generic structural rank from numbering and broad heading conventions."""
    cleaned = sanitize_contents_entry(strip_markdown_inline(text)).strip()
    lower = cleaned.lower()

    if re.match(r"^(chapter|appendix)\b", lower):
        return 1
    if lower in {"foreword", "forward", "preface", "introduction", "bibliography", "index"}:
        return 1

    appendix_num = re.match(r"^[A-Z]\.(\d+(?:\.\d+)*)\b", cleaned)
    if appendix_num:
        return len(appendix_num.group(1).split(".")) + 1

    dotted_num = re.match(r"^\d+(?:\.\d+)+\b", cleaned)
    if dotted_num:
        return len(dotted_num.group(0).split("."))

    return max(1, original_level - 1)


def apply_visual_heading_levels(
    md_text: str,
    pdf_path: Path,
    page_numbers: list[int] | None = None,
    style_cache: dict[int, list[dict[str, float | str]]] | None = None,
) -> str:
    """Rebuild heading levels from source typography when no outline is available."""
    headings = extract_markdown_headings(md_text)
    if not headings:
        return md_text

    source_matches = match_headings_to_source_lines(
        headings,
        pdf_path,
        page_numbers,
        style_cache=style_cache,
    )
    if not source_matches:
        return md_text

    raw_sizes = sorted(
        {
            float(match["size"])
            for match in source_matches.values()
            if float(match.get("size", 0.0)) > 0
        },
        reverse=True,
    )
    if not raw_sizes:
        return md_text

    size_buckets: list[float] = []
    for size in raw_sizes:
        if not size_buckets or abs(size - size_buckets[-1]) >= 1.5:
            size_buckets.append(size)

    size_to_level = {
        int(round(size)): min(4, 2 + idx)
        for idx, size in enumerate(size_buckets)
    }
    lines = md_text.splitlines()

    for heading_idx, heading in enumerate(headings):
        source_match = source_matches.get(heading_idx)
        if not source_match:
            continue

        size = int(round(float(source_match.get("size", 0.0))))
        visual_level = size_to_level.get(size)
        if visual_level is None:
            continue

        explicit_rank = infer_heading_rank(heading.text, 1)
        has_explicit_structure = explicit_rank > 1 or bool(
            re.match(r"^(chapter|appendix)\b", sanitize_contents_entry(heading.text), re.IGNORECASE)
        )
        if has_explicit_structure:
            inferred_level = min(6, 2 + max(0, explicit_rank - 1))
            final_level = min(visual_level, inferred_level)
        else:
            final_level = min(visual_level, max(2, heading.original_level))

        lines[heading.line_idx] = f"{'#' * final_level} {heading.text}"

    return "\n".join(lines)


def apply_contents_heading_levels(
    md_text: str,
    contents_outline: list[OutlineEntry],
) -> str:
    """Rewrite heading levels from a visible text TOC when no embedded outline exists."""
    if not contents_outline:
        return md_text

    headings = extract_markdown_headings(md_text)
    matches = map_outline_to_headings(contents_outline, headings)
    if not matches:
        return md_text

    lines = md_text.splitlines()
    current_level: int | None = None

    for idx, heading in enumerate(headings):
        inferred_md_level = min(6, 2 + max(0, infer_heading_rank(heading.text, heading.original_level) - 1))

        if idx in matches:
            current_level = min(6, 2 + max(0, matches[idx].level - 1))
            lines[heading.line_idx] = f"{'#' * current_level} {heading.text}"
            continue

        if current_level is None:
            lines[heading.line_idx] = f"{'#' * inferred_md_level} {heading.text}"
            continue

        lines[heading.line_idx] = f"{'#' * max(inferred_md_level, min(6, current_level + 1))} {heading.text}"

    return "\n".join(lines)


def apply_outline_heading_levels(md_text: str, outline: list[OutlineEntry]) -> str:
    """Rewrite Markdown heading levels from the PDF outline when available."""
    if not outline:
        return md_text

    lines = md_text.splitlines()
    headings = extract_markdown_headings(md_text)
    matches = map_outline_to_headings(outline, headings)
    if not matches:
        return md_text

    root_level = min(entry.level for entry in outline)
    base_md_level = 2

    current_matched_level: int | None = None
    for idx, heading in enumerate(headings):
        line_idx = heading.line_idx
        text = heading.text
        inferred_md_level = min(6, base_md_level + max(0, infer_heading_rank(text, heading.original_level) - 1))

        if idx in matches:
            outline_md_level = base_md_level + max(0, matches[idx].level - root_level)
            current_matched_level = min(6, max(outline_md_level, inferred_md_level))
            lines[line_idx] = f"{'#' * current_matched_level} {text}"
            continue

        if current_matched_level is None:
            lines[line_idx] = f"{'#' * inferred_md_level} {text}"
            continue

        lines[line_idx] = f"{'#' * max(inferred_md_level, min(6, current_matched_level + 1))} {text}"

    return "\n".join(lines)


def strip_contents_sections(md_text: str) -> str:
    """Remove visible contents sections; the heading hierarchy is the primary navigation."""
    lines = md_text.splitlines()
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    result: list[str] = []
    i = 0

    while i < len(lines):
        match = heading_re.match(lines[i])
        if not match:
            result.append(lines[i])
            i += 1
            continue

        heading_text = strip_markdown_inline(match.group(2))
        if not looks_like_contents_heading(heading_text):
            result.append(lines[i])
            i += 1
            continue

        i += 1
        while i < len(lines):
            next_match = heading_re.match(lines[i])
            if next_match and not looks_like_contents_heading(strip_markdown_inline(next_match.group(2))):
                break
            i += 1

        if result and result[-1] != "":
            result.append("")

    return "\n".join(result)




def extract_markdown(
    context: ConversionContext,
    force_ocr: bool,
    backend: str | None,
    langs: list[str],
    images_dir: Path,
) -> tuple[str, Path]:
    """Extract Markdown from a PDF using PyMuPDF4LLM and optional OCR preprocessing."""
    import pymupdf4llm

    pdf_path = context.pdf_path
    page_numbers = context.page_numbers

    with tempfile.TemporaryDirectory(prefix=f"{sanitize_stem(pdf_path.stem)}_images_") as tmp_dir:
        extraction_images_dir = Path(tmp_dir)
        chunks = pymupdf4llm.to_markdown(
            str(pdf_path),
            pages=page_numbers,
            page_chunks=True,
            write_images=True,
            image_path=str(extraction_images_dir),
            use_ocr=bool(backend),
            force_ocr=force_ocr,
            ocr_language=map_lang_codes(langs, "tesseract"),
            ocr_function=get_ocr_function(backend, langs),
            header=False,
            footer=False,
            show_progress=False,
        )

        page_texts: list[str] = []
        for chunk in chunks:
            page_no = int(chunk["metadata"]["page_number"])
            page_text = chunk["text"]
            page_text = restore_code_blocks_in_chunk(
                page_text,
                chunk["page_boxes"],
                pdf_path,
                page_no,
                context.geometry_cache,
            )
            page_texts.append(page_text.strip())

        for src in extraction_images_dir.iterdir():
            target = images_dir / src.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(src), str(target))

        md_text = "\n\n".join(text for text in page_texts if text).strip() + "\n"
        return md_text, extraction_images_dir


def cleanup_markdown(
    md_text: str,
    context: ConversionContext,
    images_dir: Path,
    output_path: Path,
    source_images_dir: Path | None = None,
) -> str:
    """Apply markdown cleanup after extraction."""
    pdf_path = context.pdf_path
    page_numbers = context.page_numbers

    md_text = cleanup_heading_markup(md_text)
    md_text = remove_running_headers(md_text)
    md_text = convert_contents_tables_to_lists(md_text)
    md_text = expand_contents_paragraphs(md_text)

    pdf_outline = get_cached_outline(context)
    if pdf_outline:
        md_text = apply_outline_heading_levels(md_text, pdf_outline)
    else:
        contents_outline = extract_contents_outline_from_pdf(
            pdf_path,
            page_numbers,
            style_cache=context.style_cache,
        )
        if not contents_outline:
            contents_outline = extract_contents_outline_from_markdown(md_text)
        if contents_outline:
            md_text = apply_contents_heading_levels(md_text, contents_outline)
        else:
            md_text = apply_visual_heading_levels(
                md_text,
                pdf_path,
                page_numbers,
                style_cache=context.style_cache,
            )

    md_text = remove_redundant_page_title_headings(md_text)
    md_text = clean_markdown_tables(md_text)
    md_text = fix_definition_bullets(md_text)
    md_text = normalize_prose_lines(md_text)
    md_text = split_option_bullet_runs(md_text)
    md_text = split_inline_bullet_runs(md_text)
    md_text = dedupe_adjacent_bullets(md_text)
    md_text = strip_contents_sections(md_text)
    md_text = merge_adjacent_fenced_blocks(md_text)
    md_text = make_image_refs_relative(
        md_text,
        images_dir,
        output_path.parent,
        source_images_dir=source_images_dir,
    )
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
        "--auto-ocr",
        action="store_true",
        help="Auto-enable OCR only when selected pages are image-only",
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
        context = ConversionContext(
            pdf_path=pdf_path,
            page_numbers=page_numbers,
            with_text=with_text,
            without_text=without_text,
        )
        ocr_resolution = resolve_ocr_resolution(
            force_ocr_requested=args.ocr,
            auto_ocr_requested=args.auto_ocr,
            image_only_pages=without_text,
            engine=args.ocr_engine,
        )

        if ocr_resolution.enabled and ocr_resolution.backend is None:
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
        print(f"  Auto OCR:      {args.auto_ocr}")
        print(f"  OCR active:    {ocr_resolution.enabled}")
        print(f"  OCR backend:   {ocr_resolution.backend or 'disabled'}")

        start_time = time.time()
        print("\nConverting...", flush=True)

        images_dir.mkdir(parents=True, exist_ok=True)

        try:
            md_text, extracted_images_dir = extract_markdown(
                context=context,
                force_ocr=ocr_resolution.force_ocr,
                backend=ocr_resolution.backend,
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
        md_text = cleanup_markdown(
            md_text,
            context,
            images_dir,
            output_path,
            source_images_dir=extracted_images_dir,
        )
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
