"""CV tailoring and cover letter generation via the Claude Code CLI.

Runs `claude -p` (headless print mode) as a subprocess, so generation uses the
user's existing Claude subscription — no Anthropic API key or per-token billing.
"""
from __future__ import annotations

import logging
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Kept for backward-compat with older Settings rows; the CLI picks the user's
# default model, we never pass --model.
DEFAULT_MODEL = "claude-cli"

CALL_TIMEOUT_S = 300  # generous: CLI cold-start + long CV + long posting

CV_SYSTEM = """You are an expert career coach helping a finance student tailor their CV to pass ATS filters for Sales, Trading, and Structuring internships at top investment banks.

You will receive:
1. The candidate's base CV, broken into numbered paragraphs (one per line of the actual CV).
2. A specific job posting.

Your job: produce minimally-edited replacements for each paragraph so the CV:
- Mirrors keywords and phrasings from the job posting (ATS optimization)
- Preserves all factual content (do NOT invent experience, schools, dates, employers)
- Preserves bullet structure and approximate length per paragraph
- Uses the same tone and language as the original

Output STRICT JSON only, no prose, no markdown fences:
{
  "replacements": [
    {"index": 0, "new_text": "..."},
    {"index": 3, "new_text": "..."}
  ]
}

Only include paragraphs you are actually changing. If a paragraph is fine as-is (contact info, education facts, dates), omit it.
Never hallucinate. Never change company names, schools, GPAs, dates, or contact details.
"""

COVER_LETTER_SYSTEM = """You are an expert career coach writing a tailored cover letter for a finance student applying to a Sales, Trading, or Structuring internship at a top investment bank.

Output a polished, professional cover letter in English. Tone: confident, concise, specific. Length: 3-4 short paragraphs.

Structure:
- Opening: state the role, why this bank specifically (1-2 specific reasons), and one-sentence personal motivation.
- Body 1: most relevant experience or coursework, linked explicitly to skills the job posting names.
- Body 2: one quantified or specific accomplishment showing fit (markets interest, technical chops, leadership).
- Closing: short, polite, asks for interview.

Do NOT invent facts. Use only information present in the candidate's CV. Do not include placeholders like [Your Name] — use the candidate's actual name from the CV.

Output plain text only. No markdown, no JSON, no preamble like "Here is the cover letter".
"""


class ClaudeCliError(RuntimeError):
    pass


def _claude_exe() -> str:
    exe = shutil.which("claude")
    if exe:
        return exe
    fallback = Path.home() / ".local" / "bin" / "claude.exe"
    if fallback.exists():
        return str(fallback)
    raise ClaudeCliError(
        "Claude Code CLI not found. Install it or add it to PATH (expected at ~/.local/bin/claude.exe)."
    )


def _run_claude(prompt: str) -> str:
    """Run `claude -p` with the prompt on stdin, return stdout text."""
    exe = _claude_exe()
    # Neutral cwd so the CLI doesn't pick up any project CLAUDE.md context.
    cwd = str(Path(__file__).parent / "storage")
    try:
        proc = subprocess.run(
            [exe, "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CALL_TIMEOUT_S,
            cwd=cwd,
            env={**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
        )
    except subprocess.TimeoutExpired:
        raise ClaudeCliError(f"claude CLI timed out after {CALL_TIMEOUT_S}s")
    if proc.returncode != 0:
        # Usage-limit and auth messages arrive on stdout, not stderr — show both.
        detail = " | ".join(s.strip()[:400] for s in (proc.stdout, proc.stderr) if s and s.strip())
        raise ClaudeCliError(f"claude CLI failed (exit {proc.returncode}): {detail or 'no output'}")
    out = (proc.stdout or "").strip()
    if not out:
        raise ClaudeCliError(f"claude CLI returned empty output; stderr: {proc.stderr.strip()[:500]}")
    return out


def is_usage_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(w in msg for w in ("usage limit", "rate limit", "limit reached", "out of extra usage", "upgrade to"))


def _format_paragraphs(paragraphs: list[str]) -> str:
    return "\n".join(f"[{i}] {p}" for i, p in enumerate(paragraphs))


def _job_block(offer: dict) -> str:
    return (
        f"Bank: {offer.get('bank')}\n"
        f"Role: {offer.get('role_title')}\n"
        f"Location: {offer.get('location')}\n"
        f"Program type: {offer.get('program_type')}\n"
        f"Start: {offer.get('start_date_raw') or 'not specified'}\n"
        f"\nDescription:\n{offer.get('description') or '(no description provided — infer from role + bank)'}"
    )


def tailor_cv_paragraphs(
    base_cv_paragraphs: list[str],
    offer: dict,
    **_ignored,  # api_key/model kept out of the signature; absorb legacy kwargs
) -> dict[int, str]:
    """Return {paragraph_index: new_text} for the paragraphs that should change."""
    prompt = (
        f"{CV_SYSTEM}\n\n"
        f"BASE CV PARAGRAPHS:\n{_format_paragraphs(base_cv_paragraphs)}\n\n"
        f"Tailor for this job:\n\n{_job_block(offer)}\n\nReturn JSON only."
    )
    text = _run_claude(prompt)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        logger.warning("No JSON in CV tailoring response: %s", text[:300])
        return {}
    try:
        # strict=False: the model sometimes emits literal newlines inside JSON
        # strings (multi-line CV bullets) — accept them instead of failing.
        parsed = json.loads(match.group(0), strict=False)
    except json.JSONDecodeError as e:
        logger.warning("CV tailoring JSON parse failed: %s", e)
        return {}
    out: dict[int, str] = {}
    for r in parsed.get("replacements", []):
        idx = r.get("index")
        new_text = r.get("new_text")
        if isinstance(idx, int) and isinstance(new_text, str):
            out[idx] = new_text
    return out


def generate_cover_letter(
    base_cv_text: str,
    offer: dict,
    base_cover_letter_text: str | None = None,
    **_ignored,
) -> str:
    template_block = (
        f"\n\nCANDIDATE'S OWN COVER LETTER TEMPLATE (match its voice and reuse its strongest lines where relevant):\n"
        f"{base_cover_letter_text}"
        if base_cover_letter_text else ""
    )
    prompt = (
        f"{COVER_LETTER_SYSTEM}\n\n"
        f"CANDIDATE CV:\n{base_cv_text}"
        f"{template_block}\n\n"
        f"Write the cover letter for this job:\n\n{_job_block(offer)}"
    )
    return _run_claude(prompt)
