"""Layout and region recovery helpers for the PDF-to-Markdown converter."""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from .document import get_document
from .models import Region
from .text import normalize_inline_spacing, normalize_whitespace

LINE_VERTICAL_MARGIN = 1.0
LINE_BOX_OVERLAP_MIN = 0.35
ROW_CLUSTER_Y_TOLERANCE = 5.0
WORD_GAP_NEW_CELL_MIN = 14.0
INDENT_STEP = 10.0
STRUCTURED_SHORT_ROW_MAX = 72
STRUCTURED_INDENT_SPAN_MIN = 12.0
STRUCTURED_AVG_WORDS_MAX = 8.0
DEFINITION_LEFT_ALIGN_TOLERANCE = 16.0
DEFINITION_RIGHT_ALIGN_TOLERANCE = 24.0
DEFINITION_RIGHT_CONTINUATION_TOLERANCE = 12.0
CODE_RENDER_CHARS_PER_UNIT = 5.5
TABLE_GROUP_RIGHT_COLUMN_MIN_GAP = 60.0
TABLE_GROUP_VERTICAL_OVERLAP_MIN = 0.7
PREFORMATTED_MAX_SENTENCE_LIKE_ROWS = 1
PREFORMATTED_AVG_WORDS_MAX = 6.0
PREFORMATTED_SHORT_LAYOUT_AVG_WORDS_MAX = 5.0
REGION_GROUP_VERTICAL_GAP_MAX = 28.0
REGION_GROUP_HORIZONTAL_OVERLAP_MIN = 0.6
REGION_GROUP_LEFT_EDGE_DELTA_MAX = 28.0
REGION_GROUP_RIGHT_EDGE_DELTA_MAX = 40.0


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

    doc = get_document(pdf_path)
    page = doc.load_page(page_no - 1)
    words = page.get_text("words", sort=True)

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
                "words": [{"x0": entry[0], "x1": entry[2], "text": entry[4]} for entry in entries],
            }
        )

    xml_cache[cache_key] = lines
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
    """Return True when a recovered line meaningfully overlaps a page box."""
    x0, y0, x1, y1 = box
    line_x0 = float(line["x0"])
    line_y0 = float(line["y0"])
    line_x1 = float(line["x1"])
    line_y1 = float(line["y1"])

    line_center_y = (line_y0 + line_y1) / 2.0
    if line_center_y < y0 - LINE_VERTICAL_MARGIN or line_center_y > y1 + LINE_VERTICAL_MARGIN:
        return False

    overlap_x = max(0.0, min(x1, line_x1) - max(x0, line_x0))
    line_width = max(1.0, line_x1 - line_x0)
    return overlap_x / line_width >= LINE_BOX_OVERLAP_MIN


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
    y_tolerance: float = ROW_CLUSTER_Y_TOLERANCE,
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
                    if gap > WORD_GAP_NEW_CELL_MIN:
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


def indent_levels(rows: list[list[dict[str, object]]], step: float = INDENT_STEP) -> set[int]:
    """Estimate distinct indentation levels within a block."""
    base_x = block_base_x(rows)
    return {max(0, int(round((float(row[0]["x0"]) - base_x) / step))) for row in rows if row}


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
    short_rows = sum(1 for text in texts if len(text) <= STRUCTURED_SHORT_ROW_MAX)
    proseish_rows = sum(1 for text in texts if len(text.split()) >= 10 and text.endswith((".", "!", "?")))
    punctuation_rows = sum(1 for text in texts if re.search(r"[{}[\]<>:=;/\\|()]", text))
    avg_words = region_avg_words(region)
    indent_count = len(indent_levels(region.rows))
    multi_col = max(len(row) for row in region.rows) >= 2
    indent_span = max(leading_x_positions(region.rows)) - min(leading_x_positions(region.rows))

    return (
        len(region.rows) >= 2
        and short_rows >= max(2, len(texts) - 1)
        and proseish_rows <= 1
        and avg_words <= STRUCTURED_AVG_WORDS_MAX
        and (multi_col or indent_count >= 2 or indent_span >= STRUCTURED_INDENT_SPAN_MIN or punctuation_rows >= 3)
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
        max(left_x_positions) - min(left_x_positions) <= DEFINITION_LEFT_ALIGN_TOLERANCE
        and max(right_x_positions) - min(right_x_positions) <= DEFINITION_RIGHT_ALIGN_TOLERANCE
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
        elif len(row) == 1 and table_rows and float(row[0]["x0"]) >= right_anchor - DEFINITION_RIGHT_CONTINUATION_TOLERANCE:
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
    punctuation_rows = sum(1 for text in texts if re.search(r"[{}[\]<>:=;/\\|()]", text))
    delimiter_rows = sum(1 for text in texts if re.search(r"[_./\\-]", text))
    short_rows = sum(1 for text in texts if len(text) <= STRUCTURED_SHORT_ROW_MAX)
    proseish_rows = sum(1 for text in texts if len(text.split()) >= 10 and text.endswith((".", "!", "?")))
    avg_words = sum(len(text.split()) for text in texts) / max(1, len(texts))
    sentence_like = sum(text.endswith((".", "!", "?")) for text in texts)
    aligned_layout = region_is_structured(region)
    snippet_norm = normalize_whitespace(region.snippet)
    rows_norm = normalize_whitespace("\n".join(normalized_texts))

    if not aligned_layout:
        return False
    if (
        len(region.rows) >= 3
        and sentence_like <= PREFORMATTED_MAX_SENTENCE_LIKE_ROWS
        and avg_words <= PREFORMATTED_AVG_WORDS_MAX
        and (punctuation_rows >= 1 or delimiter_rows >= 2)
    ):
        return True
    if (
        len(region.rows) >= 4
        and short_rows >= len(region.rows) - 1
        and proseish_rows == 0
        and avg_words <= PREFORMATTED_SHORT_LAYOUT_AVG_WORDS_MAX
    ):
        return True
    return (
        punctuation_rows >= 1
        and proseish_rows == 0
        and avg_words <= PREFORMATTED_AVG_WORDS_MAX
        and snippet_norm == rows_norm
    )


def render_layout_code_block(rows: list[list[dict[str, object]]]) -> str:
    """Render layout rows as an aligned fenced code block."""
    base_x = block_base_x(rows)
    chars_per_unit = CODE_RENDER_CHARS_PER_UNIT
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


def render_structured_box(region: Region) -> tuple[str, str] | None:
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
            if bbox[0] < left_x1 + TABLE_GROUP_RIGHT_COLUMN_MIN_GAP:
                continue
            if overlap_ratio(left_y0, left_y1, bbox[1], bbox[3]) < TABLE_GROUP_VERTICAL_OVERLAP_MIN:
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
        and vertical_gap <= REGION_GROUP_VERTICAL_GAP_MAX
        and x_overlap >= REGION_GROUP_HORIZONTAL_OVERLAP_MIN
        and left_edge_delta <= REGION_GROUP_LEFT_EDGE_DELTA_MAX
        and right_edge_delta <= REGION_GROUP_RIGHT_EDGE_DELTA_MAX
        and region_avg_words(left) <= STRUCTURED_AVG_WORDS_MAX
        and region_avg_words(right) <= STRUCTURED_AVG_WORDS_MAX
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
            if next_idx >= len(page_boxes) or next_idx in consumed or next_idx not in text_regions:
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
        if idx in consumed or box_info.get("class") != "text":
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
        if merged and kind == "code" and merged[-1][2] == "code" and not page_text[merged[-1][1] : start].strip():
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
