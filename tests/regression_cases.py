"""Regression case definitions for real sample PDFs."""

from __future__ import annotations

import os
import re
from pathlib import Path


DEFAULT_SAMPLE_DIR = Path(
    "/Volumes/Learning/Retro Literatur/Atari ST/Developer Books/atari-tos-main/doc/additional_material/pdf"
)


def sample_pdf_dir() -> Path:
    """Return the configured sample PDF directory."""
    configured = os.environ.get("PDF_TO_MARKDOWN_SAMPLE_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_SAMPLE_DIR


def output_path_for(pdf_path: Path) -> Path:
    """Return the default Markdown output path for a PDF."""
    stem = re.sub(r"[^\w-]", "_", pdf_path.stem).strip("_")
    stem = re.sub(r"_+", "_", stem)
    return pdf_path.parent / f"{stem}.md"


def regression_cases() -> list[dict[str, object]]:
    """Return grouped regression cases based on the configured sample PDF root."""
    pdf_dir = sample_pdf_dir()
    return [
        {
            "name": "PureC listings",
            "suite": "listings",
            "pdf": pdf_dir / "PureC_English_Overview-JLG.pdf",
            "args": [],
            "checks": [
                r"## Project \(.PRJ\) Files",
                r"```\n\{ output_file \| \* \}\n\{ \.L \[ <linker_options> \] \}",
            ],
        },
        {
            "name": "cmanship program listing",
            "suite": "listings",
            "pdf": pdf_dir / "cmanship-v1.0.pdf",
            "args": [],
            "checks": [
                r"```[\s\S]*do_froundrec\(\)",
                r"```[\s\S]*for \(color=1; color<7; \+\+color\) \{",
            ],
        },
        {
            "name": "WD1772 visible TOC hierarchy",
            "suite": "headings",
            "pdf": pdf_dir / "WD1772-JLG.pdf",
            "args": [],
            "checks": [
                r"## COMMAND DESCRIPTION",
                r"### COMMAND SUMMARY",
                r"#### Flag Summary",
            ],
        },
    ]
