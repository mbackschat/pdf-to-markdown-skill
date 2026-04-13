# CLAUDE.md

This file is for developing and maintaining this skill project. It should let a future session quickly understand how the converter works, how to validate changes, and what kinds of regressions matter.

## Documentation Split

- `.claude/skills/pdf-to-markdown/SKILL.md`
  - For agents using the skill at runtime.
  - Keep it short and operational:
    - when to use the skill
    - how to invoke it
    - OCR decision rules
    - important flags
    - concise examples
- `README.md`
  - For humans evaluating or using the project.
  - Keep it user-facing:
    - what the project does
    - installation
    - usage
    - flags
    - outputs
    - a concise overview
- `CLAUDE.md`
  - For developing and maintaining the skill project.
  - Keep architecture, workflow, regression strategy, anti-overfitting guidance, and implementation caveats here.

If information is mainly useful to someone improving the converter, it belongs in `CLAUDE.md`, not in `SKILL.md` or `README.md`.

## Current Architecture

- Main extractor: `PyMuPDF4LLM`
- Default mode: digital-first extraction for born-digital PDFs
- OCR policy:
  - `--ocr` / `--scan` forces OCR
  - `--auto-ocr` opt-in enables OCR only for image-only pages
- Default OCR backend:
  - macOS: Apple Vision via `ocrmac`
  - other platforms: RapidOCR
  - explicit fallback: Tesseract

The converter is split into an internal `converter/` package:

- `document.py`
  - shared PDF/page access, cached outline loading, page-style lines, word geometry
- `page_types.py`
  - shared classification such as visible contents-page detection
- `ocr.py`
  - OCR resolution and backend selection
- `regions.py`
  - page-geometry grouping and structured/preformatted recovery
- `headings.py`
  - heading reconstruction from outline, visible TOC, extracted contents, and typography fallback
- `reference_entries.py`
  - suppression/demotion of non-hierarchical heading-like text in reference/manual layouts
- `contents_cleanup.py`
  - cleanup for visible contents sections when they survive extraction
- `cleanup.py`
  - main markdown cleanup orchestration
- `convert.py`
  - end-to-end conversion flow, image extraction handoff, file/folder orchestration helpers
- `text.py`, `models.py`
  - shared text helpers and data structures

`.claude/skills/pdf-to-markdown/pdf_to_markdown.py` should stay a thin CLI entry point, not grow into a new monolith.

## Conversion Pipeline

At a high level the current pipeline is:

1. Parse arguments and resolve OCR policy.
2. Convert the PDF with `PyMuPDF4LLM`.
3. When needed, use OCR on selected pages.
4. Recover layout-sensitive regions from geometry:
   - `pdftotext -bbox-layout` first when available
   - PyMuPDF word geometry as fallback
5. Reconstruct heading structure from the strongest available source:
   - embedded PDF outline first
   - visible contents-page layout second
   - extracted-markdown contents third
   - source-page typography fallback last
6. Suppress non-hierarchical heading-like text:
   - running page headers
   - reference-entry field labels
   - caption-like/local labels
   - dense control/item label regions
7. Strip visible contents sections from final markdown output.
8. Rewrite image paths and finalize output.

Important design choice:

- visible contents pages are mainly treated as internal structure data
- they are usually removed from final markdown because markdown readers already provide heading navigation

## Resume Workflow

When resuming work after a break, do not start by tuning heuristics against one PDF. Rebuild context in this order:

1. Read `CLAUDE.md`.
2. Review `tests/regression_cases.py`.
3. Run fast unit tests:
   - `python3 -m unittest tests/test_cleanup_primitives.py`
4. Run the fast smoke test:
   - `uv run .claude/skills/pdf-to-markdown/pdf_to_markdown.py tests/pdf/PureC_English_Overview-JLG.pdf`
5. Run the relevant regression suite:
   - `python3 tests/run_regression_checks.py headings`
   - `python3 tests/run_regression_checks.py listings`
6. Only then run heavier fixture conversions if the change is likely to affect them.

If `uv` cache access is restricted in the environment, use:

- `UV_CACHE_DIR=/tmp/uv-cache`

The regression runner already defaults that cache path internally.

## Regression Corpus

Treat the PDFs in `tests/pdf/` as a small regression corpus. They are regression fixtures, not templates for the converter.

- `PureC_English_Overview-JLG.pdf`
  - fastest smoke test
  - born-digital
  - catches:
    - running page-title leakage
    - heading cleanup regressions
    - syntax/project-file listing flattening
    - general preformatted recovery
- `cmanship-v1.0.pdf`
  - larger born-digital manual
  - catches:
    - long program listings
    - split fenced-block recovery
    - bullet/list corruption around listings
    - image extraction on reruns
- `WD1772-JLG.pdf`
  - born-digital manual with strong visible TOC and no embedded outline
  - catches:
    - visible-TOC indentation handling
    - sibling-vs-child heading depth errors
    - caption/label leakage into the heading tree
- `Bitbook2.pdf`
  - born-digital with weak/no useful outline and sparse heading signal
  - catches:
    - over-eager fallback heading creation
    - promotion of control labels or troubleshooting items to headings
    - noisy no-outline hierarchy
- `Atari-Compendium.pdf`
  - large born-digital reference manual
  - catches:
    - reference-entry handling
    - demotion of labels like `OPCODE`, `BINDING`, `COMMENTS`, `PARAMETERS`
    - signatures staying out of the heading tree
    - large-image extraction stability on reruns
- `GEM_RCS-2.pdf`
  - OCR-heavy scan fixture
  - catches:
    - OCR path validity
    - Apple Vision integration on macOS
    - OCR/image interaction problems
    - invisible OCR text requirement for extracted images
  - always run this with `--ocr`

Generated `.md` files and extracted image folders inside `tests/pdf/` are for local regression runs. The PDF corpus itself lives there as a git submodule.

## Regression Ground Rules

- Prefer structural invariants over exact whole-file output.
- Use `tests/regression_cases.py` as the durable summary of what must stay true.
- If a new bug is discovered in a real fixture, add a regression for it before or alongside the fix.
- If a regression is known but not fixed yet, it is acceptable to mark it as an expected failure temporarily, but convert it back to a normal passing regression once fixed.
- Keep quick iteration biased toward:
  - unit tests
  - the PureC smoke test
  - the smallest relevant regression suite
- Only use the larger fixtures when the change is likely to affect them.

The regression helpers are the main executable source of truth. `CLAUDE.md` should complement them by preserving:

- why each fixture matters
- what failure mode it is meant to catch
- the fastest validation workflow
- the broader design decisions that came out of those regressions

## Anti-Overfitting Guidance

- Treat the fixtures as regression guards, not as templates for the converter.
- Changes to conversion logic must solve a general extraction problem, not one document-specific quirk.
- Prefer stronger source signals over special cases:
  - embedded outline
  - visible TOC layout
  - source-page typography
  - page geometry
  - indentation
  - column alignment
  - repeated visual structure
- For listings, prefer structure-based recovery over language-specific detection.
- For headings and TOC reconstruction, do not rely mainly on title text, numbering style, or document-specific labels. Those are only weak supporting signals.
- If a fix improves one fixture but harms another, prefer the more conservative and more general behavior.
- Do not add title-specific, publisher-specific, or corpus-specific hacks unless the rule can be explained as a generic document-structure rule.
- When changing thresholds or heuristics, validate against more than one fixture, especially one that exercises a different failure mode.

The goal is not to make one fixture perfect in isolation. The goal is to keep the converter robust across mixed technical PDFs by improving general structure recovery.

## Current Behavior To Preserve

- Running page headers and page titles should not leak into markdown headings.
- Visible TOCs should repair heading hierarchy when reliable, but should usually be stripped from final output.
- No-outline fallback should be conservative and fairly shallow.
- Reference-entry pages should keep entry names as headings but demote signatures and field labels.
- Structured listings should be recovered from geometry, not language-specific token checks.
- OCR text inserted back into scanned PDFs must remain invisible, or extracted images will show doubled text.
- Image extraction should be rerun-safe for large manuals with many figures.

## Known Limitations

- Scanned PDFs still depend on OCR quality, so spelling and segmentation errors can remain.
- Complex visible TOCs can still flatten or split awkwardly when the source PDF is highly degraded.
- `pdftotext` is optional, but it remains the strongest geometry source for many structured listings.
- Some non-code structured regions, especially heavily flattened inline bullet runs, still need post-processing cleanup.
- `--threads` is kept for interface compatibility but is currently unused.
- Some region/grouping decisions are still threshold-driven, so the converter is cleaner but not heuristic-free.
- `--auto-ocr` is intentionally narrow and only reacts to image-only pages, not generally poor text layers.

## Regression Helpers

- `tests/test_cleanup_primitives.py`
  - fast unit tests for cleanup helpers and OCR-policy behavior
- `tests/regression_cases.py`
  - regression corpus metadata and expected structural checks
- `tests/run_regression_checks.py`
  - sample-PDF regression runner
- `tests/pdf/`
  - sample PDF corpus as a git submodule

Keep this file updated when the architecture, regression workflow, or fixture roles change materially.
