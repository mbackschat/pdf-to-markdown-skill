---
name: pdf-to-markdown
description: Convert PDF files to Markdown using a PyMuPDF4LLM digital-first pipeline with explicit OCR flags for scanned or image-only documents. Use when user wants to convert PDF documentation to markdown.
argument-hint: <pdf-path-or-folder> [--ocr|--scan] [--auto-ocr] [--output FILE] [--pages RANGE] [--ocr-engine ENGINE] [--langs LANGS] [--threads N]
allowed-tools: Bash(uv run:*), Read
---

Convert PDF files to Markdown using PyMuPDF4LLM. Optimized for technical documentation (datasheets, hardware manuals, programming guides) with tables, diagrams, and code listings. `--ocr` or `--scan` forces OCR for scanned documents. `--auto-ocr` opt-in enables OCR only when selected pages are image-only.

The converter also applies post-processing to improve:

- heading hierarchy
- visible contents-page handling
- tables and lists
- flattened preformatted listings
- extracted image path handling

Important behavior for agents:

- Prefer no OCR for born-digital PDFs.
- Use `--ocr` / `--scan` only when the PDF is scanned, image-only, or has a broken text layer.
- `--auto-ocr` enables OCR only for image-only pages.
- Built-in PDF outlines are preferred for heading structure; visible contents pages are the next fallback.
- Visible contents pages are usually removed from final Markdown because markdown readers already provide heading navigation.

Repository-wide implementation details and limitations live in the root `CLAUDE.md`.

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
| `--ocr` / `--scan` | Force full-page OCR for scanned PDFs or broken text layers. Use when user says "scanned", "scan", or "ocr". |
| `--auto-ocr` | Enable OCR only when selected pages are image-only. |
| `--ocr-engine` | `auto` (default), `mac`, `rapidocr`, `tesseract` |
| `--langs` | Comma-separated language codes (default: `en`) |
| `--threads` | Compatibility flag kept for the skill interface; currently unused |

## Output

- Markdown files are written next to the source PDF by default (e.g., `/path/to/doc.pdf` → `/path/to/doc.md`)
- Diagrams and figures are exported as PNGs in a `<name>_images/` subfolder next to the `.md` file
- Images are referenced inline in the markdown: `![](name_images/picture_0001.png)`
- Visible contents pages are stripped from the Markdown output; heading navigation is preserved through the reconstructed Markdown hierarchy
