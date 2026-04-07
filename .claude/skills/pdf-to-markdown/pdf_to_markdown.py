#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["docling[ocrmac,easyocr]"]
# ///
"""Convert PDF files to Markdown using Docling with OCR support.

Optimized for technical documentation: datasheets, hardware manuals,
programming guides with tables, diagrams, and code listings.
"""

import argparse
import logging
import re
import sys
import threading
import time
from collections import Counter
from pathlib import Path


def parse_page_range(page_range_str: str) -> tuple[int, int]:
    """Parse a page range string like '1-50' into a (start, end) tuple."""
    parts = page_range_str.split("-")
    if len(parts) == 1:
        page = int(parts[0])
        return (page, page)
    elif len(parts) == 2:
        return (int(parts[0]), int(parts[1]))
    else:
        raise ValueError(f"Invalid page range: {page_range_str}")


def build_ocr_options(engine: str, langs: list[str], force_ocr: bool):
    """Build OCR options based on the selected engine."""
    if engine == "auto":
        engine = "mac" if sys.platform == "darwin" else "easyocr"

    if engine == "mac":
        try:
            from docling.datamodel.pipeline_options import OcrMacOptions

            mac_lang_map = {
                "en": "en-US",
                "de": "de-DE",
                "fr": "fr-FR",
                "es": "es-ES",
                "it": "it-IT",
                "pt": "pt-BR",
                "nl": "nl-NL",
                "ja": "ja-JP",
                "zh": "zh-Hans",
            }
            mac_langs = [mac_lang_map.get(l, l) for l in langs]
            print(f"  OCR engine: macOS Vision ({', '.join(mac_langs)})")
            return OcrMacOptions(lang=mac_langs, force_full_page_ocr=force_ocr)
        except ImportError:
            print("  macOS Vision not available, falling back to EasyOCR...")
            engine = "easyocr"

    if engine == "easyocr":
        try:
            from docling.datamodel.pipeline_options import EasyOcrOptions

            print(f"  OCR engine: EasyOCR ({', '.join(langs)})")
            return EasyOcrOptions(
                lang=langs,
                force_full_page_ocr=force_ocr,
                use_gpu=True,
                confidence_threshold=0.3,
            )
        except ImportError:
            print("  EasyOCR not available. Install with:")
            print('    uv run --with "docling[easyocr]" python3 ...')
            sys.exit(1)

    if engine == "tesseract":
        try:
            from docling.datamodel.pipeline_options import TesseractOcrOptions

            tess_lang_map = {"en": "eng", "de": "deu", "fr": "fra", "es": "spa"}
            tess_langs = [tess_lang_map.get(l, l) for l in langs]
            print(f"  OCR engine: Tesseract ({', '.join(tess_langs)})")
            return TesseractOcrOptions(
                lang=tess_langs, force_full_page_ocr=force_ocr
            )
        except ImportError:
            print("  Tesseract not available. Install with:")
            print('    uv run --with "docling[tesserocr]" python3 ...')
            sys.exit(1)

    print(f"  Unknown OCR engine: {engine}")
    sys.exit(1)


def fix_headings(md_text: str) -> str:
    """Fix flat heading hierarchy and remove running headers.

    1. Detect running headers (headings repeated 3+ times) and remove them.
    2. If a TOC is found, use it to identify main sections (##) and demote
       all other headings to subsections (###).
    """
    lines = md_text.split("\n")

    # Step 1: Find and remove running headers.
    # A running header is a heading that appears identically 3+ times.
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$")
    heading_texts = []
    for line in lines:
        m = heading_re.match(line)
        if m:
            heading_texts.append(m.group(2).strip())

    counts = Counter(heading_texts)
    running_headers = {text for text, count in counts.items() if count >= 3}

    if running_headers:
        # Remove running header lines and any immediately following blank line
        cleaned = []
        skip_next_blank = False
        for line in lines:
            m = heading_re.match(line)
            if m and m.group(2).strip() in running_headers:
                skip_next_blank = True
                continue
            if skip_next_blank and line.strip() == "":
                skip_next_blank = False
                continue
            skip_next_blank = False
            cleaned.append(line)
        lines = cleaned

    # Step 2: Extract TOC entries to identify main sections.
    # Look for a TOC section: a heading containing "Table of Content" or
    # "Inhaltsverzeichnis" followed by table rows or list items.
    toc_entries: set[str] = set()
    in_toc = False
    toc_heading_idx = None
    for i, line in enumerate(lines):
        m = heading_re.match(line)
        if m:
            text = m.group(2).strip()
            if re.search(r"(?i)table of content|inhaltsverzeichnis|contents", text):
                in_toc = True
                toc_heading_idx = i
                continue
            elif in_toc:
                # Next heading after TOC block ends it
                in_toc = False
        if in_toc:
            # Extract TOC entry text from table rows: | Entry text... | page |
            toc_match = re.match(r"\|\s*(.+?)\s*\|", line)
            if toc_match:
                entry = toc_match.group(1).strip()
                # Clean dots, page numbers, trailing punctuation
                entry = re.sub(r"[.\s·…]+\d*\s*$", "", entry).strip()
                # Also strip leading chapter numbers like "2.1" or "2.1.1"
                entry_no_num = re.sub(r"^\d+(\.\d+)*\.?\s*", "", entry).strip()
                if entry and len(entry) > 1:
                    toc_entries.add(entry)
                if entry_no_num and len(entry_no_num) > 1:
                    toc_entries.add(entry_no_num)
            # Also handle list-style TOC: - Entry text
            list_match = re.match(r"[-*]\s+(.+)", line)
            if list_match:
                entry = list_match.group(1).strip()
                entry = re.sub(r"[.\s·…]+\d*\s*$", "", entry).strip()
                if entry and len(entry) > 1:
                    toc_entries.add(entry)

    # Step 3: Assign heading levels based on TOC membership.
    # TOC entries -> ## (main sections), others -> ### (subsections).
    if toc_entries:
        result = []
        for line in lines:
            m = heading_re.match(line)
            if m:
                text = m.group(2).strip()
                # Normalize for matching: strip trailing colons/dots
                text_clean = re.sub(r"[.:]+$", "", text).strip()
                if text_clean in toc_entries or text in toc_entries:
                    result.append(f"## {text}")
                else:
                    result.append(f"### {text}")
            else:
                result.append(line)
        lines = result

    return "\n".join(lines)


class ProgressTracker(logging.Handler):
    """Track Docling progress by intercepting log messages."""

    def __init__(self):
        super().__init__()
        self.current_page = 0
        self.current_stage = ""

    def emit(self, record):
        msg = record.getMessage()
        # Docling logs page processing messages
        if "page" in msg.lower():
            self.current_stage = msg.strip()


def print_progress(start_time: float, stop_event: threading.Event, tracker: ProgressTracker):
    """Print elapsed time periodically while conversion is running."""
    while not stop_event.wait(10.0):
        elapsed = time.time() - start_time
        stage = f" - {tracker.current_stage}" if tracker.current_stage else ""
        print(f"  ... {elapsed:.0f}s elapsed{stage}", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Convert PDF to Markdown using Docling with OCR support."
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
        help="Enable forced full-page OCR (for scanned PDFs without selectable text)",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=["auto", "mac", "easyocr", "tesseract"],
        default="auto",
        help="OCR engine to use (default: auto = mac on macOS, easyocr elsewhere)",
    )
    parser.add_argument(
        "--langs",
        default="en",
        help="Comma-separated language codes (default: en)",
    )
    parser.add_argument(
        "--threads", type=int, default=4, help="Number of CPU threads (default: 4)"
    )
    args = parser.parse_args()

    # Validate input — accept a single PDF or a folder of PDFs
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
        if not input_path.suffix.lower() == ".pdf":
            print(f"Error: Not a PDF file: {input_path}")
            sys.exit(1)
        pdf_files = [input_path]

    # Parse options
    langs = [l.strip() for l in args.langs.split(",")]
    force_ocr = args.ocr
    page_range = None
    if args.pages:
        page_range = parse_page_range(args.pages)

    # Build OCR options (skip entirely for digital PDFs to avoid downloading OCR dependencies)
    if force_ocr:
        ocr_options = build_ocr_options(args.ocr_engine, langs, force_ocr)
    else:
        ocr_options = None
        print("  OCR disabled (digital PDF mode)")

    # Set up progress tracking via Docling's logging
    tracker = ProgressTracker()
    tracker.setLevel(logging.DEBUG)
    docling_logger = logging.getLogger("docling")
    docling_logger.addHandler(tracker)
    docling_logger.setLevel(logging.INFO)

    # Import Docling components
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorOptions,
        PdfPipelineOptions,
        TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import ImageRefMode
    from urllib.parse import quote

    # Configure pipeline
    pipeline_options = PdfPipelineOptions(
        do_ocr=force_ocr,
        **({"ocr_options": ocr_options} if ocr_options else {}),
        do_table_structure=True,
        table_structure_options=TableStructureOptions(do_cell_matching=True),
        generate_picture_images=True,
        accelerator_options=AcceleratorOptions(
            num_threads=args.threads,
            device="auto",
        ),
    )

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    batch_start = time.time()

    for file_idx, pdf_path in enumerate(pdf_files, 1):
        # Set up output paths for this file — default: next to the source PDF
        stem = re.sub(r"[^\w-]", "_", pdf_path.stem).strip("_")
        stem = re.sub(r"_+", "_", stem)
        if args.output and len(pdf_files) == 1:
            output_path = Path(args.output).resolve()
        else:
            output_path = pdf_path.parent / f"{stem}.md"

        images_dir = output_path.parent / f"{stem}_images"

        # Print configuration
        file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
        if len(pdf_files) > 1:
            print(f"\n[{file_idx}/{len(pdf_files)}] {pdf_path.name}")
        print(f"PDF to Markdown Converter (Docling)")
        print(f"{'=' * 40}")
        print(f"  Input:  {pdf_path}")
        print(f"  Size:   {file_size_mb:.1f} MB")
        print(f"  Output: {output_path}")
        print(f"  Images: {images_dir}/")
        if page_range:
            print(f"  Pages:  {page_range[0]}-{page_range[1]}")
        print(f"  Force OCR: {force_ocr}")

        # Convert with progress indicator
        print(f"\nConverting...", flush=True)
        start_time = time.time()

        stop_event = threading.Event()
        progress_thread = threading.Thread(
            target=print_progress,
            args=(start_time, stop_event, tracker),
            daemon=True,
        )
        progress_thread.start()

        convert_kwargs = {}
        if page_range:
            convert_kwargs["page_range"] = page_range

        try:
            result = converter.convert(str(pdf_path), **convert_kwargs)
        except Exception as e:
            stop_event.set()
            print(f"\nError converting {pdf_path.name}: {e}")
            if len(pdf_files) > 1:
                print("  Skipping this file...")
                continue
            sys.exit(1)

        stop_event.set()
        progress_thread.join(timeout=1)

        elapsed = time.time() - start_time
        print(f"  Conversion completed in {elapsed:.1f}s")

        # Export to markdown with images
        print(f"  Exporting markdown and images...")

        images_dir.mkdir(parents=True, exist_ok=True)

        result.document.save_as_markdown(
            filename=output_path,
            artifacts_dir=images_dir,
            image_mode=ImageRefMode.REFERENCED,
        )

        # Count exported images and clean up empty dir
        image_count = len(list(images_dir.glob("*")))
        if image_count == 0:
            images_dir.rmdir()

        # Post-process: fix heading levels and remove running headers
        print(f"  Post-processing headings...")
        md_text = output_path.read_text(encoding="utf-8")
        md_text = fix_headings(md_text)

        # Make image references relative and URL-encode paths with special chars
        if image_count > 0:
            abs_images = str(images_dir)
            rel_images = str(images_dir.relative_to(output_path.parent))
            md_text = md_text.replace(abs_images, rel_images)

            def encode_image_path(m):
                alt = m.group(1)
                path = m.group(2)
                encoded = quote(path, safe="/")
                return f"![{alt}]({encoded})"

            md_text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", encode_image_path, md_text)

        output_path.write_text(md_text, encoding="utf-8")

        # Summary for this file
        output_size_kb = output_path.stat().st_size / 1024
        total_elapsed = time.time() - start_time
        print(f"\nDone!")
        print(f"  Markdown: {output_path} ({output_size_kb:.1f} KB)")
        if image_count > 0:
            print(f"  Images:   {image_count} files in {images_dir}/")
        else:
            print(f"  Images:   none extracted")
        print(f"  Time:     {total_elapsed:.1f}s")

    # Batch summary
    if len(pdf_files) > 1:
        batch_elapsed = time.time() - batch_start
        print(f"\n{'=' * 40}")
        print(f"Batch complete: {len(pdf_files)} files in {batch_elapsed:.1f}s")


if __name__ == "__main__":
    main()
