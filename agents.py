"""
agents.py – LLM-based citation verification and caching utilities.

Provides:
    - VerificationResult dataclass
    - _call_llm() for provider-agnostic async LLM calls (Anthropic / OpenAI)
    - verify_citation() for verifying a single (group, cite_key) pair
    - Cache utilities: compute_hash, load_cache, append_to_cache, make_missing_result
"""

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    group_id: str
    cite_key: str
    sentence: str              # Clean sentence (no LaTeX cite commands)
    match_found: bool
    confidence: str            # "high" | "medium" | "low" | "none"
    verified: bool             # True if confidence == "high"
    supporting_passage: str
    page_number: int | None
    page_range: str | None
    section: str | None
    relationship: str
    notes: str
    error: str | None
    sentence_hash: str


# ---------------------------------------------------------------------------
# Provider-agnostic async LLM call
# ---------------------------------------------------------------------------

async def _call_llm(
    system: str | None,
    user: str,
    config: dict,
    light: bool = False,
) -> str:
    """Call an LLM via Anthropic or OpenAI async client.

    Parameters
    ----------
    system : str
        System prompt.
    user : str
        User prompt.
    config : dict
        Must contain keys: provider, model, light_model, timeout, max_tokens.
    light : bool
        If True, use config["light_model"] instead of config["model"].

    Returns
    -------
    str
        The assistant's text response.
    """
    model = config["light_model"] if light else config["model"]
    provider = config["provider"]
    timeout = config.get("timeout", 120)
    max_tokens = config.get("max_tokens", 4096)

    if provider == "anthropic":
        import anthropic

        client = anthropic.AsyncAnthropic()
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user}],
        )
        if system:
            kwargs["system"] = system
        coro = client.messages.create(**kwargs)
        response = await asyncio.wait_for(coro, timeout=timeout)
        return response.content[0].text

    elif provider == "openai":
        import openai

        client = openai.AsyncOpenAI()
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": user})
        coro = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=msgs,
        )
        response = await asyncio.wait_for(coro, timeout=timeout)
        return response.choices[0].message.content

    else:
        raise ValueError(f"Unsupported provider: {provider}")


# ---------------------------------------------------------------------------
# Citation verification
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are an academic citation verification assistant. Your task is to "
    "determine whether a cited paper supports the claim made in the citing "
    "sentence. You will be given the claim, surrounding context, bibliographic "
    "information about the cited paper, and the full text of the cited paper. "
    "Respond ONLY with a JSON object (no markdown fences, no commentary)."
)


def _build_user_prompt(
    clean_sentence: str,
    paragraph: str,
    bib_entry,
    stitched_text: str,
) -> str:
    """Assemble the user prompt for verify_citation."""
    # Bibliographic info
    if bib_entry is not None:
        bib_info = (
            f"Authors: {bib_entry.authors}\n"
            f"Year: {bib_entry.year}\n"
            f"Title: {bib_entry.title}"
        )
    else:
        bib_info = "Bibliographic information not available."

    return (
        f"## Claim\n{clean_sentence}\n\n"
        f"## Surrounding context\n{paragraph}\n\n"
        f"## Cited paper information\n{bib_info}\n\n"
        f"## Full text of cited paper\n"
        f"(Page breaks are indicated by «p.N» markers.)\n\n"
        f"{stitched_text}\n\n"
        f"## Instructions\n"
        f"Search the full text above for evidence that supports or relates to "
        f"the claim. Return a JSON object with these fields:\n"
        f"- match_found (bool): whether supporting evidence was found\n"
        f"- confidence (str): \"high\", \"medium\", \"low\", or \"none\"\n"
        f"- supporting_passage (str): the most relevant passage from the text "
        f"(quote directly), or empty string if none\n"
        f"- page_number (int or null): page where the best evidence appears\n"
        f"- page_range (str or null): range of pages with relevant content, "
        f'e.g. "3-5"\n'
        f"- section (str or null): section title where evidence appears\n"
        f"- relationship (str): brief description of how the cited paper "
        f"relates to the claim\n"
        f"- notes (str): any caveats, qualifications, or additional context\n\n"
        f"When referencing other papers in 'relationship' or 'notes', "
        f"use \\citep{{key}} with the citation key. Do not use any other "
        f"LaTeX commands — only \\citep and \\citet are allowed.\n"
        f"Use LaTeX-style quotes: `` for opening double quote, '' for closing "
        f"double quote, ` for opening single quote, ' for closing single quote."
    )


def _strip_markdown_fences(text: str) -> str:
    """Remove leading/trailing markdown code fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (with optional language tag)
        first_newline = text.index("\n") if "\n" in text else len(text)
        text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _fix_json_escapes(text: str) -> str:
    """Fix invalid JSON escape sequences from LLM output (e.g. \\citep, \\S).

    JSON only allows: \\", \\\\, \\/, \\b, \\f, \\n, \\r, \\t, \\uXXXX.
    Any other \\X is invalid. We double the backslash so it becomes a literal \\.
    """
    valid_escapes = set('"\\/bfnrtu')
    result = []
    i = 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text):
            next_ch = text[i + 1]
            if next_ch in valid_escapes:
                result.append(text[i])
                result.append(next_ch)
                i += 2
            else:
                # Invalid escape — double the backslash
                result.append('\\\\')
                i += 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)


async def verify_citation(
    group,
    key: str,
    pdf_data: dict,
    bib_entry,
    config: dict,
) -> VerificationResult:
    """Verify a single (group, cite_key) pair against PDF text.

    Parameters
    ----------
    group
        Duck-typed object with .clean_sentence, .paragraph, .group_id.
    key : str
        Citation key.
    pdf_data : dict
        Must contain ``"stitched_text"`` key with the full PDF text.
    bib_entry
        Duck-typed object with .authors, .year, .title (or None).
    config : dict
        LLM configuration dict.

    Returns
    -------
    VerificationResult
    """
    clean_sentence = group.clean_sentence
    sentence_hash = compute_hash(key, clean_sentence)
    stitched_text = pdf_data["stitched_text"]

    user_prompt = _build_user_prompt(
        clean_sentence, group.paragraph, bib_entry, stitched_text
    )

    last_error: Exception | None = None

    for attempt in range(3):
        try:
            raw = await _call_llm(_SYSTEM_PROMPT, user_prompt, config)
            raw = _strip_markdown_fences(raw)
            raw = _fix_json_escapes(raw)
            data = json.loads(raw)

            confidence = data.get("confidence", "none")
            return VerificationResult(
                group_id=group.group_id,
                cite_key=key,
                sentence=clean_sentence,
                match_found=bool(data.get("match_found", False)),
                confidence=confidence,
                verified=(confidence == "high"),
                supporting_passage=data.get("supporting_passage", ""),
                page_number=data.get("page_number"),
                page_range=data.get("page_range"),
                section=data.get("section"),
                relationship=data.get("relationship", ""),
                notes=data.get("notes", ""),
                error=None,
                sentence_hash=sentence_hash,
            )

        except TimeoutError:
            last_error = TimeoutError("LLM call timed out")
            await asyncio.sleep(2 ** (attempt + 1))

        except Exception as exc:
            last_error = exc
            # Detect rate-limit errors from either provider
            is_rate_limit = False
            exc_str = str(exc)
            if "429" in exc_str:
                is_rate_limit = True
            # Anthropic raises anthropic.RateLimitError
            if type(exc).__name__ == "RateLimitError":
                is_rate_limit = True

            if is_rate_limit:
                await asyncio.sleep(2 ** (attempt + 2))
            else:
                # Non-retryable error on first two attempts still retry
                await asyncio.sleep(2 ** (attempt + 1))

    # All retries exhausted
    return VerificationResult(
        group_id=group.group_id,
        cite_key=key,
        sentence=clean_sentence,
        match_found=False,
        confidence="none",
        verified=False,
        supporting_passage="",
        page_number=None,
        page_range=None,
        section=None,
        relationship="",
        notes="",
        error=str(last_error),
        sentence_hash=sentence_hash,
    )


# ---------------------------------------------------------------------------
# Cache utilities
# ---------------------------------------------------------------------------

def compute_hash(cite_key: str, clean_sentence: str) -> str:
    """SHA-256 hash of ``cite_key|clean_sentence``, truncated to 16 hex chars."""
    digest = hashlib.sha256(f"{cite_key}|{clean_sentence}".encode()).hexdigest()
    return digest[:16]


def _cache_path(output_dir: str) -> Path:
    return Path(output_dir) / "additional_output" / "verification.json"


def _dict_to_result(d: dict) -> VerificationResult:
    """Reconstruct a VerificationResult from a cache dict."""
    r = d.get("result", d)
    return VerificationResult(
        group_id=r.get("group_id", ""),
        cite_key=r.get("cite_key", ""),
        sentence=r.get("sentence", ""),
        match_found=bool(r.get("match_found", False)),
        confidence=r.get("confidence", "none"),
        verified=bool(r.get("verified", False)),
        supporting_passage=r.get("supporting_passage", ""),
        page_number=r.get("page_number"),
        page_range=r.get("page_range"),
        section=r.get("section"),
        relationship=r.get("relationship", ""),
        notes=r.get("notes", ""),
        error=r.get("error"),
        sentence_hash=r.get("sentence_hash", ""),
    )


def load_cache(output_dir: str) -> dict:
    """Load the verification cache, returning a dict keyed by sentence_hash.

    Values are VerificationResult objects. Returns an empty dict if the
    cache file doesn't exist or is malformed.
    """
    path = _cache_path(output_dir)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_entries = data.get("entries", {})
        return {k: _dict_to_result(v) for k, v in raw_entries.items()}
    except (json.JSONDecodeError, KeyError):
        return {}


def append_to_cache(result: VerificationResult, output_dir: str) -> None:
    """Add or update a verification result in the cache file."""
    path = _cache_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, KeyError):
            data = {"version": 1, "entries": {}}
    else:
        data = {"version": 1, "entries": {}}

    data.setdefault("version", 1)
    data.setdefault("entries", {})

    data["entries"][result.sentence_hash] = {
        "sentence_hash": result.sentence_hash,
        "group_id": result.group_id,
        "cite_key": result.cite_key,
        "result": asdict(result),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def make_missing_result(group, key: str) -> VerificationResult:
    """Create a VerificationResult for a citation whose PDF is unavailable."""
    clean_sentence = group.clean_sentence
    return VerificationResult(
        group_id=group.group_id,
        cite_key=key,
        sentence=clean_sentence,
        match_found=False,
        confidence="none",
        verified=False,
        supporting_passage="",
        page_number=None,
        page_range=None,
        section=None,
        relationship="",
        notes="",
        error="missing_pdf",
        sentence_hash=compute_hash(key, clean_sentence),
    )
