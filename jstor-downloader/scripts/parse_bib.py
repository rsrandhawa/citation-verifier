#!/usr/bin/env python3
"""Parse BibTeX (.bib) files into JSON for the JSTOR downloader.

Usage:  python3 parse_bib.py <path-to-bib-file>
Output: JSON array on stdout: [{title, author, year, journal, key}, ...]
"""
import sys, re, json

def parse_bib(filepath):
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    entries = []
    for match in re.finditer(r"@(\w+)\s*\{([^,]*),\s*(.*?)\n\s*\}", content, re.DOTALL):
        if match.group(1).lower() in ("string", "comment", "preamble"):
            continue
        body = match.group(3)
        fields = {}
        for fm in re.finditer(r'(\w+)\s*=\s*(?:\{((?:[^{}]|\{[^{}]*\})*)\}|"([^"]*)"|(\d+))', body):
            k = fm.group(1).lower()
            v = fm.group(2) or fm.group(3) or fm.group(4) or ""
            fields[k] = re.sub(r"\s+", " ", re.sub(r"[{}\\]", "", v)).strip()
        entry = {
            "key": match.group(2).strip(),
            "title": fields.get("title", ""),
            "author": fields.get("author", ""),
            "year": fields.get("year", ""),
            "journal": fields.get("journal", fields.get("booktitle", "")),
        }
        if entry["title"]:
            entries.append(entry)
    return entries

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 parse_bib.py <path>", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(parse_bib(sys.argv[1]), indent=2))
