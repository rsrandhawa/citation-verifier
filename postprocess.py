"""
postprocess.py – LaTeX appendix generation and source patching.

Standalone module: receives all data as arguments, no imports from other
project modules.

Provides:
    - escape_latex(text)       – escape LaTeX special characters
    - build_appendix(grouped_results, bib_entries, output_dir) – write appendix.tex
    - patch_tex(src_tex, entry_map, groups, output_dir) – write main_verified.tex
"""

import re
from pathlib import Path

# Same regex the parser uses for citation commands
CITE_PATTERN = (
    r'\\(?:citep|citet|cite|parencite|textcite|autocite|fullcite)'
    r'\*?(?:\[[^\]]*\])*\{([^}]+)\}'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CITE_CMD_RE = re.compile(
    r'\\(?:citep|citet|cite|parencite|textcite|autocite)\*?'
    r'(?:\[[^\]]*\])*\{[^}]+\}'
)


def escape_latex(text: str) -> str:
    """Escape LaTeX special characters in *text*, preserving citation commands.

    Order matters: backslash must be replaced first.
    """
    # Pull out citation commands before escaping, replace with placeholders
    citations: list[str] = []
    def _save_cite(m):
        citations.append(m.group(0))
        return f'\x00CITE{len(citations) - 1}\x00'
    text = _CITE_CMD_RE.sub(_save_cite, text)

    text = text.replace('\\', '\\textbackslash{}')
    for char in '&%$#_{}':
        text = text.replace(char, f'\\{char}')
    text = text.replace('~', '\\textasciitilde{}')
    text = text.replace('^', '\\textasciicircum{}')

    # Convert straight/unicode quotes to proper LaTeX quotes
    text = text.replace('\u201c', '``')   # left double "
    text = text.replace('\u201d', "''")    # right double "
    text = text.replace('\u2018', '`')     # left single '
    text = text.replace('\u2019', "'")     # right single '
    # Straight double quotes: naive open/close alternation
    parts = text.split('"')
    if len(parts) > 1:
        result = parts[0]
        for i, part in enumerate(parts[1:], 1):
            result += ('``' if i % 2 == 1 else "''") + part
        text = result

    # Straight single quotes: opening if preceded by space/start, else closing/apostrophe
    text = re.sub(r"(?<=\s)'|^'", '`', text)

    # Restore citation commands
    for i, cite in enumerate(citations):
        text = text.replace(f'\x00CITE{i}\x00', cite)

    return text


def _attr(obj, key, default=None):
    """Read *key* from *obj* whether it is a dict or has attributes."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _parse_line_from_group_id(group_id: str) -> int:
    """Extract the line number from a group_id like 'group_42' or 'group_42_10'."""
    m = re.match(r'group_(\d+)', group_id)
    return int(m.group(1)) if m else 0


def _short_author(bib_entry) -> str:
    """Return 'LastName (Year)' for a bib entry, or the raw key."""
    authors = _attr(bib_entry, 'authors', '')
    year = _attr(bib_entry, 'year', '')
    if authors:
        # Take the first author's last name
        first = authors.split(',')[0].split(' and ')[0].strip()
        # If "Last, First" format, take the first part
        last = first.split(',')[0].strip()
        return f"{last} ({year})" if year else last
    return ''


# ---------------------------------------------------------------------------
# build_appendix
# ---------------------------------------------------------------------------

def build_appendix(
    grouped_results: dict,
    bib_entries: dict,
    output_dir: str,
) -> dict:
    """Write ``appendix.tex`` and return ``{group_id: entry_number}``.

    Parameters
    ----------
    grouped_results : dict[str, list[VerificationResult]]
        Keyed by group_id.  Each value is a list of results (one per cite key).
    bib_entries : dict
        Keyed by cite_key.  Values are duck-typed bib entry objects / dicts.
    output_dir : str | Path
        Directory where ``appendix.tex`` will be written.

    Returns
    -------
    dict[str, int]
        Mapping from group_id to sequential entry number.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Sort groups by line number parsed from group_id
    sorted_ids = sorted(
        grouped_results.keys(),
        key=_parse_line_from_group_id,
    )

    # Assign sequential numbers
    entry_map: dict[str, int] = {}
    for idx, gid in enumerate(sorted_ids, start=1):
        entry_map[gid] = idx

    # Tallies
    total = len(sorted_ids)
    verified_count = 0
    review_count = 0
    error_count = 0

    lines: list[str] = []

    # Preamble
    lines.append('\\newpage')
    lines.append('\\appendix')
    lines.append('\\section{Citation Verification Appendix}')
    lines.append('')
    lines.append('Status indicators: '
                 '\\textcolor{green!60!black}{\\textbf{Verified}} = '
                 'high-confidence match found; '
                 '\\textcolor{red}{\\textbf{Needs Review}} = '
                 'no high-confidence match.')
    lines.append('')

    for gid in sorted_ids:
        results = grouped_results[gid]
        entry_num = entry_map[gid]
        line_num = _parse_line_from_group_id(gid)

        # Build subsection heading from bib entries
        heading_parts: list[str] = []
        for r in results:
            key = _attr(r, 'cite_key', '')
            bib = bib_entries.get(key)
            if bib is not None:
                heading_parts.append(_short_author(bib))
            else:
                heading_parts.append(escape_latex(key))

        heading = ', '.join(heading_parts)

        # Light rule between entries (skip before the first)
        if entry_num > 1:
            lines.append('\\medskip')
            lines.append('\\noindent\\rule{\\textwidth}{0.2pt}')
            lines.append('\\medskip')

        lines.append(f'\\subsection*{{[{entry_num}] {heading}}}')
        lines.append(f'\\hypertarget{{appendix:{gid}}}{{}}')
        lines.append('')

        # Back-to-text link with tex line reference
        lines.append(
            f'\\noindent '
            f'\\hyperlink{{claim:{gid}}}'
            f'{{\\textit{{$\\leftarrow$ back to text}}}}'
            f'\\hfill \\textcolor{{gray}}{{tex: line {line_num}}}'
        )
        lines.append('\\smallskip')
        lines.append('')

        # Claim in paper (use the sentence from the first result)
        sentence = _attr(results[0], 'sentence', '')
        lines.append('\\begin{quote}')
        lines.append(escape_latex(sentence))
        lines.append('\\end{quote}')

        multi = len(results) > 1

        for idx, r in enumerate(results):
            error = _attr(r, 'error')
            confidence = _attr(r, 'confidence', 'none')
            match_found = _attr(r, 'match_found', False)
            supporting = _attr(r, 'supporting_passage', '')
            relationship = _attr(r, 'relationship', '')
            notes = _attr(r, 'notes', '')
            page_number = _attr(r, 'page_number')
            page_range = _attr(r, 'page_range')
            section = _attr(r, 'section')
            cite_key = _attr(r, 'cite_key', '')

            label = f'({chr(ord("a") + idx)}) ' if multi else ''

            if error:
                error_count += 1 if idx == 0 or multi else 0
                lines.append(
                    f'\\fcolorbox{{red}}{{red!5}}{{\\parbox{{\\dimexpr\\textwidth-2\\fboxsep-2\\fboxrule}}'
                    f'{{{label}\\textbf{{Error:}} {escape_latex(str(error))}}}}}'
                )
                lines.append('')
                continue

            # --- Status banner ---
            if idx > 0:
                lines.append('\\medskip')

            if confidence == 'high':
                if idx == 0:
                    verified_count += 1
                lines.append(
                    f'\\noindent\\fcolorbox{{green!60!black}}{{green!5}}{{\\parbox{{\\dimexpr\\textwidth-2\\fboxsep-2\\fboxrule}}'
                    f'{{{label}\\textcolor{{green!60!black}}{{\\textbf{{Verified}}}}}}}}'
                )
            else:
                if idx == 0 and not multi:
                    review_count += 1
                elif multi and idx == 0:
                    pass
                lines.append(
                    f'\\noindent\\fcolorbox{{red}}{{red!5}}{{\\parbox{{\\dimexpr\\textwidth-2\\fboxsep-2\\fboxrule}}'
                    f'{{{label}\\textcolor{{red}}{{\\textbf{{Needs Review}}}}}}}}'
                )

            lines.append('\\smallskip')

            if not match_found:
                lines.append(
                    f'\\noindent No matching passage found in '
                    f'\\texttt{{{escape_latex(cite_key)}}}.'
                )
                lines.append('')
                continue

            # --- Supporting passage with page info ---
            if supporting:
                detail_parts: list[str] = []
                if page_range is not None:
                    detail_parts.append(f'pp.\\,{page_range}')
                elif page_number is not None:
                    detail_parts.append(f'p.\\,{page_number}')
                if section:
                    detail_parts.append(f'\\S\\,{escape_latex(section)}')
                location = f' ({", ".join(detail_parts)})' if detail_parts else ''

                lines.append('')
                lines.append(f'\\noindent \\textbf{{Text Identified:}}')
                lines.append('\\begin{quote}')
                lines.append(f'\\textit{{{escape_latex(supporting)}}}{location}')
                lines.append('\\end{quote}')
            else:
                # Page info without passage
                detail_parts = []
                if page_range is not None:
                    detail_parts.append(f'pp.\\,{page_range}')
                elif page_number is not None:
                    detail_parts.append(f'p.\\,{page_number}')
                if section:
                    detail_parts.append(f'\\S\\,{escape_latex(section)}')
                if detail_parts:
                    lines.append(f'\\noindent ({", ".join(detail_parts)})')

            # --- Relationship & Notes ---
            if relationship:
                lines.append('')
                lines.append('\\smallskip')
                lines.append(f'\\noindent \\textbf{{Relationship:}} {escape_latex(relationship)}')
            if notes:
                lines.append('')
                lines.append('\\smallskip')
                lines.append(f'\\noindent \\textbf{{Notes:}} {escape_latex(notes)}')

            lines.append('')

        # For multi-cite groups, tally at group level
        if multi:
            all_high = all(
                _attr(r, 'confidence') == 'high'
                for r in results
                if not _attr(r, 'error')
            )
            any_error = any(_attr(r, 'error') for r in results)
            if any_error:
                error_count += 1
            elif all_high:
                verified_count += 1
            else:
                review_count += 1

    # Summary
    lines.append('\\bigskip')
    lines.append('\\noindent\\rule{\\textwidth}{0.4pt}')
    lines.append('')
    lines.append(
        f'\\textbf{{Summary:}} {total} groups, '
        f'{verified_count} verified, '
        f'{review_count} needs review, '
        f'{error_count} errors.'
    )

    # Write file
    tex_path = output_dir / 'appendix.tex'
    tex_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    return entry_map


# ---------------------------------------------------------------------------
# patch_tex
# ---------------------------------------------------------------------------

def patch_tex(
    src_tex: str,
    entry_map: dict,
    groups: dict,
    output_dir: str,
) -> None:
    """Patch the original ``.tex`` source with hyperlinks and write output.

    Parameters
    ----------
    src_tex : str
        Full text of the original .tex file.
    entry_map : dict[str, int]
        Mapping from group_id to sequential entry number (from build_appendix).
    groups : dict
        Keyed by group_id.  Used only to confirm group existence.
    output_dir : str | Path
        Directory where ``main_verified.tex`` will be written.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cite_re = re.compile(CITE_PATTERN)
    # Full-match pattern (we need start/end positions of entire command)
    cite_full_re = re.compile(
        r'\\(?:citep|citet|cite|parencite|textcite|autocite|fullcite)'
        r'\*?(?:\[[^\]]*\])*\{[^}]+\}'
    )

    out_lines: list[str] = []
    src_lines = src_tex.split('\n')

    # First pass: count matches per line (mirrors parser.py two-pass logic)
    matches_per_line: dict[int, list] = {}
    for line_idx, line in enumerate(src_lines):
        line_num = line_idx + 1
        mlist = list(cite_full_re.finditer(line))
        if mlist:
            matches_per_line[line_num] = mlist

    for line_idx, line in enumerate(src_lines):
        line_num = line_idx + 1

        if line_num not in matches_per_line:
            out_lines.append(line)
            continue

        matches = matches_per_line[line_num]
        multi = len(matches) > 1

        # Process right-to-left to preserve column offsets
        for m in reversed(matches):
            col = m.start()  # 0-based column to match parser
            # Build group_id matching parser.py format
            if multi:
                group_id = f'group_{line_num}_{col}'
            else:
                group_id = f'group_{line_num}'

            if group_id not in entry_map:
                continue

            entry_num = entry_map[group_id]

            # Insert hyperlink AFTER the cite command
            after = (
                f'\\hyperlink{{appendix:{group_id}}}'
                f'{{\\textsuperscript{{\\textcolor{{blue}}{{[{entry_num}]}}}}}}'
            )
            # Insert hypertarget BEFORE the cite command
            before = f'\\hypertarget{{claim:{group_id}}}{{}}'

            end = m.end()
            start = m.start()
            line = line[:end] + after + line[end:]
            line = line[:start] + before + line[start:]

        out_lines.append(line)

    # Insert \input{appendix} before \end{document}
    final_lines: list[str] = []
    inserted = False
    for line in out_lines:
        if not inserted and line.strip() == '\\end{document}':
            final_lines.append('\\input{appendix}')
            inserted = True
        final_lines.append(line)

    out_path = output_dir / 'main_verified.tex'
    out_path.write_text('\n'.join(final_lines) + '\n', encoding='utf-8')
