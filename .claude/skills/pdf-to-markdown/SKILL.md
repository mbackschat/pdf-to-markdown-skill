---
name: pdf-to-markdown
description: Convert PDF files to Markdown using Docling with OCR support for scanned documents. Use when user wants to convert PDF documentation to markdown.
argument-hint: <pdf-path-or-folder> [--ocr] [--output FILE] [--pages RANGE] [--ocr-engine ENGINE]
allowed-tools: Bash(uv *) Read
---

Convert PDF files to Markdown using Docling. Optimized for technical documentation (datasheets, hardware manuals, programming guides) with tables, diagrams, and code listings. By default, OCR is off (digital PDF mode). Pass `--ocr` or `--scan` for scanned documents.

## How to run

Use `uv run` to execute the script with no global installs required:

```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py $ARGUMENTS
```

If no arguments were provided, ask the user for a PDF file path or folder.

The argument can be a single PDF file or a folder. When a folder is given, all `*.pdf` files in it are converted as a batch (one `.md` per PDF). The `--output` flag is ignored in batch mode.

## Examples

Convert a digital PDF (default, no OCR):
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py /path/to/manual.pdf
```

Convert a scanned PDF with full OCR. Use `--ocr` or `--scan` when user says "scanned", "scan", or "ocr":
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py /path/to/datasheet.pdf --ocr
```

Convert specific pages of a large document:
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py /path/to/large_book.pdf --pages 1-50
```

Convert with custom output location:
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py /path/to/file.pdf -o /path/to/output.md
```

Convert all PDFs in a folder (batch mode):
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py /path/to/pdf_folder/
```

Convert a document with multiple languages:
```bash
uv run ${CLAUDE_SKILL_DIR}/pdf_to_markdown.py /path/to/german_doc.pdf --langs en,de
```

## Flags

| Flag | Description |
|------|-------------|
| `-o`, `--output` | Output `.md` file path (default: next to the source PDF as `<name>.md`) |
| `--pages` | Page range, e.g. `1-50` (default: all pages) |
| `--ocr` / `--scan` | Enable forced full-page OCR (for scanned PDFs). Use when user says "scanned", "scan", or "ocr". Off by default. |
| `--ocr-engine` | `auto` (default), `mac`, `easyocr`, `tesseract` |
| `--langs` | Comma-separated language codes (default: `en`) |
| `--threads` | Number of CPU threads (default: 4) |

## Output

- Markdown files are written next to the source PDF by default (e.g., `/path/to/doc.pdf` → `/path/to/doc.md`)
- Diagrams and figures are exported as PNGs in a `<name>_images/` subfolder next to the `.md` file
- Images are referenced inline in the markdown: `![](name_images/picture_0001.png)`

## Notes

- First run downloads Docling + PyTorch (~1-2 min); subsequent runs are fast
- Requires only `uv` and Python 3.10+
