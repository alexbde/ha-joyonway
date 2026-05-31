#!/usr/bin/env python3
"""Convert PDF files in docs/ to Markdown text files for easy reference."""
import subprocess
import sys
import re
import shutil
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

def pdf_to_md(pdf_path: Path) -> Path:
    """Convert a single PDF to a .md file using pdftotext."""
    md_path = pdf_path.with_suffix(".md")
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: pdftotext failed for {pdf_path.name}: {result.stderr}")
        sys.exit(1)

    raw = result.stdout
    # Light cleanup: collapse 3+ blank lines into 2
    cleaned = re.sub(r'\n{4,}', '\n\n\n', raw)

    md_path.write_text(cleaned, encoding="utf-8")
    print(f"OK: {pdf_path.name} -> {md_path.name}  ({len(cleaned)} chars)")
    return md_path

def main():
    if shutil.which("pdftotext") is None:
        print("ERROR: 'pdftotext' is not installed or not in PATH.")
        print("Install poppler to provide pdftotext, then retry.")
        sys.exit(1)

    if not DOCS_DIR.exists():
        print(f"docs/ directory not found at {DOCS_DIR}")
        sys.exit(1)

    pdfs = sorted(DOCS_DIR.glob("*.pdf"))
    if not pdfs:
        print("No PDF files found in docs/")
        sys.exit(1)

    for pdf in pdfs:
        pdf_to_md(pdf)

    print(f"\nDone. {len(pdfs)} file(s) converted.")

if __name__ == "__main__":
    main()

