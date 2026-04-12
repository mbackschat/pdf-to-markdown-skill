"""Markdown cleanup helpers for the PDF-to-Markdown converter."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from .contents_cleanup import convert_contents_tables_to_lists, expand_contents_paragraphs
from .headings import (
    apply_contents_heading_levels,
    apply_outline_heading_levels,
    apply_visual_heading_levels,
    match_headings_to_source_lines,
    promote_structured_plaintext_headings,
    extract_contents_outline_from_markdown,
    extract_contents_outline_from_pdf,
    get_cached_outline,
    extract_markdown_headings,
    strip_contents_sections,
)
from .models import ConversionContext
from .reference_entries import normalize_reference_entry_headings
from .text import (
    cleanup_heading_markup,
    normalize_inline_spacing,
)


def remove_running_headers(
    md_text: str,
    context: ConversionContext | None = None,
) -> str:
    """Remove repeated running headers while leaving heading depth untouched."""
    lines = md_text.split("\n")
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$")

    in_code = False
    heading_occurrences: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = heading_re.match(line)
        if match:
            heading_occurrences.append((idx, match.group(2).strip()))

    counts = __import__("collections").Counter(text for _, text in heading_occurrences)

    source_matches: dict[int, dict[str, float | str]] = {}
    if context is not None:
        headings = extract_markdown_headings(md_text)
        if headings:
            source_matches = match_headings_to_source_lines(
                headings,
                context.pdf_path,
                context.page_numbers,
                style_cache=context.style_cache,
            )

    def is_banner_like(index: int) -> bool:
        if source_matches:
            matched_heading_idx = next(
                (
                    heading_idx
                    for heading_idx, source in source_matches.items()
                    if headings[heading_idx].line_idx == index
                ),
                None,
            )
            if matched_heading_idx is not None:
                source = source_matches[matched_heading_idx]
                y0 = float(source.get("y0", 9999.0))
                size = float(source.get("size", 0.0))
                if y0 <= 65.0 and size <= 15.5:
                    return True

        # Running headers usually sit alone at a page boundary and are immediately
        # followed by the real section heading rather than by body text.
        next_idx = index + 1
        while next_idx < len(lines) and not lines[next_idx].strip():
            next_idx += 1
        if next_idx >= len(lines):
            return False
        return bool(heading_re.match(lines[next_idx]))

    running_headers = {
        text
        for text, count in counts.items()
        if count >= 3
        and all(is_banner_like(idx) for idx, heading_text in heading_occurrences if heading_text == text)
    }
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


def make_image_refs_relative(
    md_text: str,
    images_dir: Path,
    output_dir: Path,
    source_images_dir: Path | None = None,
) -> str:
    """Rewrite extracted image paths to be relative to the Markdown output."""
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
        md_text = pattern.sub(
            lambda m: f"```\n{m.group(1).rstrip()}\n{m.group(2).lstrip()}\n```",
            md_text,
        )
    return md_text


def merge_fenced_block_with_code_bullets(md_text: str) -> str:
    """Merge a fenced code block followed by bulletized inline-code lines."""
    lines = md_text.splitlines()
    result: list[str] = []
    i = 0

    while i < len(lines):
        if lines[i] != "```":
            result.append(lines[i])
            i += 1
            continue

        j = i + 1
        block_lines: list[str] = []
        while j < len(lines) and lines[j] != "```":
            block_lines.append(lines[j])
            j += 1
        if j >= len(lines):
            result.extend(lines[i:])
            break

        bullet_idx = j + 1
        while bullet_idx < len(lines) and not lines[bullet_idx].strip():
            bullet_idx += 1

        code_bullets: list[str] = []
        scan_idx = bullet_idx
        while scan_idx < len(lines):
            stripped = lines[scan_idx].strip()
            if not stripped:
                scan_idx += 1
                continue
            match = re.fullmatch(r"-\s+`(.+?)`", stripped)
            if not match:
                break
            code_bullets.append(match.group(1))
            scan_idx += 1

        if code_bullets:
            result.append("```")
            result.extend(block_lines)
            result.extend(code_bullets)
            result.append("```")
            i = scan_idx
            continue

        result.append(lines[i])
        result.extend(block_lines)
        result.append(lines[j])
        i = j + 1

    return "\n".join(result)


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
            duplicate_pairs = sum(1 for row in rows if len(row) >= 2 and row[0] and row[0] == row[1])
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
    md_text = remove_running_headers(md_text, context)
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

    md_text = promote_structured_plaintext_headings(md_text)
    md_text = remove_redundant_page_title_headings(md_text)
    md_text = clean_markdown_tables(md_text)
    md_text = fix_definition_bullets(md_text)
    md_text = normalize_prose_lines(md_text)
    md_text = split_option_bullet_runs(md_text)
    md_text = split_inline_bullet_runs(md_text)
    md_text = dedupe_adjacent_bullets(md_text)
    md_text = normalize_reference_entry_headings(md_text, context)
    md_text = strip_contents_sections(md_text)
    md_text = merge_fenced_block_with_code_bullets(md_text)
    md_text = merge_adjacent_fenced_blocks(md_text)
    md_text = make_image_refs_relative(
        md_text,
        images_dir,
        output_path.parent,
        source_images_dir=source_images_dir,
    )
    md_text = re.sub(r"\n{3,}", "\n\n", md_text).strip() + "\n"
    return md_text
