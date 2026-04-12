#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h}
pdf_path="$repo_root/tests/pdf/GEM_RCS-2.pdf"

cd "$repo_root"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"

log_file=$(mktemp)
trap 'rm -f "$log_file"' EXIT

uv run .claude/skills/pdf-to-markdown/pdf_to_markdown.py "$pdf_path" --ocr >"$log_file" 2>&1

grep -q '^  Image-only pages: 70$' "$log_file"
grep -q '^  OCR active:    True$' "$log_file"
grep -q '^  OCR backend:   mac$' "$log_file"
grep -q '^OCR on page.number=0/1\.$' "$log_file"
