"""Shared data structures for the PDF-to-Markdown converter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Region:
    """A layout-aware text region recovered from one PDF page area."""

    page_no: int
    source_class: str
    start: int
    stop: int
    bbox: tuple[float, float, float, float]
    snippet: str
    rows: list[list[dict[str, object]]]


@dataclass
class OutlineEntry:
    """A PDF outline/bookmark entry used for heading reconstruction."""

    level: int
    title: str
    page: int
    slug: str | None = None


@dataclass
class MarkdownHeading:
    """A Markdown heading candidate extracted from generated Markdown."""

    line_idx: int
    text: str
    keys: list[str]
    slug: str
    original_level: int


@dataclass
class ConversionContext:
    """Shared document state and caches used across extraction and cleanup."""

    pdf_path: Path
    page_numbers: list[int] | None
    geometry_cache: dict[int, list[dict[str, float | str]]] = field(default_factory=dict)
    style_cache: dict[int, list[dict[str, float | str]]] = field(default_factory=dict)
    outline: list[OutlineEntry] | None = None
    with_text: int = 0
    without_text: int = 0


@dataclass
class OcrResolution:
    """Resolved OCR behavior for one conversion run."""

    enabled: bool
    force_ocr: bool
    auto_ocr: bool
    backend: str | None
