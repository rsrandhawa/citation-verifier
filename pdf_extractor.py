"""
Standalone PDF text extraction module.
Extracts the first N pages of a PDF, cleans text, and stitches pages together
with page-boundary markers.

Uses only: fitz (PyMuPDF), re, os, pathlib
"""

import os
import re
from pathlib import Path

import fitz  # PyMuPDF


def extract_intro_pages(pdf_filename: str, papers_dir: str, max_pages: int = 7) -> dict | None:
    """Extract and clean text from the first max_pages of a PDF.

    Args:
        pdf_filename: Name of the PDF file (e.g. "paper.pdf").
        papers_dir: Directory containing the PDF.
        max_pages: Maximum number of pages to extract (1-indexed). Default 7.

    Returns:
        Dict with "pages" (list of {page_num, text}) and "stitched_text",
        or None if the PDF cannot be found/opened.
    """
    pdf_path = Path(papers_dir) / pdf_filename

    if not pdf_path.exists():
        print(f"Warning: PDF not found: {pdf_path}")
        return None

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        print(f"Warning: Could not open PDF {pdf_path}: {e}")
        return None

    num_pages = min(max_pages, len(doc))

    # --- Per-page extraction and cleaning ---
    cleaned_pages = []
    for i in range(num_pages):
        raw = doc[i].get_text()

        # Fix hyphenation at line breaks
        text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", raw)

        # Collapse 3+ newlines to double newline
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Strip excessive whitespace (leading/trailing per line, and overall)
        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join(lines).strip()

        cleaned_pages.append({"page_num": i + 1, "text": text})

    doc.close()

    # --- Page-boundary stitching ---
    if not cleaned_pages:
        return {"pages": [], "stitched_text": ""}

    parts = []
    for idx, page in enumerate(cleaned_pages):
        if idx == 0:
            parts.append(page["text"])
            continue

        prev_text = cleaned_pages[idx - 1]["text"]
        curr_text = page["text"]

        # Determine if previous page ended mid-sentence
        tail = prev_text[-100:] if len(prev_text) >= 100 else prev_text
        ends_mid_sentence = not re.search(r"[.!?]\s*$", tail)

        # Determine if current page starts lowercase or continues a word
        starts_continuation = bool(curr_text) and (
            curr_text[0].islower() or curr_text[0].isdigit()
        )

        marker = f" \u00abp.{page['page_num']}\u00bb "

        if ends_mid_sentence and starts_continuation:
            # Merge without extra spacing — just the marker
            parts.append(marker + curr_text)
        else:
            parts.append(marker + curr_text)

    stitched_text = "".join(parts)

    return {
        "pages": cleaned_pages,
        "stitched_text": stitched_text,
    }
