"""Conversion orchestration helpers for the PDF-to-Markdown converter."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import cleanup
from .document import detect_text_pages, hidden_text_pages, page_plain_text
from .models import ConversionContext
from .ocr import get_ocr_function, map_lang_codes, pick_ocr_backend, resolve_ocr_resolution
from .page_types import page_is_contents
from .regions import restore_code_blocks_in_chunk
from .text import sanitize_stem

PAGE_SEPARATOR_DASHES = 48
HIDDEN_TEXT_MODES = ("text", "ocr", "off")


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


def parse_page_spec(spec: str, page_count: int) -> list[int]:
    """Parse '1-3,7,9-10' (1-based, inclusive) into sorted unique 0-based page numbers."""
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        start, end = parse_page_range(part)
        if start < 1 or end < start or end > page_count:
            raise ValueError(
                f"Invalid page range {start}-{end} for document with {page_count} pages"
            )
        pages.update(range(start - 1, end))
    if not pages:
        raise ValueError(f"Invalid page specification: {spec!r}")
    return sorted(pages)


def describe_pages(page_numbers: list[int] | None) -> str:
    """Render 0-based page numbers as 1-based ranges, e.g. '1-3, 7, 9-10'."""
    if page_numbers is None:
        return "all"
    ranges: list[str] = []
    pages = sorted(set(page_numbers))
    start = prev = pages[0]
    for page in pages[1:] + [None]:
        if page is not None and page == prev + 1:
            prev = page
            continue
        ranges.append(f"{start + 1}-{prev + 1}" if start != prev else f"{start + 1}")
        if page is not None:
            start = prev = page
    return ", ".join(ranges)


def page_separator(page_no: int) -> str:
    """Marker-style page break: ``{N}`` plus 48 dashes, N being the 1-based PDF page."""
    return "{" + str(page_no) + "}" + "-" * PAGE_SEPARATOR_DASHES


def join_page_texts(page_texts: list[tuple[int, str]], *, page_separators: bool) -> str:
    """Join per-page Markdown.

    With separators every selected page gets a marker on its own line, even a
    page that produced no text, so consumers can map every page back.
    """
    if not page_separators:
        return "\n\n".join(text for _, text in page_texts if text).strip() + "\n"
    parts: list[str] = []
    for page_no, text in page_texts:
        parts.append(page_separator(page_no) + "\n\n")
        if text:
            parts.append(text.rstrip() + "\n\n")
    return "".join(parts)


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


def recover_hidden_text(
    context: ConversionContext,
    by_page: dict[int, dict],
    *,
    mode: str,
    backend: str | None,
    ocr_engine: str,
    langs: list[str],
) -> set[int]:
    """Refill pages whose PyMuPDF4LLM chunk lost the page's text layer.

    ``text`` keeps the PDF's own text layer (exact, no OCR run); ``ocr`` renders
    the page and OCRs it; ``off`` leaves the chunk as extracted. Returns the
    pages that were refilled.
    """
    if mode not in HIDDEN_TEXT_MODES:
        raise ValueError(f"Unknown hidden-text mode: {mode!r}")
    hidden = hidden_text_pages(
        context.pdf_path, {page_no: chunk["text"] for page_no, chunk in by_page.items()}
    )
    context.hidden_text_pages = hidden
    if not hidden or mode == "off":
        return set()
    if mode == "text":
        for page_no in hidden:
            by_page[page_no]["text"] = page_plain_text(context.pdf_path, page_no)
        return set(hidden)

    import pymupdf
    import pymupdf4llm

    ocr_backend = backend or pick_ocr_backend(ocr_engine)
    if ocr_backend is None:
        raise RuntimeError(
            f"{len(hidden)} page(s) of {context.pdf_path.name} need OCR to recover their "
            "text layer, but no OCR backend is installed.\n"
            "Available backends for this skill are Apple Vision (ocrmac), RapidOCR, or Tesseract."
        )
    # PyMuPDF4LLM only OCRs pages it sees as image-only, and the tiny text layer
    # keeps confusing its extraction even after OCR text is added. Render each
    # page onto a scratch page that holds nothing but the image, OCR that, and
    # extract from it: the same path PyMuPDF4LLM takes for a scanned page.
    ocr = get_ocr_function(ocr_backend, langs)
    doc = pymupdf.open(str(context.pdf_path))
    try:
        for page_no in hidden:
            page = doc.load_page(page_no - 1)
            pixmap = page.get_pixmap(dpi=300, colorspace=pymupdf.csRGB, alpha=False)
            scratch = pymupdf.open()
            try:
                scratch_page = scratch.new_page(width=page.rect.width, height=page.rect.height)
                scratch_page.insert_image(scratch_page.rect, pixmap=pixmap)
                ocr(scratch_page, dpi=300)
                chunks = pymupdf4llm.to_markdown(
                    scratch,
                    page_chunks=True,
                    write_images=False,
                    use_ocr=False,
                    header=False,
                    footer=False,
                    show_progress=False,
                )
            finally:
                scratch.close()
            if chunks:
                by_page[page_no]["text"] = chunks[0]["text"]
                by_page[page_no]["page_boxes"] = chunks[0]["page_boxes"]
    finally:
        doc.close()
    return set(hidden)


def extract_markdown(
    context: ConversionContext,
    force_ocr: bool,
    backend: str | None,
    langs: list[str],
    images_dir: Path,
    *,
    write_images: bool = True,
    page_separators: bool = False,
    hidden_text: str = "text",
    ocr_engine: str = "auto",
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
            write_images=write_images,
            image_path=str(extraction_images_dir),
            use_ocr=bool(backend),
            force_ocr=force_ocr,
            ocr_language=map_lang_codes(langs, "tesseract"),
            ocr_function=get_ocr_function(backend, langs),
            header=False,
            footer=False,
            show_progress=False,
        )
        by_page = {int(chunk["metadata"]["page_number"]): chunk for chunk in chunks}

        # Forced OCR already replaced every page's text; otherwise look for pages
        # whose text layer PyMuPDF4LLM dropped (tiny scanner OCR fonts).
        recovered: set[int] = set()
        if not force_ocr:
            recovered = recover_hidden_text(
                context,
                by_page,
                mode=hidden_text,
                backend=backend,
                ocr_engine=ocr_engine,
                langs=langs,
            )

        page_texts: list[tuple[int, str]] = []
        for page_no in sorted(by_page):
            chunk = by_page[page_no]
            page_text = chunk["text"]
            if page_no not in recovered:
                page_text = restore_code_blocks_in_chunk(
                    page_text,
                    chunk["page_boxes"],
                    pdf_path,
                    page_no,
                    context.geometry_cache,
                )
            if page_is_contents(context, page_no):
                page_text = ""
            page_texts.append((page_no, page_text.strip()))

        if write_images:
            move_extracted_images(extraction_images_dir, images_dir)

        md_text = join_page_texts(page_texts, page_separators=page_separators)
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
    page_separators: bool = False,
    write_images: bool = True,
    hidden_text: str = "text",
) -> None:
    """Convert one PDF and write its Markdown output next to it or to an explicit path."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    try:
        page_numbers = parse_page_spec(pages_arg, doc.page_count) if pages_arg else None
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
    print(f"  Images: {images_dir}/" if write_images else "  Images: disabled (--no-images)")
    if page_numbers is not None:
        print(f"  Pages:  {describe_pages(page_numbers)}")
    print(f"  Text pages:    {with_text}")
    print(f"  Image-only pages: {without_text}")
    print(f"  OCR requested: {ocr_requested}")
    print(f"  Auto OCR:      {auto_ocr_requested}")
    print(f"  OCR active:    {ocr_resolution.enabled}")
    print(f"  OCR backend:   {ocr_resolution.backend or 'disabled'}")
    print(f"  Hidden text:   {hidden_text}")
    print(f"  Page markers:  {page_separators}")
    print(f"  Skip headings: {skip_heading_pipeline}")
    print(f"  Skip text:     {skip_text_cleanup}")
    print(f"  Raw cleanup:   {skip_all_cleanup}")

    start_time = time.time()
    print("\nConverting...", flush=True)

    if write_images:
        reset_images_dir(images_dir)

    md_text, extracted_images_dir = extract_markdown(
        context=context,
        force_ocr=ocr_resolution.force_ocr,
        backend=ocr_resolution.backend,
        langs=langs,
        images_dir=images_dir,
        write_images=write_images,
        page_separators=page_separators,
        hidden_text=hidden_text,
        ocr_engine=ocr_engine,
    )
    if context.hidden_text_pages:
        action = {"text": "kept PDF text layer", "ocr": "OCR'd", "off": "left empty"}[hidden_text]
        pages_text = describe_pages([page_no - 1 for page_no in context.hidden_text_pages])
        print(f"  Hidden-text pages ({action}): {pages_text}")

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

    image_count = len(list(images_dir.glob("*"))) if images_dir.is_dir() else 0
    if image_count == 0 and images_dir.is_dir():
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
                page_separators=getattr(args, "page_separators", False),
                write_images=not getattr(args, "no_images", False),
                hidden_text=getattr(args, "hidden_text", "text"),
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
