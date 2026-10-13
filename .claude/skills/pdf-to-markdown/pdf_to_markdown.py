#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pymupdf4llm",
#   "ocrmac; platform_system == 'Darwin'",
#   "rapidocr_onnxruntime; platform_system != 'Darwin'",
# ]
# ///
"""CLI entry point for the PDF-to-Markdown converter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from converter.convert import run_cli


def build_parser() -> argparse.ArgumentParser:
    """Build the converter CLI parser."""
    parser = argparse.ArgumentParser(
        description="Convert PDF to Markdown using PyMuPDF4LLM with optional OCR fallback."
    )
    parser.add_argument("pdf_path", help="Path to a PDF file or folder containing PDFs")
    parser.add_argument(
        "-o",
        "--output",
        help="Output .md file path (default: next to the source PDF)",
    )
    parser.add_argument(
        "--pages",
        help="Pages to convert, 1-based: a range like '1-50' or a list like '1-3,7,9-10' (default: all)",
    )
    parser.add_argument(
        "--page-separators",
        action="store_true",
        help="Start every page with a '{N}' + 48-dash marker line (N = PDF page number) so "
        "consumers can map text back to pages; empty pages keep their marker",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Do not extract images; the Markdown keeps text only",
    )
    parser.add_argument(
        "--hidden-text",
        choices=["text", "ocr", "off"],
        default="text",
        help="Pages whose text PyMuPDF4LLM drops (tiny scanner OCR layers): keep the PDF "
        "text layer (text, default), OCR the page (ocr), or leave it empty (off)",
    )
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
    parser.add_argument(
        "--skip-heading-pipeline",
        action="store_true",
        help="Bypass heading reconstruction and heading-specific cleanup",
    )
    parser.add_argument(
        "--skip-text-cleaning",
        action="store_true",
        help="Bypass prose/table/bullet cleanup while keeping heading reconstruction",
    )
    parser.add_argument(
        "--skip-all-cleanup",
        action="store_true",
        help="Bypass all optional post-processing and keep near-raw extracted Markdown",
    )
    return parser


def main() -> None:
    """Parse CLI arguments and run the converter."""
    parser = build_parser()
    raise SystemExit(run_cli(parser.parse_args()))


if __name__ == "__main__":
    main()
