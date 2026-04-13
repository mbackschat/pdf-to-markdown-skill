#!/usr/bin/env python3
"""Lightweight regression checks for real sample PDFs used during development."""

from __future__ import annotations

import re
import subprocess
import sys
import os
from pathlib import Path

from regression_cases import output_path_for, regression_cases, sample_pdf_dir


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / ".claude" / "skills" / "pdf-to-markdown" / "pdf_to_markdown.py"


def run_case(case: dict[str, object]) -> bool:
    pdf_path = Path(case["pdf"])
    if not pdf_path.exists():
        print(f"SKIP {case['name']}: missing {pdf_path}")
        return True

    cmd = [
        "uv",
        "run",
        str(SCRIPT_PATH),
        str(pdf_path),
        *[str(arg) for arg in case.get("args", [])],
    ]
    print(f"RUN  {case['name']}: {' '.join(cmd)}")
    env = dict(os.environ)
    env.setdefault("UV_CACHE_DIR", "/tmp/uv-cache")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, env=env)
    returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
    if returncode != 0:
        combined = f"{stdout}\n{stderr}"
        if any(
            marker in combined
            for marker in [
                "Failed to fetch",
                "dns error",
                "failed to lookup address information",
                "Failed to initialize cache",
            ]
        ):
            print(f"SKIP {case['name']}: uv environment unavailable")
            return True
        print(stdout)
        print(stderr)
        print(f"FAIL {case['name']}: converter exited with {returncode}")
        return False

    output_path = output_path_for(pdf_path)
    if not output_path.exists():
        print(f"FAIL {case['name']}: missing output {output_path}")
        return False

    md_text = output_path.read_text(encoding="utf-8")
    failures: list[str] = []
    for pattern in case["checks"]:
        if not re.search(pattern, md_text, flags=re.MULTILINE):
            failures.append(f"missing pattern {pattern}")
    for pattern in case.get("absent_checks", []):
        if re.search(pattern, md_text, flags=re.MULTILINE):
            failures.append(f"unexpected pattern {pattern}")

    if case.get("expected_failure"):
        if failures:
            print(f"XFAIL {case['name']}: {failures[0]}")
            return True
        print(f"XPASS {case['name']}: expected failure no longer fails")
        return False

    if failures:
        print(f"FAIL {case['name']}: {failures[0]}")
        return False

    print(f"PASS {case['name']}")
    return True


def main() -> int:
    requested_suite = sys.argv[1] if len(sys.argv) > 1 else None
    cases = regression_cases()
    if requested_suite:
        cases = [case for case in cases if case.get("suite") == requested_suite]
    print(f"Using sample PDF root: {sample_pdf_dir()}")
    ok = True
    for case in cases:
        ok = run_case(case) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
