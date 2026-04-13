"""Conversion orchestration helpers for the PDF-to-Markdown converter."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import cleanup
from .document import detect_text_pages
from .models import ConversionContext
from .ocr import get_ocr_function, map_lang_codes, resolve_ocr_resolution
from .page_types import page_is_contents
from .regions import restore_code_blocks_in_chunk
from .text import sanitize_stem


def parse_page_range(page_range_str: str) -> tuple[int, int]:
    """Parse a page range string like '1-50' into a 1-based inclusive tuple."""
    parts = page_range_str.split("-")
    if len(parts) == 1:
        page = int(parts[0])
        return (page, page)
    if len(parts) == 2:
        return (int(parts[0]), int(parts[1]))
    raise ValueError(f"Invalid page range: {page_range_str}")


def build_page_numbers(page_range: tuple[int, int] | None, page_count: int) -> list[int] | None:
    """Convert a 1-based inclusive page range to 0-based page numbers."""
    if page_range is None:
        return None

    start, end = page_range
    if start < 1 or end < start or end > page_count:
        raise ValueError(f"Invalid page range {start}-{end} for document with {page_count} pages")
    return list(range(start - 1, end))


def collect_pdf_files(input_path: Path) -> list[Path]:
    """Resolve a file or folder input into the PDFs to convert."""
    if not input_path.exists():
        raise FileNotFoundError(f"Path not found: {input_path}")

    if input_path.is_dir():
        pdf_files = sorted(input_path.glob("*.pdf"))
        if not pdf_files:
            raise FileNotFoundError(f"No PDF files found in {input_path}")
        return pdf_files

    if input_path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {input_path}")
    return [input_path]


def resolve_output_path(pdf_path: Path, output_arg: str | None, batch_mode: bool) -> tuple[Path, Path]:
    """Return the output markdown path and sibling images directory."""
    stem = sanitize_stem(pdf_path.stem)
    if output_arg and not batch_mode:
        output_path = Path(output_arg).resolve()
    else:
        output_path = pdf_path.parent / f"{stem}.md"
    images_dir = output_path.parent / f"{stem}_images"
    return output_path, images_dir


def reset_images_dir(images_dir: Path) -> None:
    """Prepare an image output directory for a fresh conversion run."""
    if images_dir.exists():
        for child in images_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        images_dir.mkdir(parents=True, exist_ok=True)


def move_extracted_images(extraction_images_dir: Path, images_dir: Path) -> None:
    """Move extracted images into the final output directory, recreating it if needed."""
    images_dir.mkdir(parents=True, exist_ok=True)
    for src in extraction_images_dir.iterdir():
        target = images_dir / src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(src), str(target))


def extract_markdown(
    context: ConversionContext,
    force_ocr: bool,
    backend: str | None,
    langs: list[str],
    images_dir: Path,
) -> tuple[str, Path]:
    """Extract Markdown from a PDF using PyMuPDF4LLM and optional OCR preprocessing."""
    import pymupdf4llm

    pdf_path = context.pdf_path
    page_numbers = context.page_numbers

    with tempfile.TemporaryDirectory(prefix=f"{sanitize_stem(pdf_path.stem)}_images_") as tmp_dir:
        extraction_images_dir = Path(tmp_dir)
        chunks = pymupdf4llm.to_markdown(
            str(pdf_path),
            pages=page_numbers,
            page_chunks=True,
            write_images=True,
            image_path=str(extraction_images_dir),
            use_ocr=bool(backend),
            force_ocr=force_ocr,
            ocr_language=map_lang_codes(langs, "tesseract"),
            ocr_function=get_ocr_function(backend, langs),
            header=False,
            footer=False,
            show_progress=False,
        )

        page_texts: list[str] = []
        for chunk in chunks:
            page_no = int(chunk["metadata"]["page_number"])
            page_text = chunk["text"]
            page_text = restore_code_blocks_in_chunk(
                page_text,
                chunk["page_boxes"],
                pdf_path,
                page_no,
                context.geometry_cache,
            )
            if page_is_contents(context, page_no):
                continue
            page_texts.append(page_text.strip())

        move_extracted_images(extraction_images_dir, images_dir)

        md_text = "\n\n".join(text for text in page_texts if text).strip() + "\n"
        return md_text, extraction_images_dir


def convert_pdf(
    pdf_path: Path,
    *,
    output_arg: str | None,
    pages_arg: str | None,
    ocr_requested: bool,
    auto_ocr_requested: bool,
    ocr_engine: str,
    langs: list[str],
    skip_heading_pipeline: bool,
    skip_text_cleanup: bool,
    skip_all_cleanup: bool,
    batch_mode: bool,
    file_idx: int | None = None,
    total_files: int | None = None,
) -> None:
    """Convert one PDF and write its Markdown output next to it or to an explicit path."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    try:
        page_range = parse_page_range(pages_arg) if pages_arg else None
        page_numbers = build_page_numbers(page_range, doc.page_count)
    finally:
        if not doc.is_closed:
            doc.close()

    with_text, without_text = detect_text_pages(pdf_path, page_numbers)
    context = ConversionContext(
        pdf_path=pdf_path,
        page_numbers=page_numbers,
        with_text=with_text,
        without_text=without_text,
    )
    ocr_resolution = resolve_ocr_resolution(
        force_ocr_requested=ocr_requested,
        auto_ocr_requested=auto_ocr_requested,
        image_only_pages=without_text,
        engine=ocr_engine,
    )

    if ocr_resolution.enabled and ocr_resolution.backend is None:
        raise RuntimeError(
            f"OCR is required for {pdf_path.name}, but no OCR backend is installed.\n"
            "Available backends for this skill are Apple Vision (ocrmac), RapidOCR, or Tesseract."
        )

    output_path, images_dir = resolve_output_path(pdf_path, output_arg, batch_mode)

    if batch_mode and file_idx is not None and total_files is not None:
        print(f"\n[{file_idx}/{total_files}] {pdf_path.name}")
    print("PDF to Markdown Converter (PyMuPDF4LLM)")
    print("=" * 40)
    print(f"  Input:  {pdf_path}")
    print(f"  Output: {output_path}")
    print(f"  Images: {images_dir}/")
    if page_range:
        print(f"  Pages:  {page_range[0]}-{page_range[1]}")
    print(f"  Text pages:    {with_text}")
    print(f"  Image-only pages: {without_text}")
    print(f"  OCR requested: {ocr_requested}")
    print(f"  Auto OCR:      {auto_ocr_requested}")
    print(f"  OCR active:    {ocr_resolution.enabled}")
    print(f"  OCR backend:   {ocr_resolution.backend or 'disabled'}")
    print(f"  Skip headings: {skip_heading_pipeline}")
    print(f"  Skip text:     {skip_text_cleanup}")
    print(f"  Raw cleanup:   {skip_all_cleanup}")

    start_time = time.time()
    print("\nConverting...", flush=True)

    reset_images_dir(images_dir)

    md_text, extracted_images_dir = extract_markdown(
        context=context,
        force_ocr=ocr_resolution.force_ocr,
        backend=ocr_resolution.backend,
        langs=langs,
        images_dir=images_dir,
    )

    print("  Post-processing markdown...")
    md_text = cleanup.cleanup_markdown(
        md_text,
        context,
        images_dir,
        output_path,
        source_images_dir=extracted_images_dir,
        skip_heading_pipeline=skip_heading_pipeline,
        skip_text_cleanup=skip_text_cleanup,
        skip_all_cleanup=skip_all_cleanup,
    )
    output_path.write_text(md_text, encoding="utf-8")

    image_count = len(list(images_dir.glob("*")))
    if image_count == 0:
        images_dir.rmdir()

    elapsed = time.time() - start_time
    output_size_kb = output_path.stat().st_size / 1024
    print("\nDone!")
    print(f"  Markdown: {output_path} ({output_size_kb:.1f} KB)")
    if image_count > 0:
        print(f"  Images:   {image_count} files in {images_dir}/")
    else:
        print("  Images:   none extracted")
    print(f"  Time:     {elapsed:.1f}s")


def run_cli(args) -> int:
    """Run the converter using argparse-style parsed arguments."""
    input_path = Path(args.pdf_path).resolve()

    try:
        pdf_files = collect_pdf_files(input_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    if input_path.is_dir():
        print(f"Found {len(pdf_files)} PDF(s) in {input_path}")
        if args.output:
            print("Warning: --output is ignored in batch mode (one .md per PDF)")

    langs = [lang.strip() for lang in args.langs.split(",") if lang.strip()]
    batch_start = time.time()

    for file_idx, pdf_path in enumerate(pdf_files, 1):
        try:
            convert_pdf(
                pdf_path,
                output_arg=args.output,
                pages_arg=args.pages,
                ocr_requested=args.ocr,
                auto_ocr_requested=args.auto_ocr,
                ocr_engine=args.ocr_engine,
                langs=langs,
                skip_heading_pipeline=args.skip_heading_pipeline,
                skip_text_cleanup=args.skip_text_cleaning,
                skip_all_cleanup=args.skip_all_cleanup,
                batch_mode=len(pdf_files) > 1,
                file_idx=file_idx,
                total_files=len(pdf_files),
            )
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        except Exception as exc:
            print(f"\nError converting {pdf_path.name}: {exc}")
            if len(pdf_files) > 1:
                print("  Skipping this file...")
                continue
            return 1

    if len(pdf_files) > 1:
        batch_elapsed = time.time() - batch_start
        print(f"\n{'=' * 40}")
        print(f"Batch complete: {len(pdf_files)} files in {batch_elapsed:.1f}s")

    return 0
