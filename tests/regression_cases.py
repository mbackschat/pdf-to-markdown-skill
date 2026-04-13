"""Regression case definitions for real sample PDFs."""

from __future__ import annotations

import os
import re
from pathlib import Path


DEFAULT_SAMPLE_DIR = Path(__file__).resolve().parent / "pdf"


def sample_pdf_dir() -> Path:
    """Return the configured sample PDF directory."""
    configured = os.environ.get("PDF_TO_MARKDOWN_SAMPLE_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_SAMPLE_DIR


def output_path_for(pdf_path: Path) -> Path:
    """Return the default Markdown output path for a PDF."""
    stem = re.sub(r"[^\w-]", "_", pdf_path.stem).strip("_")
    stem = re.sub(r"_+", "_", stem)
    return pdf_path.parent / f"{stem}.md"


def regression_cases() -> list[dict[str, object]]:
    """Return grouped regression cases based on the configured sample PDF root."""
    pdf_dir = sample_pdf_dir()
    return [
        {
            "name": "PureC listings",
            "suite": "listings",
            "pdf": pdf_dir / "PureC_English_Overview-JLG.pdf",
            "args": [],
            "checks": [
                r"## Project \(.PRJ\) Files",
                r"```\n\{ output_file \| \* \}\n\{ \.L \[ <linker_options> \] \}",
            ],
        },
        {
            "name": "cmanship program listing",
            "suite": "listings",
            "pdf": pdf_dir / "cmanship-v1.0.pdf",
            "args": [],
            "checks": [
                r"```[\s\S]*do_froundrec\(\)",
                r"```[\s\S]*for \(color=1; color<7; \+\+color\) \{",
            ],
        },
        {
            "name": "WD1772 visible TOC hierarchy",
            "suite": "headings",
            "pdf": pdf_dir / "WD1772-JLG.pdf",
            "args": [],
            "checks": [
                r"## COMMAND DESCRIPTION",
                r"### COMMAND SUMMARY",
                r"#### Flag Summary",
            ],
        },
        {
            "name": "WD1772 command hierarchy should follow visible TOC",
            "suite": "headings",
            "pdf": pdf_dir / "WD1772-JLG.pdf",
            "args": [],
            "checks": [
                r"^## COMMAND DESCRIPTION$",
                r"^### COMMAND SUMMARY$",
                r"^#### Flag Summary$",
                r"^## TYPE I COMMANDS$",
                r"^### RESTORE \(SEEK TRACK 0\)$",
                r"^### SEEK$",
                r"^### STEP$",
                r"^### STEP-IN$",
                r"^### STEP-OUT$",
                r"^## TYPE II COMMANDS$",
                r"^### READ SECTOR$",
                r"^### WRITE SECTOR$",
                r"^## TYPE III COMMANDS$",
                r"^### Read Address$",
                r"^### Read Track$",
                r"^### WRITE TRACK FORMATTING THE DISK$",
                r"^#### CRC computation / verification in MFM$",
                r"^## TYPE IV COMMANDS$",
                r"^## Status Register$",
                r"^### Status Register Description$",
                r"^### Status Register Summary$",
                r"^### Single Density - 128 Bytes/Sector$",
                r"^### Double Density - 512 Bytes/Sector$",
                r"^### Non-Standard Formats$",
                r"^## Electrical and Timing Characteristics$",
            ],
            "absent_checks": [
                r"^###+ TYPE I COMMANDS$",
                r"^###+ TYPE II COMMANDS$",
                r"^###+ TYPE III COMMANDS$",
                r"^###+ TYPE IV COMMANDS$",
                r"^###+ Status Register$",
                r"^###+ Electrical and Timing Characteristics$",
                r"^#+ WD 1772 Floppy Disk Controller Specification$",
                r"^#+ Where:$",
            ],
        },
        {
            "name": "Bitbook sparse no-outline headings",
            "suite": "headings",
            "pdf": pdf_dir / "Bitbook2.pdf",
            "args": [],
            "checks": [
                r"## The Little Black Bit Book\.",
                r"## A 2\.5 Meg Socketed Ram Upgrade for the 1040ST",
                r"## CHAPTER 3\. TROUBLESHOOTING",
                r"#### 2\.2\.1 USER CONTROL NAMES AND OPERATIONS",
            ],
            "absent_checks": [
                r"^## [^#\n]*POWER SWITCH$",
                r"^## [^#\n]*BRIGHTNESS CONTROL$",
                r"^## [^#\n]*CONTRAST CONTROL$",
                r"^## [^#\n]*MODE SWITCH$",
                r"^## PROBLEM$",
                r"^## CHECK THESE ITEMS$",
                r"^## Abnormal picture$",
                r"^## CHAPTER 3\. TROUBLESHOOTING CHAPTER 4\. SPECIFICATIONS$",
            ],
        },
        {
            "name": "Bitbook formatter listing",
            "suite": "listings",
            "pdf": pdf_dir / "Bitbook2.pdf",
            "args": [],
            "checks": [
                r"```[\s\S]*/\* formath\.c Formatter fuer High Density Disketten \*/",
                r"```[\s\S]*Public Domain High Density Mini Formatter",
            ],
        },
        {
            "name": "Atari reference entries keep signatures out of heading tree",
            "suite": "headings",
            "pdf": pdf_dir / "Atari-Compendium.pdf",
            "args": [],
            "checks": [
                r"### Cauxin\(\)",
                r"\nWORD Cauxin\(VOID\)\n",
                r"### Cauxis\(\)",
                r"\nWORD Cauxis\(VOID\)\n",
                r"\n\*\*OPCODE\*\*",
                r"\n\*\*BINDING\*\*",
            ],
            "absent_checks": [
                r"^### WORD Cauxin\(VOID\)$",
                r"^### WORD Cauxis\(VOID\)$",
                r"^### OPCODE$",
                r"^### PARAMETERS$",
                r"^### BINDING$",
                r"^### COMMENTS$",
                r"^### AVAILABILITY$",
                r"^### CAVEATS$",
                r"^### RETURN VALUE$",
                r"^### SEE ALSO$",
            ],
        },
    ]
