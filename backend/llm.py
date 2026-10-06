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

# Max characters for a tailored experience bullet to stay on ONE printed line at
# the CV's base font size (measured against the A4 render; ~135 is the hard wrap
# point, 132 leaves a small safety margin). Longer rewrites are rejected in
# favour of the original bullet; see tailor_cv_json.
BULLET_MAX = 132

# The candidate's standing target (2026-08-17, refined): SALES on the STRUCTURED
# EQUITY DERIVATIVES desk. He rates his odds of breaking in on the sales side
# higher than on trading, and structured equity derivatives is the product area
# his experience actually evidences (structured products at Lazard/SG, macro
# structuring at CACIB, FX sales trading at Valoris). Most postings are broader
# than that ("Global Markets", "FICC & Equities Sales & Trading"), so this steers
# EMPHASIS and ordering only — it never adds or removes a fact, and it never
# overrides a posting that is explicitly a different desk or a pure trading seat.
DESK_EMPHASIS = """DESK EMPHASIS (candidate's target):
- The candidate is targeting a SALES seat on the STRUCTURED EQUITY DERIVATIVES
  desk: pricing and pitching structured/equity-linked payoffs (autocallables,
  reverse convertibles, capital-protected notes, delta-one and volatility
  products) to institutional and private-bank clients.
- Wherever the posting genuinely allows it, foreground in this order:
  (1) client-facing sales evidence — communication, persuasion, relationship
      building, reading client needs, pitching trade ideas, explaining complex
      payoffs simply, languages;
  (2) STRUCTURED PRODUCTS and EQUITY DERIVATIVES product knowledge — payoff
      mechanics, pricing, volatility, hedging;
  (3) the quantitative / execution / modelling material, which stays in and is
      still relevant, but sits after the two above.
- Prefer the candidate's real structured-products and derivatives vocabulary
  over generic "financial markets" phrasing when the underlying fact supports it.
- If the posting is unambiguously a different desk (FICC/rates/credit/FX) or a
  pure trading / quant / market-making seat, tailor to the posting AS WRITTEN and
  drop this emphasis — never contort the CV toward a desk the posting is not.
- This changes ORDER and WORDING only. Never add, drop, or alter a fact, and
  never claim sales or product experience the CV does not evidence."""

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

STRUCTURE (exactly 4 short paragraphs, one page, ~300–380 words):
1. Hook + why this exact role and bank.
2. The signature story: problem, the candidate's action, the skill, and the result, tied to what the posting needs.
3. One more angle of fit: a second short proof point or a genuine, specific motivation.
4. A brief, warm close that looks forward and asks for a conversation.

HARD RULES:
- Use ONLY facts from the candidate's CV — never invent experience, tools, numbers, or results. Keep real numbers exact; they carry the story.
- Write ONLY the body paragraphs. Do NOT write a salutation/greeting ("Dear ..."), a date, an address block, or a sign-off ("Sincerely", a name) — those are added automatically around your text. Do not open with the candidate's name.
- No em dashes or en dashes. No markdown, no headings, no bullet points, no preamble like "Here is the cover letter".
- English, plain text, paragraphs separated by a blank line.
"""


class ClaudeCliError(RuntimeError):
    pass


def _claude_exe() -> str:
    exe = shutil.which("claude")
    if exe:
        return exe
    # The native installer puts it in ~/.local/bin, which a GUI-launched
    # backend's PATH often lacks (claude.exe on Windows, claude on macOS).
    for name in ("claude.exe", "claude"):
        fallback = Path.home() / ".local" / "bin" / name
        if fallback.exists():
            return str(fallback)
    raise ClaudeCliError(
        "Claude Code CLI not found. Install it or add it to PATH (expected in ~/.local/bin)."
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
- Every bullet MUST fit on ONE line. Keep each bullet to about 120 characters and NEVER longer than the original bullet — if the original is long, make your version SHORTER. Compress by cutting filler words ("responsible for", "in order to", "various"), not facts or numbers.
- Keep the SAME NUMBER of bullets per entry.
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
        f"{DESK_EMPHASIS}\n\n"
        f"CV EDITABLE CONTENT:\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n\n"
        f"Tailor for this job:\n\n{_job_block(offer)}\n\nReturn JSON only."
    )
    parsed = _extract_json(_run_claude(prompt))
    # Second pass over the REWRITTEN text: strip AI tells with the vendored
    # humanizer skill before any of it lands in the CV, mirroring what
    # generate_cover_letter does for the letter body. Falls back to the tailored
    # text on any failure, so this can only ever change HOW the CV reads.
    parsed = humanize_cv_json(parsed)
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
                    # One-line guard: accept a rewritten bullet only if it fits on
                    # one line (<= BULLET_MAX chars). If the model returned a longer
                    # bullet, keep the ORIGINAL — the renderer's font auto-fit then
                    # shrinks any base bullet that is itself over-length. This keeps
                    # tailored CVs at near-full size instead of shrinking to fit a
                    # rewrite that grew past one line.
                    capped = [new_b if len(new_b) <= BULLET_MAX else old_b
                              for new_b, old_b in zip(clean, old)]
                    if capped != old:
                        changes += 1
                    entry["bullets"] = capped
    if changes == 0:
        # No usable edit came back (empty/malformed JSON, or output identical
        # to the base CV). Shipping the base CV labeled "tailored" would be a
        # silent lie — fail loudly so the queue records it and the user retries.
        raise ClaudeCliError(
            "tailoring produced no changes (model output was empty, malformed, "
            "or identical to the base CV)")
    return tailored, changes


_GREETING_RE = re.compile(r"^\s*(dear|hello|hi|to whom|good (morning|afternoon))\b.*", re.I)
_SIGNOFF_RE = re.compile(
    r"\n\s*(sincerely|regards|best regards|kind regards|warm regards|"
    r"yours (faithfully|sincerely|truly)|respectfully|best),?\s*\n.*",
    re.I | re.S)


def _strip_scaffold(text: str) -> str:
    """Drop any greeting line / sign-off block the model added despite being told
    to write body-only (the renderer owns the salutation and sign-off)."""
    t = (text or "").strip()
    lines = t.split("\n")
    if lines and _GREETING_RE.match(lines[0].strip()):
        t = "\n".join(lines[1:]).strip()
    t = _SIGNOFF_RE.sub("", t).strip()
    return t


_HUMANIZER_MD_PATH = Path(__file__).parent / "humanizer_skill.md"
_humanizer_md_cache: str | None = None


def _humanizer_instructions() -> str:
    global _humanizer_md_cache
    if _humanizer_md_cache is None:
        try:
            _humanizer_md_cache = _HUMANIZER_MD_PATH.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("humanizer skill not found (%s); skipping humanize pass", e)
            _humanizer_md_cache = ""
    return _humanizer_md_cache


def humanize_cover_letter(body_text: str) -> str:
    """Second CLI pass: rewrite the cover-letter body with the vendored humanizer
    skill to strip AI tells and give it a natural, human voice. Facts are frozen.
    Falls back to the input on any failure so the pipeline never breaks."""
    guide = _humanizer_instructions()
    if not guide.strip() or not body_text.strip():
        return body_text
    prompt = (
        f"{guide}\n\n"
        "======================================================================\n"
        "TASK: Apply the humanizing guide above to the COVER-LETTER BODY below.\n\n"
        "This is a real job-application cover letter, so obey these hard limits:\n"
        "- Do NOT invent, add, drop, or alter any fact, number, employer, school, "
        "date, tool, or result. Change only HOW it reads, never WHAT it says.\n"
        "- Keep first person; keep it warm and specific; keep the same number of "
        "paragraphs and the same order of ideas.\n"
        "- Keep standard finance compound terms hyphenated when they modify a noun "
        "(equity-linked, fixed-income, cross-asset, soft-call, step-up, "
        "market-making, front-office, risk-adjusted, sales-trading). Do NOT strip "
        "those hyphens.\n"
        "- No em dashes or en dashes, no emojis, no markdown, no headings.\n"
        "- Do NOT add a greeting, date, address, or sign-off. Output the body only.\n"
        "- Output ONLY the final rewritten body between <LETTER> and </LETTER> "
        "tags. No draft, no audit notes, no commentary.\n\n"
        f"<BODY>\n{body_text.strip()}\n</BODY>"
    )
    try:
        out = _run_claude(prompt)
    except ClaudeCliError as e:
        logger.warning("humanizer pass failed (%s); using original draft", e)
        return body_text
    m = re.search(r"<LETTER>(.*?)</LETTER>", out, re.DOTALL)
    if not m:
        # Without the tags we can't tell letter from commentary ("Here is the
        # rewritten letter: ...") — never ship raw model output as the letter.
        logger.warning("humanizer output missing <LETTER> tags; keeping original draft")
        return body_text
    result = re.sub(r"</?LETTER>", "", m.group(1)).strip()
    # Backstop: the guide bans em/en dashes, but if any survived, soften them.
    result = re.sub(r"\s*[—–]\s*", ", ", result)
    result = re.sub(r",\s*,", ", ", result)
    return _strip_scaffold(result) or body_text


def humanize_cv_json(payload: dict) -> dict:
    """Third CLI pass on a TAILORED CV: run the profile paragraph, the experience
    subtitles and the bullets through the vendored humanizer skill so the CV does
    not read as machine-written.

    Takes/returns the same editable-content shape `tailor_cv_json` builds
    ({"profile": str, "experience": [{"index", "subtitle"?, "bullets": [...]}]}).
    Falls back to the input on ANY failure or shape mismatch — a CV that reads a
    little synthetic beats a CV with mangled or invented content.
    """
    guide = _humanizer_instructions()
    if not guide.strip():
        return payload
    profile = (payload.get("profile") or "").strip()
    entries = [e for e in payload.get("experience", []) if isinstance(e, dict)]
    if not profile and not entries:
        return payload

    # Exact per-entry bullet counts, so a dropped or invented bullet is caught.
    shape = {int(e["index"]): len(e.get("bullets") or [])
             for e in entries if isinstance(e.get("index"), int)}

    prompt = (
        f"{guide}\n\n"
        "======================================================================\n"
        "TASK: Apply the humanizing guide above to the CV CONTENT below.\n\n"
        "This is a real CV for a job application, so obey these hard limits:\n"
        "- Do NOT invent, add, drop, or alter any fact, number, metric, percentage, "
        "employer, school, job title, tool, language, or date. Change only HOW it "
        "reads, never WHAT it says. Every number must survive EXACTLY.\n"
        "- Keep the SAME NUMBER of bullets for each experience entry, in the same "
        "order, and keep the same entry indexes. Do not merge or split bullets.\n"
        f"- Each bullet must stay on ONE line: at most {BULLET_MAX} characters, and "
        "no longer than the bullet you were given. Compress by cutting filler, "
        "never by cutting facts.\n"
        "- Keep CV register: terse, punchy, results-first. Bullets stay bullets, "
        "not sentences with 'I'. Do NOT add personality, humour, hedging, opinions, "
        "or first-person narration — the humanizer's PERSONALITY guidance is "
        "overridden here by CV convention.\n"
        "- Strip the AI tells the guide names: promotional adjectives, inflated "
        "symbolism, vague attributions, superficial -ing clauses, rule-of-three "
        "padding, negative parallelisms ('not only ... but also'), filler phrases, "
        "and AI vocabulary (leverage, spearhead, robust, seamless, comprehensive, "
        "delve, showcase, pivotal, testament, underscore, foster, landscape).\n"
        "- Prefer concrete verbs the candidate could defend in an interview: "
        "priced, quoted, hedged, built, automated, pitched, covered, reconciled.\n"
        "- Keep standard finance compound terms hyphenated when they modify a noun "
        "(equity-linked, fixed-income, cross-asset, delta-one, capital-protected, "
        "market-making, front-office, risk-adjusted, sales-trading). Do NOT strip "
        "those hyphens.\n"
        "- No em dashes or en dashes, no emojis, no markdown, no bullet characters.\n"
        "- Output ONLY strict JSON between <CV> and </CV> tags, same shape as the "
        "input, with no commentary:\n"
        '  {"profile": "...", "experience": [{"index": 0, "subtitle": "...", '
        '"bullets": ["...", "..."]}]}\n'
        '  Omit "subtitle" for entries that were given none.\n\n'
        f"<CV_CONTENT>\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n</CV_CONTENT>"
    )
    try:
        out = _run_claude(prompt)
    except ClaudeCliError as e:
        logger.warning("CV humanizer pass failed (%s); keeping tailored text", e)
        return payload

    m = re.search(r"<CV>(.*?)</CV>", out, re.DOTALL)
    if not m:
        logger.warning("CV humanizer output missing <CV> tags; keeping tailored text")
        return payload
    parsed = _extract_json(m.group(1))
    if not isinstance(parsed, dict) or not parsed:
        logger.warning("CV humanizer output was not usable JSON; keeping tailored text")
        return payload

    result = {"profile": payload.get("profile"), "experience": []}
    new_prof = parsed.get("profile")
    if isinstance(new_prof, str) and new_prof.strip():
        result["profile"] = _drop_dashes(new_prof.strip())

    by_index = {e.get("index"): e for e in parsed.get("experience", [])
                if isinstance(e, dict)}
    for src in entries:
        idx = src.get("index")
        got = by_index.get(idx)
        keep = dict(src)
        if got:
            sub = got.get("subtitle")
            if src.get("subtitle") and isinstance(sub, str) and sub.strip():
                keep["subtitle"] = _drop_dashes(sub.strip())
            old = list(src.get("bullets") or [])
            nb = got.get("bullets")
            if isinstance(nb, list) and old and len(nb) == shape.get(idx, -1):
                clean = [_drop_dashes(str(x).strip()) for x in nb]
                if all(clean):
                    # Same one-line rule tailoring uses: an over-long rewrite
                    # loses to the text it replaced.
                    keep["bullets"] = [n if len(n) <= BULLET_MAX else o
                                       for n, o in zip(clean, old)]
        result["experience"].append(keep)
    return result


def _drop_dashes(text: str) -> str:
    """The guide bans em/en dashes; soften any that survived the pass."""
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    return re.sub(r",\s*,", ", ", text)


def generate_cover_letter(
    base_cv_text: str,
    offer: dict,
    base_cover_letter_text: str | None = None,
    **_ignored,
) -> str:
    """Generate the cover-letter BODY (no salutation/sign-off) as a story draft,
    then run the humanizer pass over it. Returns body-only text; the renderer
    wraps it with the letterhead, date, recipient, greeting and sign-off."""
    template_block = (
        f"\n\nCANDIDATE'S OWN COVER LETTER TEMPLATE (match its voice and reuse its strongest lines where relevant):\n"
        f"{base_cover_letter_text}"
        if base_cover_letter_text else ""
    )
    prompt = (
        f"{COVER_LETTER_SYSTEM}\n\n"
        f"{DESK_EMPHASIS}\n\n"
        f"CANDIDATE CV:\n{base_cv_text}"
        f"{template_block}\n\n"
        f"Write the cover letter body for this job:\n\n{_job_block(offer)}"
    )
    draft = _strip_scaffold(_run_claude(prompt))
    return humanize_cover_letter(draft)
