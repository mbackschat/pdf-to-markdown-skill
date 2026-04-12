"""Heading and contents reconstruction helpers for the PDF-to-Markdown converter."""

from __future__ import annotations

import re
from pathlib import Path

from .models import ConversionContext, MarkdownHeading, OutlineEntry
from .text import (
    PAGE_MARKER_RE,
    looks_like_contents_heading,
    normalize_inline_spacing,
    sanitize_contents_entry,
    slugify_heading,
    strip_markdown_inline,
)


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


def extract_pdf_outline(pdf_path: Path, page_numbers: list[int] | None = None) -> list[OutlineEntry]:
    """Read the PDF outline/bookmark tree, optionally filtered to selected pages."""
    import pymupdf

    selected_pages = {page + 1 for page in page_numbers} if page_numbers is not None else None
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


def heading_match_keys(text: str) -> list[str]:
    """Generate matching keys for headings and TOC entries."""
    text = sanitize_contents_entry(text)
    stripped_number = re.sub(r"^(?:[A-Z]\.)?\d+(?:\.\d+)*[:.)-]?\s*", "", text)
    stripped_chapter = re.sub(
        r"^(?:chapter|appendix)\s+[A-Z0-9]+[:.)-]?\s*",
        "",
        stripped_number,
        flags=re.IGNORECASE,
    )
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

    selected_pages = (
        [page + 1 for page in page_numbers]
        if page_numbers is not None
        else list(range(1, total_pages + 1))
    )
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
            if not title or looks_like_contents_heading(title):
                continue

            if not has_leader and page == 0:
                aligned_with_existing = bool(page_entries) and any(
                    abs(float(line["x0"]) - float(entry["x0"])) <= 12 for entry in page_entries
                )
                if not (aligned_with_existing and looks_like_toc_title_only_line(title)):
                    if page_entries:
                        break
                    continue

            page_entries.append({"x0": float(line["x0"]), "title": title, "page": page})

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

    selected_pages = (
        [page + 1 for page in page_numbers]
        if page_numbers is not None
        else list(range(1, page_count + 1))
    )
    if style_cache is None:
        style_cache = {}
    source_lines: list[dict[str, float | str]] = []

    for page_no in selected_pages:
        for line in extract_page_style_lines(pdf_path, page_no, style_cache):
            token = normalized_heading_token(str(line["text"]))
            if not token:
                continue
            source_lines.append({**line, "page_no": page_no, "token": token})

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


def promote_structured_plaintext_headings(md_text: str) -> str:
    """Promote standalone numbered/chapter plaintext lines to headings conservatively."""
    lines = md_text.splitlines()
    in_code = False
    heading_re = re.compile(r"^(#{1,6})\s+")

    def eligible(text: str) -> bool:
        stripped = sanitize_contents_entry(strip_markdown_inline(text))
        if not stripped or len(stripped) > 120:
            return False
        if stripped.endswith((".", "!", "?")):
            return False
        return bool(
            re.match(r"^(chapter|appendix)\b", stripped, re.IGNORECASE)
            or re.match(r"^(?:[A-Z]\.)?\d+(?:\.\d+)+\b", stripped)
        )

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped or stripped.startswith("|") or heading_re.match(stripped):
            continue
        if idx > 0 and lines[idx - 1].strip():
            continue
        if idx + 1 < len(lines) and lines[idx + 1].strip():
            continue
        if not eligible(stripped):
            continue

        rank = infer_heading_rank(stripped, 1)
        level = min(6, 2 + max(0, rank - 1))
        lines[idx] = f"{'#' * level} {stripped}"

    return "\n".join(lines)


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

    size_to_level = {int(round(size)): min(4, 2 + idx) for idx, size in enumerate(size_buckets)}
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


def apply_contents_heading_levels(md_text: str, contents_outline: list[OutlineEntry]) -> str:
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
        inferred_md_level = min(
            6, 2 + max(0, infer_heading_rank(heading.text, heading.original_level) - 1)
        )

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
        inferred_md_level = min(
            6, base_md_level + max(0, infer_heading_rank(text, heading.original_level) - 1)
        )

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
