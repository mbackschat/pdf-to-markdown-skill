# pdf-to-markdown

A [Claude Code skill](https://code.claude.com/docs/en/skills.md) that converts PDF files to Markdown with a digital-first pipeline based on [PyMuPDF4LLM](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/). It is optimized for technical documents and reference material, especially datasheets, hardware manuals, programming guides, and other structured documentation with tables, diagrams, and code listings.

This converter is mainly used on Atari ST and Amiga technical literature, so it is tuned toward manuals, reference books, developer documentation, and similar archival technical PDFs. It is not perfect, but it is designed to preserve headings, listings, tables, and figures better than a plain text dump.

## Features

- **Digital-first extraction** -- born-digital PDFs are handled by PyMuPDF4LLM for better native structure recovery
- **OCR support** -- `--ocr` / `--scan` forces OCR for scanned documents, and `--auto-ocr` opt-in enables OCR only for image-only pages
- **Best-default OCR backend** -- Apple Vision via `ocrmac` on macOS, RapidOCR elsewhere, with Tesseract as an explicit fallback
- **Batch mode** -- pass a folder to convert all PDFs in one go
- **Image extraction** -- diagrams and figures exported as PNGs, referenced inline in the Markdown
- **Heading reconstruction** -- rebuilds heading hierarchy from embedded outlines, visible TOCs, or page typography when possible
- **Contents stripping** -- visible contents pages are removed from the final Markdown because Markdown readers already provide heading navigation
- **Table extraction** -- register maps, pin tables, and similar layouts are rendered as Markdown tables when possible
- **Listing preservation** -- preformatted listings are recovered from page layout rather than language-specific keywords
- **Geometry fallback** -- if `pdftotext -bbox-layout` is unavailable, PyMuPDF word positions are used as a second layout source
- **Robust image-output reruns** -- extracted image folders are reset and repopulated safely on repeat conversions
- **Page range selection** -- convert only a subset of pages with `--pages`
- **Multi-language OCR** -- pass `--langs` with comma-separated language codes (default: `en`)

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (the script runs with `uv run` -- no global Python packages needed)
- Python 3.10+
- `pdftotext` from Poppler/Xpdf (optional, but recommended as the first geometry source for structured listings)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI, desktop app, or IDE extension

The first run will be slower as `uv` downloads the Python dependencies.
If `pdftotext` is installed, the script uses it as the primary geometry source for structured listing recovery; otherwise it falls back to PyMuPDF word positions.

## Installation

Copy or symlink the `.claude/skills/pdf-to-markdown/` directory into your project or your personal skills directory:

```bash
# Project-level (this project only)
cp -r .claude/skills/pdf-to-markdown /path/to/project/.claude/skills/

# Personal (all projects)
cp -r .claude/skills/pdf-to-markdown ~/.claude/skills/
```

## Usage

Inside Claude Code, invoke the skill with the `/pdf-to-markdown` slash command:

```
/pdf-to-markdown /path/to/datasheet.pdf
```

If you want to run the converter script directly while developing the project, use:

```bash
uv run .claude/skills/pdf-to-markdown/pdf_to_markdown.py /path/to/datasheet.pdf
```

### Examples

Convert a digital PDF (default, no OCR):
```
/pdf-to-markdown /path/to/manual.pdf
```

Convert a scanned PDF with full OCR:
```
/pdf-to-markdown /path/to/datasheet.pdf --ocr
```

Convert specific pages of a large document:
```
/pdf-to-markdown /path/to/large_book.pdf --pages 1-50
```

Convert with custom output location:
```
/pdf-to-markdown /path/to/file.pdf -o /path/to/output.md
```

Batch-convert all PDFs in a folder:
```
/pdf-to-markdown /path/to/pdf_folder/
```

Convert a German-language document:
```
/pdf-to-markdown /path/to/german_doc.pdf --langs de,en
```

### Flags

| Flag | Description |
|------|-------------|
| `-o`, `--output` | Output `.md` file path (default: next to the source PDF) |
| `--pages` | Page range, e.g. `1-50` (default: all pages) |
| `--ocr` / `--scan` | Force full-page OCR for scanned PDFs or broken text layers. |
| `--auto-ocr` | Enable OCR only when selected pages are image-only. |
| `--ocr-engine` | `auto` (default), `mac`, `rapidocr`, `tesseract` |
| `--langs` | Comma-separated language codes (default: `en`) |
| `--threads` | Compatibility flag retained for the skill interface; currently unused |

## Output

- Markdown files are written next to the source PDF by default (`doc.pdf` -> `doc.md`)
- Diagrams and figures are exported as PNGs in a `<name>_images/` subfolder
- Images are referenced inline: `![](name_images/picture_0001.png)`
- Visible contents pages are stripped from the Markdown output; heading navigation is preserved through the reconstructed Markdown hierarchy

## Notes

- Born-digital PDFs usually produce the best results.
- Scanned PDFs can work well with `--ocr`, but OCR quality still depends on the source document.
- Built-in PDF outlines are preferred when available. If no outline exists, the converter tries visible TOC pages and then falls back to page typography.
- For deeper implementation notes, regression strategy, and maintenance guidance, see [CLAUDE.md](./CLAUDE.md).

## License

This project is licensed under the MIT License. See [LICENSE](./LICENSE).
