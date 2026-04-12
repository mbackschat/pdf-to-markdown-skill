"""Shared text helpers for the PDF-to-Markdown converter."""

from __future__ import annotations

import re


DECIMAL_PAGE_RE = r"[A-Za-z]?\d+(?:[.-]\d+)*"
ROMAN_PAGE_RE = r"M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{1,3})"
PAGE_MARKER_RE = rf"(?:{DECIMAL_PAGE_RE}|{ROMAN_PAGE_RE})"


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
