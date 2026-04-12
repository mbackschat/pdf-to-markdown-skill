# PLAN

This plan is based on the current implementation in [.claude/skills/pdf-to-markdown/pdf_to_markdown.py](./.claude/skills/pdf-to-markdown/pdf_to_markdown.py), the architecture note in [CONVERSION-DETAILS.md](./CONVERSION-DETAILS.md), and the findings already captured in [CODE_REVIEW.md](./CODE_REVIEW.md).

The goal is not to add more heuristics. The goal is to simplify the implementation, remove legacy spillover, reduce overfitting risk, and leave the converter in a cleaner architectural state.

## Problem Summary

The current implementation is directionally good, but it is still a hybrid:

- newer structure-first logic exists
- older heuristic cleanup still remains in the critical path
- several decisions still depend on narrow thresholds
- visible contents handling does extra work that is often discarded
- OCR policy is not fully explicit from the CLI contract

The main cleanup target is therefore not “better output by adding more special cases.” It is:

- fewer overlapping passes
- clearer ownership of each stage
- stronger structural logic earlier
- weaker heuristic cleanup later

## Guiding Principles

All cleanup work should follow these rules:

1. Prefer structural signals over content-shaped rules.
2. Prefer one decisive pass over multiple overlapping cleanup passes.
3. Keep fallbacks conservative rather than clever.
4. Do not add new document-specific heuristics unless they generalize clearly.
5. Remove redundant legacy logic instead of layering on top of it.
6. Add regression checks before changing behavior in core paths.

## Main Risks To Address

### 1. Legacy heading logic still mutates structure too early

Current problem:

- `fix_headings()` still does both running-header cleanup and heading-depth rewriting.
- The newer layered hierarchy system runs later, so the older pass can distort the input to the stronger logic.

Impact:

- unclear source of final heading depth
- harder reasoning about regressions
- legacy spillover in the most important structural path

### 2. Listing recovery uses both a newer region path and an older fallback path

Current problem:

- region-based rendering exists
- older `looks_like_preformatted()` fallback still remains in `restore_code_blocks_in_chunk()`
- grouping and rendering are still strongly shaped by page boxes from upstream extraction

Impact:

- mixed classification logic
- duplicated reasoning paths
- harder to improve without regressions

### 3. TOC/contents handling is conceptually right but operationally messy

Current problem:

- visible contents are used mainly as internal metadata
- but link generation still runs before contents sections are usually stripped
- PDF-layout TOC parsing is still fairly narrow
- Markdown TOC extraction and cleanup still do meaningful structural work late in the pipeline

Impact:

- redundant work
- blurred separation between structure inference and final output cleanup
- difficult fallback behavior to reason about

### 4. OCR behavior is not fully aligned with the CLI contract

Current problem:

- current behavior is `needs_ocr = args.ocr or without_text > 0`
- CLI wording and user mental model can still read as “OCR only when requested”

Impact:

- confusing runtime behavior
- docs and interface can drift
- harder test matrix for mixed PDFs

### 5. The script is too monolithic for safe cleanup

Current problem:

- extraction, OCR, layout recovery, hierarchy reconstruction, cleanup, and output all live in one file

Impact:

- high cognitive load
- expensive refactors
- hard to test subsystems independently

## Cleanup Strategy

The cleanup should happen in phases, with each phase leaving the system simpler than before.

## Status

- DONE: added lightweight regression coverage
- DONE: made OCR policy explicit with `--auto-ocr`
- DONE: removed legacy heading-depth rewriting from the main cleanup path
- DONE: simplified listing recovery to one primary region-driven path
- DONE: removed unconditional normal-path contents linking
- DONE: introduced a small shared conversion context for caches/state
- PARTLY DONE: widened PDF-layout TOC parsing with a conservative title-only fallback
- PARTLY DONE: split the script into smaller internal modules

## Phase 1: Lock Down Behavior With Regression Fixtures

Status: DONE

Before removing legacy logic, add lightweight regression checks around the known sample PDFs:

- `PureC_English_Overview-JLG.pdf`
- `cmanship-v1.0.pdf`
- `Atari-Compendium.pdf`
- `GEM_RCS-2.pdf`
- `WD1772-JLG.pdf`
- `Bitbook2.pdf`

What to verify:

- multiline listings remain fenced and preserve indentation reasonably
- long listings are not split into multiple adjacent fenced blocks
- chapter/section nesting is stable for PDFs with outline or visible TOC
- sparse no-outline PDFs remain conservative rather than inventing deep trees
- scanned PDFs still produce readable output when OCR is enabled

This phase should produce reusable fixture checks, not just ad-hoc manual inspection notes.

Implemented:

- `tests/test_cleanup_primitives.py`
- `tests/run_regression_checks.py`

Note:

- unit tests run successfully in this environment
- real-PDF regression checks are implemented, but sample-PDF execution was skipped here because `uv` dependency bootstrap could not complete under the current network restrictions

## Phase 2: Make OCR Policy Explicit and Final

Status: DONE

OCR is forced by `--ocr`, and can be auto-enabled only be `--auto-ocr` (a new flag)

but make it explicit in CLI help, docs, and code comments

Concrete work:

- centralize OCR decision logic into one helper, for example `resolve_ocr_policy(...)`
- remove scattered policy assumptions from docs and cleanup notes
- add explicit regression checks for mixed text plus image-only page sets

Implemented:

- new `--auto-ocr` CLI flag
- centralized OCR resolution via `resolve_ocr_resolution(...)`
- docs updated to match the explicit OCR contract
- unit coverage added for OCR resolution behavior

## Phase 3: Remove Legacy Heading Spillover

Status: DONE

This is the highest-priority cleanup.

Target:

- stop using `fix_headings()` for heading-depth rewriting
- keep only the part that still earns its keep, likely running-header removal

Concrete refactor:

1. Split `fix_headings()` into:
- `remove_running_headers(...)`
- legacy heading rewrite logic, temporarily isolated

2. Remove the legacy heading rewrite from the main cleanup pipeline.

3. Keep the layered order as the only structural hierarchy path:
- embedded outline
- visible contents from PDF layout
- visible contents from extracted Markdown
- visual typography fallback

Result:

- one authoritative path for heading nesting
- less hidden interaction between old and new logic

Implemented:

- `fix_headings()` is no longer used in the main cleanup path
- running-header cleanup is handled by `remove_running_headers(...)`
- heading depth now flows through the layered outline/contents/typography path

## Phase 4: Simplify Listing Recovery Into One Main Structural Path

Status: DONE

Target:

- keep one primary region-driven listing path
- reduce dependence on the older fallback classification path

Concrete refactor:

1. Treat `Region` plus region rendering as the main structured-recovery abstraction.
2. Move box grouping and classification decisions behind region helpers.
3. Isolate or remove `looks_like_preformatted()` once the region path covers its current wins.
4. Keep a fallback only if it has a clearly defined role and test coverage.

Important constraint:

- do not add language-specific detection rules
- do not add new document-specific exceptions

What to preserve:

- multiline code and syntax blocks
- aligned project-file examples
- two-column command/description layouts when they are clearly tabular
- multi-box or multi-page listing merges when boundaries are artificial

Implemented:

- removed the older `looks_like_preformatted()` fallback from the active chunk-repair path
- kept region-based structured rendering as the main listing-recovery path
- retained adjacent-region grouping and fenced-block merging

## Phase 5: Reframe Contents Logic As Internal Structure Data

Status: PARTLY DONE

Target:

- contents handling should primarily support heading reconstruction
- final-output contents cleanup should become a small, predictable step

Concrete refactor:

1. Separate structure extraction from visible-contents output cleanup.
2. Make contents-link generation optional or remove it from the normal pipeline.
3. Keep `strip_contents_sections(...)` only as a final-output decision, not as part of structural inference.
4. Widen the PDF-layout TOC parser slightly, but only with generic layout cues:
- indentation bands
- repeated aligned title-like lines
- document-order consistency

Avoid:

- title lexicons tied to one document family
- assumptions that all TOCs use dot leaders

Implemented:

- removed unconditional `link_contents_entries()` from the normal cleanup path
- kept visible contents primarily as structure data for heading reconstruction
- kept `strip_contents_sections(...)` as the final output cleanup step
- widened PDF-layout TOC parsing slightly with a conservative title-only fallback on recognized contents pages

Still remaining:

- further strengthen generic visible-TOC parsing without drifting into content-shaped heuristics

## Phase 6: Introduce a Small Internal Context Object

Status: DONE

The current code repeatedly reopens or re-derives related document information.

Introduce a small internal context object, for example:

- PDF path
- selected pages
- extracted outline
- style-line cache
- geometry cache
- text-page stats

Benefits:

- fewer scattered caches
- cleaner function signatures
- easier testing of extraction and reconstruction stages independently

This is a cleanup step, not a rewrite. Keep it lightweight.

Implemented:

- added `ConversionContext`
- moved shared caches/state into that context for extraction and cleanup reuse

## Phase 7: Split the Script by Responsibility

Status: PARTLY DONE

Once the critical logic is simplified, separate the file into a few internal modules.

Recommended split:

- `ocr_policy.py`
- `geometry.py`
- `regions.py`
- `headings.py`
- `cleanup.py`
- `cli.py`

This split should happen after the logic cleanup, not before. Splitting a still-messy design across files just spreads the mess around.

Implemented:

- extracted `pdfmd_models.py`
- extracted `pdfmd_ocr.py`

Still remaining:

- optional further split of heading/contents logic and geometry logic if the script grows again

## Specific Code Changes To Target

### Remove or reduce

- heading-depth rewriting inside `fix_headings()`
- unconditional `link_contents_entries()` in the normal cleanup path
- legacy fallback logic that duplicates region-based preformatted detection
- public-facing emphasis on `--threads` unless it becomes real

### Keep but constrain

- PDF-layout TOC parsing
- visual heading fallback
- group/merge logic for obviously split structured regions
- prose cleanup passes that act only outside fenced blocks

### Keep as core

- `PyMuPDF4LLM` as primary extractor
- region-based structured recovery
- outline-first hierarchy reconstruction
- visible-contents-as-metadata design
- geometry fallback from `pdftotext` to PyMuPDF

## Acceptance Criteria

The implementation should be considered cleaned up when these are true:

1. There is one authoritative heading-reconstruction path.
2. Legacy heading rewriting no longer runs before the modern hierarchy logic.
3. Listing recovery has one primary structural classifier, not two competing ones.
4. Contents handling is clearly split between:
- internal structure inference
- final-output suppression/retention
5. OCR behavior is explicit and test-covered.
6. The remaining heuristics are documented, limited, and justified.
7. The sample PDFs still pass regression checks after cleanup.

Current state:

- DONE: 1
- DONE: 2
- DONE: 3
- PARTLY DONE: 4
- DONE: 5
- DONE: 6
- PARTLY DONE: 7

Reason for partial on 7:

- regression tooling exists
- unit checks passed
- real sample-PDF runs were skipped here because `uv` could not fetch dependencies in the current restricted environment

## Order Of Work

Recommended execution order:

1. add regression fixtures
2. finalize OCR policy
3. split and reduce `fix_headings()`
4. simplify listing recovery around `Region`
5. separate contents inference from contents output cleanup
6. add a small context object and simplify signatures
7. split the file into internal modules

This order matters. It removes spillover first, then improves organization.

## What Not To Do

To avoid overfitting and overengineering, do not:

- add new language-specific code detection
- tune the implementation around one sample PDF at a time
- introduce ML-heavy classification just to replace a few heuristics
- split the file into many modules before the logic itself is simplified
- preserve redundant legacy logic “just in case” without tests proving it is still needed

## Short Version

The right cleanup plan is:

- freeze behavior with tests
- make OCR policy explicit
- remove legacy heading spillover
- make region-based structured recovery the main path
- treat contents as internal structure data, not body content
- simplify, then modularize

That path will improve the implementation without pushing it toward more overfitted or more complicated behavior.

## Short Version Now

- DONE: freeze core helper behavior with tests
- DONE: make OCR policy explicit
- DONE: remove legacy heading spillover
- DONE: make region-based structured recovery the main path
- PARTLY DONE: treat contents as internal structure data, not body content
- PARTLY DONE: split the cleaned logic into smaller modules
