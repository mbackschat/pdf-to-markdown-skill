# pdf-to-markdown

A [Claude Code skill](https://code.claude.com/docs/en/skills.md) that converts PDF files to Markdown using [Docling](https://github.com/docling-project/docling) with OCR support for scanned documents. Optimized for technical documentation such as datasheets, hardware manuals, and programming guides with tables, diagrams, and code listings.

## Features

- **OCR support** -- opt-in full-page OCR with `--ocr` or `--scan` for scanned documents; digital PDFs work out of the box
- **Platform-native OCR** -- Apple Vision on macOS, EasyOCR on Linux/Windows, with Tesseract as an option
- **Batch mode** -- pass a folder to convert all PDFs in one go
- **Image extraction** -- diagrams and figures exported as PNGs, referenced inline in the Markdown
- **Smart heading fix-up** -- detects and removes running headers; uses the document's Table of Contents to restore proper heading hierarchy
- **Table extraction** -- register maps, pin tables, etc. rendered as proper Markdown tables
- **Code preservation** -- code listings kept as fenced code blocks
- **Page range selection** -- convert only a subset of pages with `--pages`
- **Multi-language OCR** -- pass `--langs` with comma-separated language codes (default: `en`)

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (the script runs with `uv run` -- no global Python packages needed)
- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI, desktop app, or IDE extension

The first run will be slower (~1-2 min) as `uv` downloads Docling and its dependencies (including PyTorch).

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
| `--ocr` / `--scan` | Enable forced full-page OCR (for scanned PDFs). Off by default. |
| `--ocr-engine` | `auto` (default), `mac`, `easyocr`, `tesseract` |
| `--langs` | Comma-separated language codes (default: `en`) |
| `--threads` | Number of CPU threads (default: 4) |

## Output

- Markdown files are written next to the source PDF by default (`doc.pdf` -> `doc.md`)
- Diagrams and figures are exported as PNGs in a `<name>_images/` subfolder
- Images are referenced inline: `![](name_images/picture_0001.png)`

## Project Structure

```
.claude/
  skills/
    pdf-to-markdown/
      SKILL.md              # Skill definition (frontmatter + instructions for Claude)
      pdf_to_markdown.py    # Docling-based conversion script (PEP 723 inline metadata)
```

## License

This project is provided as-is for personal and commercial use.
