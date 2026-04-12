# pdf-to-markdown

A [Claude Code skill](https://code.claude.com/docs/en/skills.md) that converts PDF files to Markdown with a digital-first pipeline based on [PyMuPDF4LLM](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/). It is optimized for technical documents and reference material, especially datasheets, hardware manuals, programming guides, and other structured documentation with tables, diagrams, and code listings.

This converter is mainly used on Atari ST and Amiga technical literature, so the current heuristics are tuned toward manuals, reference books, developer documentation, and similar archival technical PDFs. It is not perfect, but it detects listings, tables, and synopsis-like reference sections with repeated structure quite reliably across that kind of material.

## Features

- **Digital-first extraction** -- born-digital PDFs are handled by PyMuPDF4LLM for better native structure recovery
- **OCR support** -- `--ocr` / `--scan` forces OCR for scanned documents, and `--auto-ocr` opt-in enables OCR only for image-only pages
- **Best-default OCR backend** -- Apple Vision via `ocrmac` on macOS, RapidOCR elsewhere, with Tesseract as an explicit fallback
- **Batch mode** -- pass a folder to convert all PDFs in one go
- **Image extraction** -- diagrams and figures exported as PNGs, referenced inline in the Markdown
- **Smart heading fix-up** -- detects and removes running headers; rebuilds heading hierarchy from the strongest available structural source
- **Layered heading reconstruction** -- prefers embedded PDF outlines, then visible TOC pages parsed from PDF layout, then visible contents recovered from Markdown, then page-typography cues for `##` / `###` / `####` nesting
- **Contents stripping** -- visible contents pages are removed from the Markdown output because Markdown readers already expose heading navigation
- **Visible TOC as structure data** -- contents pages are primarily used internally to repair heading nesting rather than preserved as final output
- **Table extraction** -- register maps, pin tables, etc. rendered as proper Markdown tables
- **Region-based structure recovery** -- positioned page text is grouped into structural regions before rendering
- **Language-agnostic preformatted detection** -- listings are recovered from layout signals rather than C-specific keywords
- **Code preservation** -- preformatted listings are kept as fenced blocks, with line breaks and indentation restored from page geometry when available
- **Layout-aware listing repair** -- two-column command/description layouts and aligned preformatted regions are reconstructed from positioned page text when possible
- **Geometry fallback** -- if `pdftotext -bbox-layout` fails or is unavailable, PyMuPDF word positions are used as a second layout source
- **Cross-page listing merge** -- adjacent fenced blocks from the same logical listing are merged when only page/chunk boundaries separate them
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

## Architecture Notes

The current hierarchy reconstruction order is:

1. embedded PDF outline/bookmarks
2. visible contents pages parsed from PDF layout
3. visible contents recovered from extracted Markdown
4. source-page typography fallback

The current listing-repair strategy is:

1. use `PyMuPDF4LLM` for the initial Markdown draft
2. recover positioned text from `pdftotext -bbox-layout` when available
3. fall back to PyMuPDF word geometry when needed
4. build structural regions and render likely preformatted blocks or definition-table layouts

The visible contents page is usually not preserved in the final Markdown. Instead, it is treated as internal structure data to improve the heading tree shown by Markdown readers.

## Regression Checks

The repo now includes lightweight cleanup checks:

- [tests/test_cleanup_primitives.py](./tests/test_cleanup_primitives.py) for unit-level behavior
- [tests/run_regression_checks.py](./tests/run_regression_checks.py) for real sample PDFs when those files are available locally
- [tests/regression_cases.py](./tests/regression_cases.py) for the configurable sample corpus and grouped regression suites

The sample PDF root now defaults to [tests/pdf](./tests/pdf). You can still override it with `PDF_TO_MARKDOWN_SAMPLE_DIR`.
The `tests/pdf/` directory is provided as a Git submodule with reference PDFs for regression testing. That submodule currently points to a private repository, so it may not be available unless you have access.

## Project Structure

```
.claude/
  skills/
    pdf-to-markdown/
      SKILL.md              # Skill definition (frontmatter + instructions for Claude)
      pdf_to_markdown.py    # PyMuPDF4LLM-based conversion script with region-based repair
      pdfmd_models.py       # Shared converter data structures
      pdfmd_ocr.py          # OCR policy and backend helpers
CLAUDE.md                   # Repo-level notes, architecture summary, and known limitations
tests/pdf/                  # Sample PDF regression corpus submodule
```

## License

This project is provided as-is for personal and commercial use.
