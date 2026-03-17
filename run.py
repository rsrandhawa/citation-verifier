#!/usr/bin/env python3
"""Citation Verification Pipeline — Entry Point"""

import argparse
import asyncio
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

import agents
import bib_parser
import paper_fetcher
import parser
import pdf_extractor
import pdf_mapper
import postprocess


def load_config():
    """Load configuration from .env and CLI args."""
    # Load project .env first, then user's ~/.env for API keys
    load_dotenv()
    load_dotenv(Path.home() / ".env")

    ap = argparse.ArgumentParser(description="Citation Verification Pipeline")
    ap.add_argument("--tex", default=None, help="Path to main.tex")
    ap.add_argument("--papers", default=None, help="Path to papers/ directory")
    ap.add_argument("--output", default=None, help="Output directory")
    ap.add_argument("--dry-run", action="store_true", help="Parse only, estimate cost, no API calls")
    ap.add_argument("--recompile-only", action="store_true", help="Rebuild from cache, no API calls")
    ap.add_argument("--skip-compile", action="store_true", help="Skip pdflatex compilation")
    ap.add_argument("--fetch-papers", action="store_true", help="Fetch papers via Crossref + arXiv")
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--max-pages", type=int, default=None)
    args = ap.parse_args()

    # Find tex file
    tex_path = args.tex
    if not tex_path:
        # Look in current directory
        candidates = list(Path(".").glob("main.tex"))
        if candidates:
            tex_path = str(candidates[0])
        else:
            candidates = list(Path(".").glob("*.tex"))
            if len(candidates) == 1:
                tex_path = str(candidates[0])
            else:
                print("ERROR: No --tex specified and no main.tex found")
                sys.exit(1)

    tex_dir = str(Path(tex_path).parent)

    config = {
        "tex_path": tex_path,
        "papers_dir": args.papers or os.path.join(tex_dir, "papers"),
        "output_dir": args.output or os.path.join(tex_dir, "output"),
        "provider": os.getenv("LLM_PROVIDER", "anthropic"),
        "model": os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
        "light_model": os.getenv("LLM_LIGHT_MODEL", "claude-haiku-4-5"),
        "max_pages": args.max_pages or int(os.getenv("MAX_PAGES", "7")),
        "concurrency": args.concurrency or int(os.getenv("CONCURRENCY", "10")),
        "max_tokens": int(os.getenv("MAX_TOKENS", "1024")),
        "timeout": int(os.getenv("TIMEOUT", "90")),
        "dry_run": args.dry_run,
        "recompile_only": args.recompile_only,
        "skip_compile": args.skip_compile,
        "fetch_papers": args.fetch_papers,
    }

    Path(config["output_dir"]).mkdir(parents=True, exist_ok=True)
    Path(config["output_dir"], "additional_output").mkdir(parents=True, exist_ok=True)
    return config


def validate_api_key(config):
    provider = config["provider"]
    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key or not key.startswith("sk-ant-"):
            print("ERROR: ANTHROPIC_API_KEY not set or invalid in .env")
            print("Get your key at: https://console.anthropic.com/settings/keys")
            sys.exit(1)
    elif provider == "openai":
        key = os.getenv("OPENAI_API_KEY", "")
        if not key or not key.startswith("sk-"):
            print("ERROR: OPENAI_API_KEY not set or invalid in .env")
            sys.exit(1)


def compile_pdf(output_dir, source_dir):
    """Compile main_verified.tex -> main_verified.pdf in output_dir."""
    tex_dir = Path(output_dir)
    src_dir = Path(source_dir)

    # Symlink ALL files from source directory into output/
    for item in src_dir.iterdir():
        if item.name == tex_dir.name:
            continue
        target = tex_dir / item.name
        if not target.exists():
            target.symlink_to(item.resolve())

    # Detect if .bib file is used
    tex_content = (tex_dir / "main_verified.tex").read_text()
    uses_bib_file = bool(re.search(r'\\bibliography\{|\\addbibresource\{', tex_content))

    if uses_bib_file:
        steps = [
            ["pdflatex", "-interaction=nonstopmode", "main_verified.tex"],
            ["bibtex", "main_verified"],
            ["pdflatex", "-interaction=nonstopmode", "main_verified.tex"],
            ["pdflatex", "-interaction=nonstopmode", "main_verified.tex"],
        ]
    else:
        steps = [
            ["pdflatex", "-interaction=nonstopmode", "main_verified.tex"],
            ["pdflatex", "-interaction=nonstopmode", "main_verified.tex"],
        ]

    for step in steps:
        result = subprocess.run(step, cwd=tex_dir, capture_output=True, text=True, timeout=120)
        if result.returncode != 0 and step == steps[-1]:
            print(f"WARNING: {step[0]} returned errors. Check output/main_verified.log")

    # Clean temp files
    for ext in [".aux", ".log", ".out", ".toc", ".bbl", ".blg", ".fls", ".fdb_latexmk"]:
        (tex_dir / f"main_verified{ext}").unlink(missing_ok=True)

    # Clean symlinks
    for item in tex_dir.iterdir():
        if item.is_symlink():
            item.unlink()


def save_entry_map(entry_map, output_dir):
    import json
    path = Path(output_dir) / "additional_output" / "entry_map.json"
    with open(path, "w") as f:
        json.dump(entry_map, f, indent=2)


def load_entry_map(output_dir):
    import json
    path = Path(output_dir) / "additional_output" / "entry_map.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def print_summary(all_results):
    total = len(all_results)
    verified = sum(1 for r in all_results if r.verified)
    needs_review = sum(1 for r in all_results if not r.verified and not r.error)
    errors = sum(1 for r in all_results if r.error)

    print(f"\n{'='*50}")
    print(f"VERIFICATION COMPLETE")
    print(f"{'='*50}")
    print(f"Total citations verified:  {total}")
    print(f"  Verified (high conf):    {verified}")
    print(f"  Needs Review:            {needs_review}")
    print(f"  Errors:                  {errors}")
    print(f"{'='*50}")


async def main():
    config = load_config()

    # 1. Parse bib
    bib_entries = bib_parser.parse_bib(config["tex_path"], config)
    print(f"Loaded {len(bib_entries)} bib entries")

    # 1a. LLM title extraction for bibitems (async, only if not dry-run)
    if not config["dry_run"] and not config["recompile_only"]:
        bib_entries = await bib_parser.resolve_llm_titles(
            bib_entries, config["tex_path"], config
        )

    # 2. Fetch papers (if --fetch-papers)
    if config.get("fetch_papers"):
        email = os.getenv("CROSSREF_EMAIL", "")
        if not email:
            print("ERROR: CROSSREF_EMAIL not set in .env (required for --fetch-papers)")
            print("Add: CROSSREF_EMAIL=your@email.edu")
            sys.exit(1)
        paper_fetcher.fetch_all(bib_entries, config["papers_dir"], email)
        return

    # 3. Parse citations
    groups = parser.parse_citations(config["tex_path"])
    print(f"Found {len(groups)} citation groups, {sum(len(g.cite_keys) for g in groups)} total cite keys")

    # 3a. Clean sentences with LLM (skip if dry-run to avoid API calls)
    if not config["dry_run"] and not config["recompile_only"]:
        validate_api_key(config)
        groups = await parser.clean_sentences_with_llm(groups, config)
        print(f"Cleaned {len(groups)} sentences via light model")

    # 4. PDF Mapping
    mapping_path = Path(config["tex_path"]).parent / "mapping.xlsx"

    if not mapping_path.exists() and not config["recompile_only"]:
        if config["dry_run"]:
            print("\n[DRY RUN] Would generate mapping.xlsx (requires LLM calls for PDF title extraction)")
            print(f"  Papers dir: {config['papers_dir']}")
            pdfs = list(Path(config['papers_dir']).glob("*.pdf")) if Path(config['papers_dir']).exists() else []
            print(f"  PDFs found: {len(pdfs)}")
        else:
            await pdf_mapper.generate_mapping(bib_entries, config["papers_dir"], str(mapping_path), config)
            print(f"\n-> Review {mapping_path}")
            print(f"-> Fix any '??' entries with the correct PDF filename")
            print(f"-> Re-run: python {' '.join(sys.argv)}")
        return

    # Load mapping
    if mapping_path.exists():
        cite_to_pdf = pdf_mapper.load_mapping(str(mapping_path))
        print(f"Loaded mapping: {len(cite_to_pdf)} cite keys -> PDF files")
    else:
        cite_to_pdf = {}

    # 5. Extract PDFs
    unique_keys = set(k for g in groups for k in g.cite_keys)
    pdf_data = {}
    missing = []
    for key in unique_keys:
        pdf_filename = cite_to_pdf.get(key)
        if pdf_filename is None:
            missing.append(key)
        else:
            result = pdf_extractor.extract_intro_pages(pdf_filename, config["papers_dir"], config["max_pages"])
            if result is None:
                missing.append(key)
            else:
                pdf_data[key] = result

    print(f"Extracted PDFs: {len(pdf_data)} found, {len(missing)} missing")
    if missing:
        print(f"  Missing: {missing}")

    # 5b. Dry run
    if config["dry_run"]:
        total_calls = sum(len(g.cite_keys) for g in groups) - len(missing)
        est_cost = total_calls * 0.015
        print(f"\n--- DRY RUN ---")
        print(f"Would make {total_calls} LLM verification calls")
        print(f"Estimated cost: ${est_cost:.2f}")
        print(f"Estimated time: {total_calls * 3 / config['concurrency']:.0f}s at concurrency={config['concurrency']}")
        return

    # 6. Load cache
    cache = agents.load_cache(config["output_dir"])

    # 6a. Recompile-only: load ALL cached results directly
    if config["recompile_only"]:
        all_results = list(cache.values())
        if not all_results:
            print("ERROR: No cached results found. Run full pipeline first.")
            sys.exit(1)
        print(f"Loaded {len(all_results)} results from cache")
        to_verify = []
    else:
        # 7. Verification
        all_results = []
        to_verify = []

        for group in groups:
            for key in group.cite_keys:
                sentence_hash = agents.compute_hash(key, group.clean_sentence)
                cached = cache.get(sentence_hash)
                if cached:
                    print(f"  [cached] {key} (group_{group.line_number})")
                    all_results.append(cached)
                elif key in missing:
                    all_results.append(agents.make_missing_result(group, key))
                else:
                    to_verify.append((group, key))

    if to_verify:
        print(f"\nVerifying {len(to_verify)} citations ({len(all_results)} from cache)...")
        sem = asyncio.Semaphore(config["concurrency"])
        cache_lock = asyncio.Lock()
        counter = {"done": 0, "total": len(to_verify)}

        async def verify_one(group, key):
            async with sem:
                result = await agents.verify_citation(
                    group, key, pdf_data[key], bib_entries.get(key), config
                )
                counter["done"] += 1
                status = "verified" if result.confidence == "high" else "needs review"
                if result.error:
                    status = f"error: {result.error}"
                print(f"  [{counter['done']}/{counter['total']}] {key} {status}")

                async with cache_lock:
                    agents.append_to_cache(result, config["output_dir"])

                return result

        new_results = await asyncio.gather(*[verify_one(g, k) for g, k in to_verify])
        all_results.extend(new_results)

    # 8. Group results
    grouped = defaultdict(list)
    for r in all_results:
        grouped[r.group_id].append(r)

    # 9. Build appendix
    entry_map = postprocess.build_appendix(grouped, bib_entries, config["output_dir"])
    save_entry_map(entry_map, config["output_dir"])

    # 10. Patch tex
    tex_content = Path(config["tex_path"]).read_text(encoding="utf-8")
    postprocess.patch_tex(tex_content, entry_map, groups, config["output_dir"])

    # 11. Compile
    if not config["skip_compile"]:
        print("\nCompiling PDF...")
        source_dir = str(Path(config["tex_path"]).parent)
        compile_pdf(config["output_dir"], source_dir)
        pdf_path = Path(config["output_dir"]) / "main_verified.pdf"
        if pdf_path.exists():
            print(f"Output: {pdf_path}")
        else:
            print("WARNING: PDF compilation may have failed. Check output directory.")

    # 12. Summary
    print_summary(all_results)


if __name__ == "__main__":
    asyncio.run(main())
