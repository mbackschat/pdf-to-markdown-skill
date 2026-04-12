"""Cleanup helpers for visible contents sections in generated Markdown."""

from __future__ import annotations

import re

from .headings import extract_contents_entries_from_text
from .text import (
    PAGE_MARKER_RE,
    looks_like_contents_heading,
    normalize_inline_spacing,
    strip_markdown_inline,
)


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

            pairs = (
                zip(text_parts, page_parts)
                if page_parts and len(page_parts) == len(text_parts)
                else ((text, "") for text in text_parts)
            )
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
