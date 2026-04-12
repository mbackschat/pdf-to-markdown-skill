---
name: pdf-to-markdown
description: Convert PDF files to Markdown using a PyMuPDF4LLM digital-first pipeline with OCR fallback for scanned documents. Use when user wants to convert PDF documentation to markdown.
argument-hint: <pdf-path-or-folder> [--ocr|--scan] [--output FILE] [--pages RANGE] [--ocr-engine ENGINE] [--langs LANGS] [--threads N]
allowed-tools: Bash(uv run:*), Read
---

Convert PDF files to Markdown using PyMuPDF4LLM. Optimized for technical documentation (datasheets, hardware manuals, programming guides) with tables, diagrams, and code listings. By default, OCR is off for digital PDFs. Pass `--ocr` or `--scan` for scanned documents.

The converter also applies post-processing to improve headings, contents pages, tables, lists, and flattened preformatted listings.

It also performs a region-based structural repair pass that can reconstruct:

- two-column command/description layouts
- aligned preformatted listings
- indented syntax and file-list examples
- multi-page listings that were split only by chunk/page boundaries

Repository-wide implementation notes and current limitations live in the root `CLAUDE.md`.

## How to run

Use `uv run` to execute the script with no global installs required:

```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py $ARGUMENTS
```

If the PDF path or output path contains spaces, quote that path when constructing the command.

If no arguments were provided, ask the user for a PDF file path or folder.

The argument can be a single PDF file or a folder. When a folder is given, all `*.pdf` files in it are converted as a batch (one `.md` per PDF). The `--output` flag is ignored in batch mode.

## Examples

Convert a digital PDF (default, no OCR):
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py "/path/to/manual.pdf"
```

Convert a scanned PDF with full OCR. Use `--ocr` or `--scan` when user says "scanned", "scan", or "ocr":
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py "/path/to/datasheet.pdf" --ocr
```

Convert specific pages of a large document:
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py "/path/to/large_book.pdf" --pages 1-50
```

Convert with custom output location:
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py "/path/to/file.pdf" -o "/path/to/output.md"
```

Convert all PDFs in a folder (batch mode):
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py "/path/to/pdf_folder/"
```

Convert a document with multiple languages:
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py "/path/to/german_doc.pdf" --langs en,de
```

## Flags

| Flag | Description |
|------|-------------|
| `-o`, `--output` | Output `.md` file path (default: next to the source PDF as `<name>.md`) |
| `--pages` | Page range, e.g. `1-50` (default: all pages) |
| `--ocr` / `--scan` | Enable forced full-page OCR (for scanned PDFs). Use when user says "scanned", "scan", or "ocr". Off by default. |
| `--ocr-engine` | `auto` (default), `mac`, `rapidocr`, `tesseract` |
| `--langs` | Comma-separated language codes (default: `en`) |
| `--threads` | Compatibility flag kept for the skill interface; currently unused |

## Output

- Markdown files are written next to the source PDF by default (e.g., `/path/to/doc.pdf` → `/path/to/doc.md`)
- Diagrams and figures are exported as PNGs in a `<name>_images/` subfolder next to the `.md` file
- Images are referenced inline in the markdown: `![](name_images/picture_0001.png)`
- Contents pages prefer internal Markdown links over copied PDF page numbers when matching headings are available

## Notes

- First run downloads PyMuPDF4LLM and OCR dependencies; subsequent runs are fast
- Requires only `uv` and Python 3.10+
- On macOS, `auto` prefers Apple Vision via `ocrmac`; elsewhere it prefers RapidOCR
- If `pdftotext` is installed, the script uses it as the primary geometry source for structured listing recovery
- If `pdftotext` is unavailable or fails, the script falls back to PyMuPDF word positions for structured listing recovery
- Internal TOC links work best on full-document conversions; partial page ranges may leave some entries unlinked
