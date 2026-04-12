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
    return parser


def main() -> None:
    """Parse CLI arguments and run the converter."""
    parser = build_parser()
    raise SystemExit(run_cli(parser.parse_args()))


if __name__ == "__main__":
    main()
