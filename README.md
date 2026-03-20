# Citation Verifier

You wrote a paper with 50 citations. How will you verify that each one actually support the claim you're making?

Citation Verifier checks every citation in your LaTeX paper against the source PDFs using LLM semantic matching. It produces an annotated PDF with a verification appendix — each claim marked **Verified** (green) or **Needs Review** (red) with supporting passages and page numbers.

## Example

See it in action with the included test paper:

- **Input:** [test_input/main.tex](test_input/main.tex) — a satirical 5-page paper with 18 citations across 3 source papers
- **Output:** [test_input/output/main_verified.pdf](test_input/output/main_verified.pdf) — annotated PDF with verification appendix

Click any citation number in the output PDF → jumps to its appendix entry.
Click "back to text" in the appendix → jumps back to the claim.

## Understanding the Output

- **Verified** (green) = high-confidence semantic match found in the cited paper
- **Needs Review** (red) = partial match, low confidence, or not found in reviewed pages

The tool is intentionally conservative — it may flag citations as **Needs Review** even when they are correct. Treat red flags as prompts to double-check, not as errors.

Need step-by-step terminal instructions? See [SETUP.md](SETUP.md).

## Quick Start

```bash
cd citation-verifier
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env   # Edit: add your ANTHROPIC_API_KEY
```

### Step 1: Add your files

Place your files in the `input/` folder:

```
input/
  main.tex          ← your LaTeX file
  references.bib    ← (if using .bib)
  papers/           ← source PDFs
    smith2023.pdf
    jones2024.pdf
    ...
```

### Step 2: Generate mapping

```bash
python run.py --tex input/main.tex --papers input/papers
```

This parses your bibliography, extracts PDF titles, and writes `input/mapping.xlsx` — a spreadsheet matching each citation key to a PDF filename. Review it and fix any `??` entries.

### Step 3: Verify citations

```bash
python run.py --tex input/main.tex --papers input/papers
```

Each citation is sent to the LLM along with the source PDF text. The tool writes:

```
input/output/
  main_verified.tex                    ← your paper with hyperlinked citation numbers
  main_verified.pdf                    ← compiled PDF with verification appendix
  additional_output/verification.json  ← cached results (for incremental re-runs)
```

### Optional: Fetch papers from arXiv

```bash
python run.py --tex input/main.tex --papers input/papers --fetch-papers
```

Uses Crossref + arXiv to download source PDFs automatically. Requires `CROSSREF_EMAIL` in `.env`.

## How It Works

1. Parse bib entries from `.bib` or `\bibitem`
2. Extract citation groups from LaTeX (with sentence context)
3. Fuzzy-match bib titles to PDFs → `mapping.xlsx`
4. Extract text from matched PDFs (first N pages)
5. Send each claim + source text to LLM for semantic verification
6. Build appendix + patch LaTeX + compile PDF

## Cost

Approximately $0.015 per citation ($0.75 for 50 citations, Claude Sonnet). Use `--dry-run` to see an estimate before committing.

## Cache

Results cached by `sha256(cite_key|sentence)`. Edit a claim → only that citation re-verifies. Use `--recompile-only` to rebuild the PDF from cache without API calls. Crash-safe: results written incrementally.

## CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--tex PATH` | `main.tex` in cwd | Path to LaTeX file |
| `--papers PATH` | `papers/` next to tex | PDF directory |
| `--output PATH` | `output/` next to tex | Output directory |
| `--dry-run` | off | Parse only, estimate cost |
| `--recompile-only` | off | Rebuild from cache, no API calls |
| `--skip-compile` | off | Skip pdflatex |
| `--fetch-papers` | off | Crossref + arXiv fetch |
| `--concurrency N` | 10 | Parallel LLM calls |
| `--max-pages N` | 7 | Pages to extract per referenced PDF |

## Env Vars

```bash
ANTHROPIC_API_KEY=sk-ant-...    # required (or in ~/.env)
LLM_PROVIDER=anthropic          # or "openai"
LLM_MODEL=claude-sonnet-4-6
LLM_LIGHT_MODEL=claude-haiku-4-5
OPENAI_API_KEY=sk-...           # if using openai provider
CROSSREF_EMAIL=you@edu          # for --fetch-papers
```

## Supported Setup

- **LaTeX:** Single `main.tex`, natbib or biblatex citations
- **PDFs:** Any filenames in `papers/`. Auto-matched to bib entries by title.
- **Compiler:** `pdflatex` on PATH
- **Preamble:** Requires `\usepackage{hyperref}` and `\usepackage{xcolor}`
- **Python:** 3.10+

## JSTOR Downloader

Need source PDFs? The bundled [JSTOR Downloader](jstor-downloader/) is a Claude Code skill that batch-downloads papers from JSTOR via Chrome automation. Give it your `.bib` file and it fetches all PDFs in ~15 seconds.

## Not Yet Supported

- Scanned/image-only PDFs (OCR)
- Multi-file LaTeX (`\input{}`/`\include{}`)
  - you need to copy paste into your main.tex file

