# JSTOR Downloader

Claude Code skill for batch-downloading academic papers from JSTOR via Chrome browser automation.

## What It Does

Given a `.bib` file, `\bibitem` references, paper titles, or a JSON list, this skill:
1. Parses input and shows a download plan
2. Searches JSTOR in parallel (up to 8 tabs at once)
3. Downloads all PDFs simultaneously via `fetch()`
4. Renames and moves files to `~/Downloads/papers/`

~10-15 seconds for 20 papers.

## Requirements

- **Claude Code** with Chrome MCP, or **Claude CoWork** (claude.ai/cowork)
- **JSTOR access** via institutional login
- **Chrome** browser open

## Usage

**Claude Code:** Invoke the skill:
```
/jstor-downloader
```

**Claude CoWork:** Drop `SKILL.md` into your CoWork session, then ask it to download your papers.

Then provide papers in any format:
- A `.bib` file path
- Pasted `\bibitem` entries
- Plain text titles (one per line)
- JSON array of `{title, author, year}`

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Full skill instructions for Claude Code |
| `scripts/parse_bib.py` | BibTeX parser — extracts title/author/year/journal to JSON |

## Integration with Citation Verifier

Use this to download source PDFs, then run [Citation Verifier](../README.md) to check your citations against them:

```bash
# 1. Download papers from JSTOR
/jstor-downloader   # provide your .bib file

# 2. Move PDFs to citation verifier input
mv ~/Downloads/papers/*.pdf input/papers/

# 3. Verify citations
python run.py --tex input/main.tex --papers input/papers
```
