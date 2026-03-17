"""Extract bibliography metadata from .bib files or inline \\begin{thebibliography}."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class BibEntry:
    cite_key: str
    title: str
    authors: str
    year: str
    journal: str | None
    entry_type: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """Strip LaTeX formatting artifacts from extracted text."""
    text = text.replace("et~al.", "et al.")
    text = text.replace("\\newblock", "")
    # \emph{...} / \textit{...} → contents
    text = re.sub(r"\\emph\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\textit\{([^}]*)\}", r"\1", text)
    # bare \em (toggle style) — just remove the command
    text = re.sub(r"\\em\b", "", text)
    # strip remaining { and }
    text = text.replace("{", "").replace("}", "")
    return text.strip()


def _extract_year(text: str) -> str:
    """Pull a four-digit year from parenthetical (YYYY) pattern."""
    m = re.search(r"\((\d{4})\)", text)
    if m:
        return m.group(1)
    # bare four-digit year as fallback
    m = re.search(r"\b((?:19|20)\d{2})\b", text)
    return m.group(1) if m else ""


def _extract_authors(text: str) -> str:
    """Return the text preceding the first (YYYY) pattern, as authors."""
    m = re.search(r"\(\d{4}\)", text)
    if m:
        return _clean_text(text[: m.start()])
    return _clean_text(text)


# ---------------------------------------------------------------------------
# .bib file parsing (pybtex)
# ---------------------------------------------------------------------------

def _parse_bib_file(bib_path: Path) -> dict[str, BibEntry]:
    """Parse a .bib file using pybtex and return entries keyed by cite_key."""
    from pybtex.database.input import bibtex as bibtex_input

    parser = bibtex_input.Parser()
    try:
        bib_data = parser.parse_file(str(bib_path))
    except Exception as exc:
        logger.warning("pybtex failed to parse %s: %s", bib_path, exc)
        return {}

    entries: dict[str, BibEntry] = {}
    for key, entry in bib_data.entries.items():
        # Title — strip braces
        title = entry.fields.get("title", "")
        title = title.replace("{", "").replace("}", "").strip()

        # Authors — format as "Last, First and Last, First"
        try:
            authors = " and ".join(
                str(person) for person in entry.persons.get("author", [])
            )
        except Exception:
            authors = ""

        year = entry.fields.get("year", "").replace("{", "").replace("}", "").strip()

        # Journal or booktitle
        journal = entry.fields.get("journal") or entry.fields.get("booktitle")
        if journal:
            journal = journal.replace("{", "").replace("}", "").strip()

        entries[key] = BibEntry(
            cite_key=key,
            title=title,
            authors=authors,
            year=year,
            journal=journal,
            entry_type=entry.type,
        )

    return entries


# ---------------------------------------------------------------------------
# \bibitem fallback parsing
# ---------------------------------------------------------------------------

_BIBITEM_PATTERN = re.compile(
    r"\\bibitem\s*(?:\[([^\]]*)\])?\s*\{([^}]+)\}(.*?)(?=\\bibitem|\Z)",
    re.DOTALL,
)

_VENUE_KEYWORDS = re.compile(
    r"(?:Proceedings|Journal|Transactions|Workshop|Conference|Review|Quarterly)",
    re.IGNORECASE,
)


def _parse_bibitem_entry(key: str, body: str) -> BibEntry:
    """Parse a single \\bibitem body into a BibEntry using \\newblock splitting
    with Bug #5 fallback for entries lacking \\newblock."""

    body_clean = body.strip()
    blocks = re.split(r"\\newblock\s*", body_clean)

    title = ""
    authors = ""
    year = ""
    journal = None

    if len(blocks) >= 2:
        # ---- Standard \newblock layout ----
        # Block 0: authors + year
        authors = _extract_authors(blocks[0])
        year = _extract_year(blocks[0])

        # Block 1: title
        title = _clean_text(blocks[1]).rstrip(".")

        # Block 2+: journal / venue
        if len(blocks) >= 3:
            journal = _clean_text(blocks[2]).rstrip(".")
    else:
        # ---- No \newblock — Bug #5 heuristics ----
        year = _extract_year(body_clean)
        authors = _extract_authors(body_clean)

        # Strategy 1: {\em ...} NOT preceded by "In " → likely title
        em_matches = list(re.finditer(r"(?<!In\s)\{\\em\s+([^}]+)\}", body_clean))
        in_em_matches = list(re.finditer(r"In\s+\{\\em\s+([^}]+)\}", body_clean))

        if em_matches and not in_em_matches:
            title = _clean_text(em_matches[0].group(1)).rstrip(".")
        elif in_em_matches:
            # Strategy 2: {\em ...} after "In " → that's the venue;
            # title = text between (YYYY). and "In {\em"
            venue_start = in_em_matches[0].start()
            journal = _clean_text(in_em_matches[0].group(1)).rstrip(".")
            year_m = re.search(r"\(\d{4}\)\.\s*", body_clean)
            if year_m:
                segment = body_clean[year_m.end() : venue_start].strip()
                title = _clean_text(segment).rstrip(".")
            else:
                # desperate fallback
                title = _fallback_title(body_clean, year)
        else:
            # Strategy 3: text between (YYYY). and next period
            title = _fallback_title(body_clean, year)

    return BibEntry(
        cite_key=key,
        title=title or key,
        authors=authors,
        year=year,
        journal=journal,
        entry_type="misc",
    )


def _fallback_title(body: str, year: str) -> str:
    """Fallback: grab text between (YYYY). and the next period."""
    pattern = rf"\({re.escape(year)}\)\.\s*" if year else None
    if pattern:
        m = re.search(pattern, body)
        if m:
            rest = body[m.end():]
            dot = rest.find(".")
            if dot > 0:
                return _clean_text(rest[:dot]).strip()
    return ""


def _needs_llm_title(entry: BibEntry, body: str) -> bool:
    """Decide whether this bibitem entry needs an LLM pass for title extraction."""
    # No \newblock at all
    if "\\newblock" not in body:
        return True
    # Title looks like a venue name
    if _VENUE_KEYWORDS.search(entry.title):
        return True
    # Empty or very short title (likely failed)
    if len(entry.title) < 5:
        return True
    return False


def _parse_bibitems(tex_text: str) -> tuple[dict[str, BibEntry], dict[str, str]]:
    """Parse all \\bibitem entries from LaTeX source.

    Returns:
        entries: dict of parsed BibEntry objects
        raw_bodies: dict mapping cite_key → raw bibitem body text (for LLM fallback)
    """
    entries: dict[str, BibEntry] = {}
    raw_bodies: dict[str, str] = {}

    for m in _BIBITEM_PATTERN.finditer(tex_text):
        key = m.group(2).strip()
        body = m.group(3)
        raw_bodies[key] = body
        entries[key] = _parse_bibitem_entry(key, body)

    return entries, raw_bodies


# ---------------------------------------------------------------------------
# LLM-assisted title extraction (Pass B)
# ---------------------------------------------------------------------------

async def extract_bibitem_titles_with_llm(
    entries_needing_titles: dict[str, str],
    config: dict,
) -> dict[str, str]:
    """Use lightweight LLM to extract titles for bibitem entries that lack \\newblock
    or where the regex-extracted title looks like a venue name.

    Args:
        entries_needing_titles: {cite_key: raw_bibitem_body}
        config: pipeline config dict

    Returns:
        {cite_key: extracted_title}
    """
    from agents import _call_llm  # noqa: E402 — late import to avoid circular deps

    sem = asyncio.Semaphore(20)
    light_config = {**config, "max_tokens": 150}
    results: dict[str, str] = {}

    async def _extract_one(cite_key: str, raw_text: str) -> None:
        async with sem:
            try:
                title = (
                    await _call_llm(
                        system=None,
                        user=(
                            "Extract ONLY the paper title from this bibliography entry. "
                            "The title is the name of the paper, NOT the journal or "
                            "conference name. Return just the title, nothing else.\n\n"
                            f"{raw_text[:500]}"
                        ),
                        config=light_config,
                        light=True,
                    )
                ).strip()
                results[cite_key] = title
            except Exception as exc:
                logger.warning(
                    "LLM title extraction failed for %s, keeping regex result: %s",
                    cite_key,
                    exc,
                )

    tasks = [
        _extract_one(key, body)
        for key, body in entries_needing_titles.items()
    ]
    if tasks:
        await asyncio.gather(*tasks)

    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_bib(tex_path: str | Path, config: dict | None = None) -> dict[str, BibEntry]:
    """Parse bibliography from .bib file or inline \\bibitem entries.

    Args:
        tex_path: path to the main .tex file
        config: pipeline config (needed only for LLM fallback on bibitems)

    Returns:
        dict mapping cite_key → BibEntry
    """
    tex_path = Path(tex_path)
    tex_text = tex_path.read_text(encoding="utf-8", errors="replace")

    # --- Try to locate a .bib file ---
    bib_path = _find_bib_file(tex_text, tex_path.parent)
    if bib_path is not None:
        entries = _parse_bib_file(bib_path)
        if entries:
            logger.info("Parsed %d entries from %s", len(entries), bib_path.name)
            return entries
        logger.warning("pybtex returned 0 entries from %s; falling back to bibitem", bib_path)

    # --- Fallback: parse \bibitem entries ---
    entries, raw_bodies = _parse_bibitems(tex_text)
    if not entries:
        logger.warning("No bib entries found in %s", tex_path)
        return {}

    logger.info("Parsed %d \\bibitem entries from %s", len(entries), tex_path.name)

    # Identify entries that need LLM help
    need_llm = {
        key: raw_bodies[key]
        for key, entry in entries.items()
        if _needs_llm_title(entry, raw_bodies[key])
    }

    return entries


async def resolve_llm_titles(
    entries: dict[str, BibEntry],
    tex_path: str | Path,
    config: dict,
) -> dict[str, BibEntry]:
    """Run LLM title extraction for bibitem entries that need it.

    Call this from an async context after parse_bib().
    """
    tex_path = Path(tex_path)
    tex_text = tex_path.read_text(encoding="utf-8", errors="replace")

    # Re-parse bibitems to get raw bodies
    _, raw_bodies = _parse_bibitems(tex_text)

    need_llm = {
        key: raw_bodies[key]
        for key, entry in entries.items()
        if key in raw_bodies and _needs_llm_title(entry, raw_bodies[key])
    }

    if not need_llm:
        return entries

    logger.info("Running LLM title extraction for %d entries", len(need_llm))
    try:
        llm_titles = await extract_bibitem_titles_with_llm(need_llm, config)
        for key, title in llm_titles.items():
            if title and len(title) > 3:
                entries[key].title = title
    except Exception as exc:
        logger.warning("LLM title extraction batch failed: %s", exc)

    return entries


# ---------------------------------------------------------------------------
# Internal: locate .bib file referenced in the .tex source
# ---------------------------------------------------------------------------

def _find_bib_file(tex_text: str, tex_dir: Path) -> Path | None:
    """Scan for \\bibliography{name} or \\addbibresource{name.bib}."""

    # \bibliography{name} (no extension)
    m = re.search(r"\\bibliography\{([^}]+)\}", tex_text)
    if m:
        names = [n.strip() for n in m.group(1).split(",")]
        for name in names:
            candidate = tex_dir / (name if name.endswith(".bib") else name + ".bib")
            if candidate.exists():
                return candidate

    # \addbibresource{name.bib}
    m = re.search(r"\\addbibresource\{([^}]+)\}", tex_text)
    if m:
        candidate = tex_dir / m.group(1).strip()
        if candidate.exists():
            return candidate

    return None
