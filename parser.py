"""
parser.py — Extract citation groups from a LaTeX file.

Handles all common citation commands (citep, citet, cite, parencite, textcite,
autocite, fullcite) including starred variants and optional arguments.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class CitationGroup:
    cite_keys: list[str]
    sentence: str               # Raw LaTeX sentence
    clean_sentence: str         # Cleaned for display
    paragraph: str
    line_number: int            # 1-indexed
    group_id: str               # "group_{line}" or "group_{line}_{col}" if multiple cites on same line


# ---------------------------------------------------------------------------
# Citation regex — handles 0, 1, or 2 optional [...] args (Bug #1 fix)
# ---------------------------------------------------------------------------

CITE_PATTERN = (
    r'\\(?:citep|citet|cite|parencite|textcite|autocite|fullcite)'
    r'\*?(?:\[[^\]]*\])*\{([^}]+)\}'
)

# ---------------------------------------------------------------------------
# Abbreviations that end with '.' but do NOT signal sentence boundaries
# ---------------------------------------------------------------------------

_ABBREVIATIONS = [
    'et al.', 'e.g.', 'i.e.', 'Fig.', 'Eq.', 'vs.', 'pp.', 'Dr.',
    'Prof.', 'cf.', 'resp.', 'vol.', 'no.', 'ed.', 'eds.', 'Ref.', 'Sec.',
]

# Build a negative-lookbehind-friendly set of escaped abbreviation endings.
# We check: is the period we found preceded by one of these abbreviation stems?
_ABBREV_STEMS = [a[:-1] for a in _ABBREVIATIONS]  # everything before the trailing '.'

# ---------------------------------------------------------------------------
# Hard boundaries — always terminate sentence scanning
# ---------------------------------------------------------------------------

_HARD_BOUNDARIES = [
    r'\\section\{',
    r'\\subsection\{',
    r'\\subsubsection\{',
    r'\\paragraph\{',
    r'\\begin\{',
    r'\\end\{',
    r'\\item',
    r'\n\n',
]

_HARD_BOUNDARY_RE = re.compile('|'.join(_HARD_BOUNDARIES))

# ---------------------------------------------------------------------------
# Pre-processing
# ---------------------------------------------------------------------------

def strip_comments(text: str) -> str:
    """Remove LaTeX comments (% to end of line), preserving \\% escapes."""
    return re.sub(r'(?<!\\)%.*$', '', text, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# Sentence extraction helpers
# ---------------------------------------------------------------------------

def _is_abbreviation(text: str, dot_pos: int) -> bool:
    """Return True if the '.' at *dot_pos* in *text* is part of a known abbreviation."""
    for stem in _ABBREV_STEMS:
        start = dot_pos - len(stem)
        if start >= 0 and text[start:dot_pos] == stem:
            return True
    return False


def _is_soft_boundary(text: str, pos: int) -> bool:
    """Check if position is a soft sentence boundary (.!? followed by space/newline)."""
    if pos < 0 or pos >= len(text):
        return False
    ch = text[pos]
    if ch not in '.!?':
        return False
    # Must be followed by whitespace (space, newline) or end-of-string
    if pos + 1 < len(text) and text[pos + 1] not in (' ', '\n', '\r', '\t'):
        return False
    # Check abbreviation exception (only for '.')
    if ch == '.' and _is_abbreviation(text, pos):
        return False
    return True


def _find_hard_boundary_backward(text: str, start: int) -> int:
    """Scan backward from *start* for the nearest hard boundary. Return its end position or -1."""
    # We search the substring text[:start] for hard boundaries, return the rightmost one.
    best = -1
    for m in _HARD_BOUNDARY_RE.finditer(text, 0, start):
        end = m.end()
        if end <= start:
            best = end
    return best


def _find_hard_boundary_forward(text: str, start: int) -> int:
    """Scan forward from *start* for the nearest hard boundary. Return its start position or len(text)."""
    m = _HARD_BOUNDARY_RE.search(text, start)
    if m:
        return m.start()
    return len(text)


def _extract_sentence(clean_text: str, match_start: int, match_end: int) -> str:
    """Extract the sentence containing the citation match from clean_text."""
    # --- Scan backward for sentence start ---
    sent_start = 0
    # Check hard boundaries first
    hard_back = _find_hard_boundary_backward(clean_text, match_start)

    # Scan backward for soft boundary (.!? + space)
    soft_back = -1
    pos = match_start - 1
    while pos >= 0:
        if _is_soft_boundary(clean_text, pos):
            soft_back = pos + 1  # sentence starts after the punctuation
            break
        pos -= 1

    # Take the closest (rightmost) boundary
    candidates = [c for c in [hard_back, soft_back] if c >= 0]
    if candidates:
        sent_start = max(candidates)

    # --- Scan forward for sentence end ---
    sent_end = len(clean_text)
    # Hard boundary forward
    hard_fwd = _find_hard_boundary_forward(clean_text, match_end)

    # Soft boundary forward
    soft_fwd = len(clean_text)
    pos = match_end
    while pos < len(clean_text):
        if _is_soft_boundary(clean_text, pos):
            soft_fwd = pos + 1  # include the punctuation
            break
        pos += 1

    sent_end = min(hard_fwd, soft_fwd)

    sentence = clean_text[sent_start:sent_end].strip()
    return sentence


# ---------------------------------------------------------------------------
# Paragraph extraction
# ---------------------------------------------------------------------------

def _extract_paragraph(clean_text: str, match_start: int) -> str:
    """Extract the paragraph (blank-line delimited) containing position match_start."""
    # Find blank line before
    para_start = clean_text.rfind('\n\n', 0, match_start)
    para_start = para_start + 2 if para_start != -1 else 0

    # Find blank line after
    para_end = clean_text.find('\n\n', match_start)
    para_end = para_end if para_end != -1 else len(clean_text)

    return clean_text[para_start:para_end].strip()


# ---------------------------------------------------------------------------
# Clean sentence generation
# ---------------------------------------------------------------------------

def _clean_sentence(sentence: str) -> str:
    """Produce a human-readable version of a raw LaTeX sentence."""
    cleaned = sentence

    # Strip \footnote{...} content entirely
    cleaned = re.sub(r'\\footnote\{[^}]*\}', '', cleaned)

    # Strip all \cite*{...} commands (including optional args)
    cleaned = re.sub(
        r'\\(?:citep|citet|cite|parencite|textcite|autocite|fullcite)'
        r'\*?(?:\[[^\]]*\])*\{[^}]*\}',
        '', cleaned
    )

    # Strip LaTeX escapes
    cleaned = cleaned.replace(r'\%', '%')
    cleaned = cleaned.replace(r'\&', '&')
    cleaned = cleaned.replace(r'\$', '$')
    cleaned = cleaned.replace(r'\#', '#')
    cleaned = cleaned.replace(r'\_', '_')

    # Strip formatting commands: \textit{X} → X, \textbf{X} → X, \emph{X} → X
    cleaned = re.sub(r'\\(?:textit|textbf|emph)\{([^}]*)\}', r'\1', cleaned)

    # Convert typography
    cleaned = cleaned.replace('---', '\u2014')
    cleaned = cleaned.replace('``', '\u201c')
    cleaned = cleaned.replace("''", '\u201d')

    # Collapse extra whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------

def parse_citations(tex_path: str | Path, config=None) -> list[CitationGroup]:
    """
    Parse a LaTeX file and return a list of CitationGroup objects,
    sorted by line number.
    """
    tex_path = Path(tex_path)
    raw_text = tex_path.read_text(encoding='utf-8')

    # Pre-process: strip comments for clean scanning text
    clean_text = strip_comments(raw_text)

    # Split original text into lines for line-by-line processing
    original_lines = raw_text.splitlines()

    # ------------------------------------------------------------------
    # FIRST PASS: count citation matches per line
    # ------------------------------------------------------------------
    matches_per_line: dict[int, list[tuple[int, re.Match]]] = {}

    for line_idx, orig_line in enumerate(original_lines):
        line_num = line_idx + 1  # 1-indexed

        # Skip entirely-comment lines in original
        stripped = orig_line.lstrip()
        if stripped.startswith('%'):
            continue

        # Use the corresponding cleaned line for matching
        clean_lines = clean_text.splitlines()
        if line_idx < len(clean_lines):
            search_line = clean_lines[line_idx]
        else:
            continue

        for m in re.finditer(CITE_PATTERN, search_line):
            if line_num not in matches_per_line:
                matches_per_line[line_num] = []
            matches_per_line[line_num].append((m.start(), m))

    # ------------------------------------------------------------------
    # Build a mapping from (line_idx, col) in line-based coords to
    # absolute position in clean_text, for sentence extraction.
    # ------------------------------------------------------------------
    # Precompute line start offsets in clean_text
    _clean_lines = clean_text.splitlines(keepends=True)
    _line_offsets: list[int] = []
    offset = 0
    for cl in _clean_lines:
        _line_offsets.append(offset)
        offset += len(cl)

    # ------------------------------------------------------------------
    # SECOND PASS: build CitationGroup objects
    # ------------------------------------------------------------------
    groups: list[CitationGroup] = []

    for line_num, match_list in sorted(matches_per_line.items()):
        multi = len(match_list) > 1
        line_idx = line_num - 1

        for col, m in match_list:
            # Extract cite keys
            keys_raw = m.group(1)
            cite_keys = [k.strip() for k in keys_raw.split(',')]

            # Compute absolute position in clean_text
            if line_idx < len(_line_offsets):
                abs_start = _line_offsets[line_idx] + m.start()
                abs_end = _line_offsets[line_idx] + m.end()
            else:
                abs_start = 0
                abs_end = 0

            # Extract sentence
            sentence = _extract_sentence(clean_text, abs_start, abs_end)

            # Extract paragraph
            paragraph = _extract_paragraph(clean_text, abs_start)

            # Clean sentence
            clean_sent = _clean_sentence(sentence)

            # Generate group_id (Bug #7 fix: two-pass approach)
            if multi:
                group_id = f"group_{line_num}_{col}"
            else:
                group_id = f"group_{line_num}"

            groups.append(CitationGroup(
                cite_keys=cite_keys,
                sentence=sentence,
                clean_sentence=clean_sent,
                paragraph=paragraph,
                line_number=line_num,
                group_id=group_id,
            ))

    # Sort by line number (stable — preserves column order within a line)
    groups.sort(key=lambda g: g.line_number)

    return groups


# ---------------------------------------------------------------------------
# LLM-based sentence cleaning
# ---------------------------------------------------------------------------

async def clean_sentences_with_llm(
    groups: list[CitationGroup],
    config,
) -> list[CitationGroup]:
    """
    Use a lightweight LLM call to extract the actual claiming sentence
    from noisy LaTeX context. Falls back to regex-cleaned sentence on failure.
    """
    from agents import _call_llm  # Import here to avoid circular imports

    semaphore = asyncio.Semaphore(20)

    async def _clean_one(group: CitationGroup) -> CitationGroup:
        prompt = (
            "Extract the single sentence that makes a claim supported by the "
            "citation from the following LaTeX text. Return ONLY the cleaned "
            "sentence with no LaTeX commands, no citation markers, and no "
            "extra commentary.\n\n"
            f"LaTeX text:\n{group.sentence}"
        )
        async with semaphore:
            try:
                result = await _call_llm(system=None, user=prompt, config=config, light=True)
                if result and isinstance(result, str) and len(result.strip()) > 10:
                    group.clean_sentence = result.strip()
            except Exception as e:
                logger.warning(
                    "LLM clean failed for %s, using regex fallback: %s",
                    group.group_id, e,
                )
                # Keep the regex-based clean_sentence as fallback
        return group

    tasks = [_clean_one(g) for g in groups]
    updated = await asyncio.gather(*tasks)
    return list(updated)
