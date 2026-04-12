# CLAUDE.md

This repository contains a Claude Code skill that converts PDFs to Markdown.

## Current Architecture

- Main extractor: `PyMuPDF4LLM`
- Default mode: digital-first extraction for born-digital PDFs
- OCR mode:
  - `--ocr` / `--scan` forces OCR
  - `--auto-ocr` opt-in enables OCR only for image-only pages
- Default OCR backend:
  - macOS: Apple Vision via `ocrmac`
  - other platforms: RapidOCR
  - explicit fallback: Tesseract
- The converter is now split into an internal `converter/` package:
  - `converter/text.py`
  - `converter/models.py`
  - `converter/ocr.py`
  - `converter/headings.py`
  - `converter/cleanup.py`
  - `converter/regions.py`
  - `converter/convert.py`
- `.claude/skills/pdf-to-markdown/pdf_to_markdown.py` should stay a thin CLI entry point and compatibility surface, not become a new monolith again.

## Verification Workflow

- For a quick smoke test after converter changes, use `PureC_English_Overview-JLG.pdf`.
- Command:
  - `uv run .claude/skills/pdf-to-markdown/pdf_to_markdown.py tests/pdf/PureC_English_Overview-JLG.pdf`
- This is the preferred fast end-to-end check because it is born-digital, small enough to run quickly, and exercises heading repair plus important preformatted listing recovery.
- Use the larger fixtures only when the change specifically affects them:
  - `cmanship-v1.0.pdf` for larger listings and image extraction
  - `WD1772-JLG.pdf` for visible-TOC heading reconstruction
  - `Atari-Compendium.pdf` for broader digital-manual structure checks
  - `GEM_RCS-2.pdf` only for OCR-related work because it is slower and scan-based

## Anti-Overfitting Guidance

- Treat the PDFs in `tests/pdf/` as regression fixtures, not as templates for the converter.
- Changes to conversion logic must solve a general extraction problem, not a document-specific quirk from one fixture.
- Prefer stronger source signals over special cases:
  - embedded outline
  - visible TOC layout
  - source-page typography
  - page geometry
  - indentation
  - column alignment
  - repeated visual structure
- For listings, prefer structure-based recovery over language-specific detection. Do not tune code-block handling around C, assembler, or wording from a single manual unless the rule clearly generalizes.
- For headings and TOC reconstruction, do not rely mainly on title text, numbering style, or document-specific labels. Use those only as weak supporting signals.
- If a fix improves one fixture but degrades another, prefer the more general and conservative behavior.
- Do not add publisher-specific, title-specific, or corpus-specific hacks unless they can be justified as a generic document-structure rule.
- When changing thresholds or heuristics, validate against more than one fixture, especially one that exercises a different failure mode.
- Use the fixtures to catch regressions in:
  - listing preservation
  - heading reconstruction
  - OCR behavior
  - image extraction
  - conservative fallback behavior
- Every new heuristic should be explainable in terms of a general document-structure problem. If it can only be justified by naming one fixture, it is probably too narrow.

## Change Intent

- The goal is not to make `PureC`, `cmanship`, `WD1772`, or any other single fixture look perfect in isolation.
- The goal is to keep the converter robust across mixed technical PDFs by improving general structure recovery.

## Markdown Quality Improvements

The converter includes a substantial post-processing pass, now mainly in `converter/cleanup.py`, to improve Markdown quality for technical books and manuals.

Current cleanup includes:

- heading cleanup and hierarchy repair
- removal of repeated running headers
- heading-level repair from the strongest available structure source:
  - embedded PDF outline/bookmark tree first
  - visible contents-page layout second
  - visible contents recovered from extracted Markdown third
  - source-page typography fallback fourth
- conversion of noisy contents pages into bullet lists
- removal of visible contents sections from final Markdown output
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

## Contents / Outline Behavior

Contents-related structure is treated primarily as internal metadata:

- if the PDF exposes a built-in outline/bookmark tree, that outline is used to repair Markdown heading depth
- otherwise, visible contents pages are parsed as internal structure data, including indentation/layout when available
- if visible contents-page layout is not usable, the script tries a weaker extracted-Markdown contents pass
- if neither outline nor usable contents structure exists, source-page heading typography is used as a conservative fallback
- visible contents sections are stripped from final Markdown output because Markdown readers already provide heading navigation

## Known Limitations

- scanned PDFs still depend on OCR quality, so spelling and segmentation errors can remain
- complex contents pages may still contain occasional awkward splits when the source PDF is highly flattened
- `pdftotext` is optional but still the strongest geometry source for many structured listings
- some non-code structured regions, especially heavily flattened inline bullet lists, can still need additional cleanup after extraction
- `--threads` is kept for interface compatibility but is currently unused
- some region and grouping decisions are still threshold-driven, so the cleanup is cleaner but not fully heuristic-free yet

## Regression Helpers

- `tests/test_cleanup_primitives.py` covers OCR policy and core cleanup helpers
- `tests/regression_cases.py` defines the sample corpus and suites
- `tests/run_regression_checks.py` runs sample-PDF checks when the referenced PDFs are available locally
- `tests/pdf/` now provides the sample PDF regression corpus as a Git submodule
- Keep quick iteration biased toward unit tests plus the PureC smoke test before running heavier fixtures

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
