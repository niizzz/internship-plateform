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
- Preserves bullet structure and layout: the CV is exactly one page and MUST stay one page
- Uses the same tone and language as the original

HARD CONSTRAINTS on every replacement:
- new_text must be the SAME LENGTH OR SHORTER than the original paragraph (character count). Never longer — replacements longer than the original are rejected automatically.
- Edit surgically: swap only the words that need to change; keep the rest of the paragraph character-for-character identical (this preserves the CV's fonts and layout).

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

COVER_LETTER_SYSTEM = """You write a cover letter for a finance student applying to a Sales / Trading / Structuring internship — in the style of a short, well-told STORY, not a resume rewritten in prose. Read the candidate's CV and the job posting, then write a letter a busy recruiter actually wants to finish.

VOICE & STYLE:
- Open with a HOOK — never "I am writing to apply for...". Start mid-thought: a specific moment, decision, or conviction, or a concrete detail that pulls the reader in and connects the candidate to THIS desk and THIS bank. One or two sentences.
- Build the letter around ONE signature story from the candidate's real experience: the situation, the problem or tension, what the candidate personally DID, the skill it took, and the outcome (with the real numbers). Give it a small arc — show, don't list.
- Weave in at most one or two other proof points naturally inside the narrative; never a comma-spliced dump of achievements.
- Make the motivation specific and genuine: a real reason this bank/desk fits the candidate's trajectory (something concrete about the role, team, or the firm's approach — infer from the posting). No generic flattery.
- Confident, warm, human first-person voice. Vary sentence length. Sound like a sharp person talking, not a template.

BANNED phrases (never use): "I am writing to apply", "I am confident that", "team player", "hit the ground running", "passionate about finance", "detail-oriented", "fast-paced environment", "I believe my skills", "perfect fit", "hard-working", "dynamic". Do not restate the CV line by line. Do not open with the candidate's name or a date.

STRUCTURE (about 4 short paragraphs, one page, ~320–400 words):
1. Hook + why this exact role and bank.
2. The signature story: problem → the candidate's action → skill → result, tied to what the posting needs.
3. One more angle of fit — a second short proof point or a genuine, specific motivation.
4. A brief, warm close that looks forward and asks for a conversation.

HARD RULES:
- Use ONLY facts from the candidate's CV — never invent experience, tools, numbers, or results. Keep real numbers exact; they carry the story.
- Use the candidate's real name for the sign-off; no placeholders like [Your Name].
- English. Plain text only — no markdown, no headings, no preamble like "Here is the cover letter".
- End with "Sincerely," on its own line, then the candidate's full name on the next line.
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
        if not (isinstance(idx, int) and isinstance(new_text, str)):
            continue
        if 0 <= idx < len(base_cv_paragraphs):
            # Enforce the one-page invariant: a longer paragraph can reflow the
            # whole CV onto a second page. Small tolerance, then reject.
            old_len = len(base_cv_paragraphs[idx])
            if len(new_text) > max(old_len + 10, int(old_len * 1.05)):
                logger.info("Rejecting over-long replacement for paragraph %d (%d > %d chars)",
                            idx, len(new_text), old_len)
                continue
        out[idx] = new_text
    return out


CV_JSON_SYSTEM = """You tailor a finance student's CV to a specific Sales / Trading / Structuring internship, for ATS keyword matching. You are given the CV's EDITABLE content as JSON plus a job posting. Rewrite ONLY the wording so it mirrors the posting's language and desk — keep every fact identical.

Rewrite:
- "profile": the summary paragraph. Keep it about the same length and end with a "Seeking a … internship at <this bank> …" sentence aimed at THIS role and bank.
- for each experience entry: its "subtitle" (only if the entry has one) and its "bullets" — mirror the skills/keywords the posting names.

HARD RULES:
- NEVER change employers, schools, job titles, dates, locations, numbers, GPAs, or metrics. Only rephrase.
- Keep each bullet the same length or shorter (the CV must stay one page) and keep the SAME NUMBER of bullets per entry.
- Do not invent experience, tools, or results.
- Keep English and a professional tone.

Output STRICT JSON only, no prose, no markdown fences:
{"profile": "...", "experience": [{"index": 0, "subtitle": "...", "bullets": ["...", "..."]}]}
Include an experience entry only if you changed it; omit "subtitle" when the entry has none."""


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        logger.warning("No JSON found in tailoring response: %s", text[:300])
        return {}
    try:
        return json.loads(match.group(0), strict=False)
    except json.JSONDecodeError as e:
        logger.warning("Tailoring JSON parse failed: %s", e)
        return {}


def _profile_section(cv: dict):
    for s in cv.get("sections", []):
        if s.get("type") == "text" and str(s.get("heading", "")).upper().startswith("PROFILE"):
            return s
    return None


def _experience_section(cv: dict):
    for s in cv.get("sections", []):
        if str(s.get("heading", "")).upper().startswith("PROFESSIONAL EXPERIENCE"):
            return s
    return None


def tailor_cv_json(base_cv: dict, offer: dict) -> tuple[dict, int]:
    """Return (tailored_cv, n_changes). Rewrites only the profile summary and the
    experience subtitles/bullets to mirror the posting; every structural field
    (names, titles, dates, education, skills) is preserved verbatim."""
    import copy

    prof_s = _profile_section(base_cv)
    exp_s = _experience_section(base_cv)
    payload = {
        "profile": (prof_s or {}).get("text", ""),
        "experience": [
            {
                "index": i,
                "role": e.get("left", ""),  # context only — do not change
                **({"subtitle": e["subtitle"]} if e.get("subtitle") else {}),
                "bullets": list(e.get("bullets") or []),
            }
            for i, e in enumerate((exp_s or {}).get("entries", []))
        ],
    }
    prompt = (
        f"{CV_JSON_SYSTEM}\n\n"
        f"CV EDITABLE CONTENT:\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n\n"
        f"Tailor for this job:\n\n{_job_block(offer)}\n\nReturn JSON only."
    )
    parsed = _extract_json(_run_claude(prompt))
    tailored = copy.deepcopy(base_cv)
    changes = 0

    new_prof = parsed.get("profile")
    ts = _profile_section(tailored)
    if ts is not None and isinstance(new_prof, str) and new_prof.strip():
        if ts.get("text") != new_prof.strip():
            changes += 1
        ts["text"] = new_prof.strip()

    edits = {e.get("index"): e for e in parsed.get("experience", []) if isinstance(e, dict)}
    te = _experience_section(tailored)
    if te is not None:
        for i, entry in enumerate(te.get("entries", [])):
            ed = edits.get(i)
            if not ed:
                continue
            if entry.get("subtitle") and isinstance(ed.get("subtitle"), str) and ed["subtitle"].strip():
                if entry["subtitle"] != ed["subtitle"].strip():
                    changes += 1
                entry["subtitle"] = ed["subtitle"].strip()
            nb = ed.get("bullets")
            old = entry.get("bullets") or []
            # Keep the same bullet count so the layout never shifts.
            if isinstance(nb, list) and old:
                clean = [str(x).strip() for x in nb if str(x).strip()]
                if len(clean) == len(old):
                    if clean != old:
                        changes += 1
                    entry["bullets"] = clean
    return tailored, changes


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
