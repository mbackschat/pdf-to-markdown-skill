# CLAUDE.md

This repository contains a Claude Code skill that converts PDFs to Markdown.

## Current Architecture

- Main extractor: `PyMuPDF4LLM`
- Default mode: digital-first extraction for born-digital PDFs
- OCR mode: opt-in with `--ocr` / `--scan`
- Default OCR backend:
  - macOS: Apple Vision via `ocrmac`
  - other platforms: RapidOCR
  - explicit fallback: Tesseract
- Structured repair model:
  - build layout-aware regions from positioned page text
  - classify regions by structural signals
  - render regions as prose, preformatted blocks, or tables

## Markdown Quality Improvements

The converter includes a substantial post-processing pass in `.claude/skills/pdf-to-markdown/pdf_to_markdown.py` to improve Markdown quality for technical books and manuals.

Current cleanup includes:

- heading cleanup and hierarchy repair
- removal of repeated running headers
- conversion of noisy contents pages into bullet lists
- conversion of matching contents entries into internal Markdown links
- table cleanup for OCR-heavy or malformed tables
- language-agnostic preformatted recovery using page geometry instead of language-specific token checks
- layout-aware reconstruction of structured listings from positioned lines and words
- fallback to PyMuPDF word geometry when `pdftotext -bbox-layout` is unavailable or fails
- merging of adjacent fenced blocks when a logical listing was split only by page/chunk boundaries
- relative image path rewriting
- spacing and punctuation normalization for extracted prose
- better handling of flattened option lists, inline bullet runs, and definition-like bullets

## Structured Listings

One of the main focuses in this repo is preserving preformatted regions from digital PDFs.

- PyMuPDF4LLM can flatten some preformatted regions or split one logical listing across page chunks
- the current script reconstructs listings from layout geometry first, not from language-specific code markers
- when `pdftotext` is installed, it is used as the primary geometry source
- if `pdftotext` fails, PyMuPDF word positions are used as a fallback geometry source
- recovered preformatted blocks are emitted as fenced code blocks
- adjacent fenced blocks are merged when they are separated only by chunk/page boundaries

This notably improves manuals with compiler options, command syntax, project-file formats, and multi-page program listings.

## Contents / TOC Behavior

Contents pages are treated specially:

- page-number-heavy TOCs are rewritten as readable bullets
- PDF page numbers are dropped from the Markdown output
- when a TOC entry matches an extracted heading, it becomes an internal link like `[Section](#section)`
- when no matching heading exists in the extracted range, the entry stays as plain text

This works best on full-document conversions. Partial page-range conversions may leave some TOC entries unlinked because the target headings were not extracted.

## Known Limitations

- scanned PDFs still depend on OCR quality, so spelling and segmentation errors can remain
- complex contents pages may still contain occasional awkward splits when the source PDF is highly flattened
- internal TOC links only work when the corresponding heading survives extraction
- `pdftotext` is optional but still the strongest geometry source for many structured listings
- some non-code structured regions, especially heavily flattened inline bullet lists, can still need additional cleanup after extraction
- `--threads` is kept for interface compatibility but is currently unused

## Sample PDFs Used During Validation

The recent converter changes were checked against these example PDFs:

- `PureC_English_Overview-JLG.pdf`
- `cmanship-v1.0.pdf`
- `Atari-Compendium.pdf`
- `GEM_RCS-2.pdf`

These were used to validate:

- digital-PDF structure extraction
- image extraction
- scanned-PDF OCR on macOS
- code-block preservation
- contents-page cleanup and linking
