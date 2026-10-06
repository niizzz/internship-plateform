"""Cold-outreach message drafting via the Claude Code CLI.

Separate from llm.py (CV / cover-letter tailoring) because the prompting concern
is different: short messages to a named human, where the failure mode is not a
weak keyword match but an invented fact or an obviously templated opener.

These functions produce DRAFTS ONLY. Nothing here sends anything, and nothing
here reads LinkedIn: LinkedIn's User Agreement prohibits automated access, and
an automated sender risks the very account the candidate networks from. The user
reviews, edits and sends by hand, which is also what keeps the messages worth
reading.
"""
from __future__ import annotations

import logging
import re

from llm import (
    ClaudeCliError,
    DESK_EMPHASIS,
    _drop_dashes,
    _humanizer_instructions,
    _job_block,
    _run_claude,
)

logger = logging.getLogger(__name__)

OUTREACH_SYSTEM = """You draft a short LinkedIn outreach message for a student trying to break into Sales & Trading in Europe. You are writing as the CANDIDATE, in first person.

What actually gets a reply, in order:
1. A SPECIFIC, TRUE reason for writing to THIS person (shared school, their desk, their own path). Generic praise reads as a mail merge and gets ignored.
2. Brevity. Junior bankers read on a phone between tasks. Short wins.
3. A small, easy ask. "15 minutes to hear how you found the desk" is answerable. "Can you refer me" is not, from a stranger.
4. Evidence of homework on the FIRM and the DESK, in one clause, not a paragraph.

HARD RULES:
- NEVER invent a fact. Not a shared class, not a mutual contact, not a meeting, not an article they wrote, not a club, not a conversation. You may only use facts given to you below. If the shared context is thin, write a message that is honestly a cold approach: that still works when it is short and specific about the desk.
- Never claim experience the candidate's CV does not show.
- The CV is the ONLY source of facts about the CANDIDATE (their school, course, employers, dates). The shared-context note describes the TIE between the two people; it is not a source of facts about the candidate. Where the two seem to disagree (a school or course named in the tie that the CV does not show), trust the CV and describe the tie in a way that stays true to it, for example 'the same programme' rather than naming a school the CV never mentions. Never let a subject line and a body disagree about where the candidate studies or works.
- No flattery openers ("I hope this finds you well", "I have long admired"). No "I am reaching out to". No "As a passionate finance student".
- No em dashes, no emojis, no markdown, no bullet points, no headings, no subject line.
- British / European spelling and conventions.
- Never mention that this was drafted with AI.
- Sign off with the candidate's first name only, or no sign-off at all for a connection note.

Output ONLY the message text between <MSG> and </MSG> tags. No commentary, no alternatives, no explanation."""


# Per-relationship steer. The opener is the whole ballgame and it differs
# completely by who the person is, so this is a hard switch rather than a hint.
ANGLE = {
    "alumni": """RELATIONSHIP: ALUMNI of the candidate's school.
- LEAD with the school tie in the first clause. It is the reason they open it.
- Then one line on why their specific path interests you (their desk, their move, their product area).
- Ask how they found the transition rather than for help. Alumni answer "how did you get there" far more readily than "can you help me".
- Warm but not familiar. You have never met.""",

    "intern": """RELATIONSHIP: a CURRENT OR RECENT INTERN at the target firm.
- Peer to peer. They were exactly where the candidate is, very recently. Write like a peer, not an applicant.
- KEEP IT VERY SHORT: two or three sentences, 60 words maximum. This OVERRIDES any longer word target in the FORMAT section. They are junior, busy, and will answer something they can reply to in one line; a long message from a stranger their own age reads as a demand.
- Ask about the process and the desk as they experienced it: what the interviews actually tested, how desk placement worked, whether the team takes off-cycles.
- DEFAULT ASK (the ASK section overrides this): do not ask them for a referral. They rarely have the standing to give one cold, and asking makes it awkward. A good conversation earns it later.""",

    "junior": """RELATIONSHIP: an ANALYST or ASSOCIATE on the target desk.
- The highest-yield target: close enough to remember applying, senior enough to pass a name to the desk.
- Lead with the DESK and the PRODUCT, specifically. Show you know what they actually trade or sell.
- One clause of relevant evidence from the candidate's own background, chosen to match their desk.
- Ask for 15 minutes and say what you would ask about, so the cost to them is legible.""",

    "recruiter": """RELATIONSHIP: a CAMPUS RECRUITER / HR contact.
- Process-oriented, not personal. Concrete and easy to action.
- State the exact programme, location and cycle the candidate is targeting.
- Ask one precise, answerable question: whether the programme is open, when applications close, whether the profile fits the eligibility criteria.
- Slightly more formal than the other angles, but still short.""",

    "senior": """RELATIONSHIP: SENIOR (VP / MD / desk head).
- Lowest reply rate, highest value. Short and genuinely specific, or it is deleted.
- Maximum four sentences, 70 words. This OVERRIDES any longer word target in the FORMAT section. No life story.
- Lead with something concrete about their desk or their market.
- DEFAULT ASK (the ASK section overrides this): something small and specific. Never ask a senior person for a job in a first message.
- Respectful and direct, no grovelling.""",
}

KIND_RULES = {
    "connection_note": (
        "FORMAT: a LinkedIn CONNECTION REQUEST note. HARD LIMIT 300 CHARACTERS "
        "including spaces. This is enforced by LinkedIn and a longer draft is "
        "unusable. Aim for 250-290 characters. No sign-off and no greeting line: "
        "it sits under a connect button. One specific reason plus one light ask. "
        "Do not spend characters introducing yourself in the abstract."
    ),
    "dm": (
        "FORMAT: a LinkedIn DIRECT MESSAGE, sent after connecting. Target 90-140 "
        "words and never more than 160. Open with the specific reason, one line of "
        "relevant evidence, then the ask. Sign off with the first name only."
    ),
    "followup": (
        "FORMAT: a FOLLOW-UP to an earlier message that got no reply. Target 35-60 "
        "words, hard maximum 80. Assume they are busy rather than rude, and convey "
        "that lightly without apologising twice. Add ONE new small thing (a recent "
        "development at the firm, a narrowed question) so it is not merely a nudge. "
        "Restate the ask once, smaller than before. Never guilt them and never "
        "write 'just bumping this'."
    ),
    "thank_you": (
        "FORMAT: a THANK-YOU after they replied or spoke to the candidate. 40-70 "
        "words. Reference something specific they actually said, given below. Say "
        "what the candidate is doing as a result, which is what makes them remember "
        "you. Keep the door open without asking for anything new."
    ),
}

# What the message asks for. Orthogonal to format and to relationship, and it
# is the part that decides whether the message converts.
ASK = {
    "chat": """ASK: a SHORT CONVERSATION about their work.
- You want to hear about THEIR desk, their day to day, how they got there, and what their previous role taught them. Frame it as curiosity about their path, not as a step in your job hunt.
- Name the two or three things you would actually ask about, so the cost to them is legible and they can answer in writing if they prefer.
- Fifteen minutes is the standard ask. Ten reads as more considerate and converts slightly better.
- Do NOT mention your CV, do not attach anything, and do not ask them to pass your name anywhere. This message is not a disguised application; if it reads like one it fails.""",

    "referral": """ASK: that they PUT THE CANDIDATE'S CV FORWARD for a specific role.
- Say plainly which role and which desk, early. A vague referral ask cannot be actioned.
- Make it PROPORTIONATE to how well they know the candidate. Cold and unacquainted: ask whether they would be comfortable passing the CV to the team, or whether the firm has an employee-referral route, rather than asking them to vouch for someone they have never spoken to. With a genuine tie or prior contact, ask directly.
- Give them ONE line of the strongest, most role-relevant evidence, so a person who says yes has something to forward. This is the line the desk will actually read.
- Make the yes cheap: say the CV is attached (email) or offer to send it (LinkedIn), and that you are happy for them to pass it on with no comment attached.
- Give them a clean way out. A referral they feel cornered into is worse than a no, and it costs the relationship.
- Never imply they already offered to help when they did not. Never claim a mutual contact you were not given.""",
}


def _ask_block(ask_type: str, target_role: str | None) -> str:
    base = ASK.get(ask_type or "chat", ASK["chat"])
    if ask_type == "referral":
        role = (target_role or "").strip()
        base += (
            "\n- The role in question: " + role if role else
            "\n- No specific posting was given, so name the DESK the candidate is "
            "targeting (structured products / equity derivatives sales) rather than "
            "inventing a job title or requisition number."
        )
    return base


# Email gets a subject line and slightly more room than a LinkedIn message; the
# 300-character connection note has no email equivalent, so a first email uses
# the "dm" slot.
EMAIL_RULES = {
    "dm": (
        "FORMAT: a FIRST EMAIL. Target 120-180 words. Slightly more formal than a "
        "LinkedIn message but still direct: no 'Dear Sir/Madam', no corporate "
        "throat-clearing. Open with the specific reason for writing. Sign off with "
        "the candidate's first name."
    ),
    "followup": (
        "FORMAT: a FOLLOW-UP EMAIL on a thread that got no reply. 40-70 words, hard "
        "maximum 90. Assume they are busy rather than rude. Add ONE new small thing "
        "so it is not merely a nudge, and restate the ask once, smaller than before. "
        "Never guilt them and never write 'just bumping this'."
    ),
    "thank_you": (
        "FORMAT: a THANK-YOU EMAIL after they replied or spoke to the candidate. "
        "50-80 words. Reference something specific they actually said, given below. "
        "Say what the candidate is doing as a result. Ask for nothing new."
    ),
}


def format_rules(channel: str, kind: str) -> str:
    """Format guidance for one channel+kind pair.

    A connection note is LinkedIn-only, so an email asking for one falls back to
    the first-email format rather than producing a 300-character stub.
    """
    if channel == "email":
        return EMAIL_RULES.get(kind if kind in EMAIL_RULES else "dm", EMAIL_RULES["dm"])
    return KIND_RULES.get(kind, KIND_RULES["dm"])


SUBJECT_RULE = (
    "SUBJECT LINE: also produce a subject, between <SUBJ> and </SUBJ> tags, before "
    "the message. Six to nine words, specific and plain. It should say who is "
    "writing and what for, so it is legible in a crowded inbox. No colons used as "
    "clickbait, no 'Quick question', no exclamation marks, no the word 'Opportunity'."
)


# LinkedIn truncates a connection-request note at 300 characters.
CONNECTION_NOTE_MAX = 300


def _contact_block(c: dict) -> str:
    """Only real, user-entered facts reach the prompt. Anything absent is stated
    as absent, so the model cannot quietly invent a tie that does not exist."""
    def line(label: str, val) -> str:
        return f"{label}: {val}" if val else f"{label}: (not known, do not invent one)"

    parts = [
        f"Name: {c.get('full_name')}",
        f"Firm: {c.get('bank')}",
        line("Their role", c.get("role_title")),
        line("Their desk / product area", c.get("desk")),
        line("Their location", c.get("location")),
        line("Their school", c.get("school")),
        line("Their graduation year", c.get("grad_year")),
    ]
    shared = (c.get("shared_context") or "").strip()
    parts.append(
        "\nTRUE SHARED CONTEXT (the only tie you may reference, verbatim facts only; this describes the RELATIONSHIP, never the candidate's own credentials, which come from the CV alone):\n" + shared
        if shared else
        "\nTRUE SHARED CONTEXT: NONE GIVEN. There is no shared history with this "
        "person. Do NOT manufacture one. Write an honest cold message that earns "
        "attention through specificity about their desk instead."
    )
    if shared:
        # The rule also lives in OUTREACH_SYSTEM, but a conflict is resolved far
        # more reliably when the instruction sits directly against the offending
        # data than when it is one bullet in a long list at the top.
        parts.append(
            "\nCONFLICT RULE: the candidate's OWN school, course, employers and dates "
            "come from the CV above and from nowhere else. The tie above may name an "
            "institution loosely. If it implies the candidate studies or works "
            "somewhere the CV does not show, the CV is correct: express the tie "
            "WITHOUT placing the candidate there (write 'the same programme' or 'the "
            "course you came through', not the institution's name). Never write a "
            "sentence that puts the candidate at a school or employer absent from the "
            "CV, and never let the subject line and the body disagree about it."
        )
    if (c.get("notes") or "").strip():
        parts.append("\nCandidate's notes on this person:\n" + c["notes"].strip())
    return "\n".join(parts)


def _role_block(offer: dict | None) -> str:
    if not offer:
        return ("TARGET ROLE: no specific posting. The candidate is approaching the "
                "firm and desk generally, so do not reference a specific job title or id.")
    return "TARGET ROLE the candidate is pursuing at this firm:\n" + _job_block(offer)


def _thread_block(previous: list[dict] | None) -> str:
    """Prior messages, so a follow-up cannot recycle the opener that already
    failed to get a reply."""
    if not previous:
        return ""
    lines = ["\nALREADY SENT TO THIS PERSON (never repeat these openers or phrasings):"]
    for m in previous:
        when = str(m.get("sent_at") or m.get("created_at") or "")[:10]
        lines.append(f"\n[{m.get('kind')} · {when}]\n{m.get('body')}")
    return "\n".join(lines)


_MSG_RE = re.compile(r"<MSG>(.*?)</MSG>", re.DOTALL)
_PREAMBLE_RE = re.compile(r"^(here'?s?|draft|message|sure|certainly)\b.*:\s*$", re.I)


_SUBJ_RE = re.compile(r"<SUBJ>(.*?)</SUBJ>", re.DOTALL)


def _extract_subject(raw: str) -> str | None:
    """Pull the subject out of its tags. Returns None when the model omitted it,
    so the caller can fall back rather than shipping an empty subject."""
    m = _SUBJ_RE.search(raw or "")
    if not m:
        return None
    subj = re.sub(r"\s+", " ", m.group(1)).strip().strip('"').strip()
    return subj or None


def clean_outreach(raw: str) -> str:
    """Pull the message out of the tags and strip the usual model residue.

    Without the tags we cannot reliably tell message from preamble ("Here's a
    draft:"), so fall back to the raw text only after dropping a leading
    commentary line.
    """
    m = _MSG_RE.search(raw or "")
    text = m.group(1) if m else (raw or "")
    text = re.sub(r"</?MSG>", "", text).strip()
    if not m:
        lines = text.split("\n")
        if lines and _PREAMBLE_RE.match(lines[0].strip()):
            text = "\n".join(lines[1:]).strip()
    text = _drop_dashes(text)
    text = re.sub(r"^[#*`>\s]+", "", text)        # stray markdown lead-in
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def draft_outreach(
    contact: dict,
    kind: str,
    base_cv_text: str,
    offer: dict | None = None,
    previous: list[dict] | None = None,
    extra_instructions: str | None = None,
    channel: str = "linkedin",
    ask_type: str = "chat",
    target_role: str | None = None,
) -> tuple[str | None, str]:
    """Draft one outreach message. Returns (subject, body).

    subject is None for LinkedIn, which has no subject line. Raises
    ClaudeCliError if the CLI fails, so the caller surfaces the failure rather
    than storing a half-written message.
    """
    angle = ANGLE.get(str(contact.get("contact_type") or "junior"), ANGLE["junior"])
    rules = format_rules(channel, kind)
    ask = _ask_block(ask_type, target_role or (offer or {}).get("role_title"))
    extra = (
        "\n\nADDITIONAL INSTRUCTION FROM THE CANDIDATE (obey it):\n" + extra_instructions.strip()
        if (extra_instructions or "").strip() else ""
    )
    subject_rule = f"\n\n{SUBJECT_RULE}" if channel == "email" else ""
    prompt = (
        f"{OUTREACH_SYSTEM}\n\n"
        f"{DESK_EMPHASIS}\n\n"
        f"{angle}\n\n"
        f"{ask}\n\n"
        f"{rules}"
        f"{subject_rule}\n\n"
        "PRECEDENCE: the ASK section states what this message asks for and "
        "OVERRIDES any default ask guidance in the RELATIONSHIP section. Where "
        "RELATIONSHIP and FORMAT disagree on length, RELATIONSHIP wins and "
        "shorter always wins.\n\n"
        f"CANDIDATE'S CV (the only source of facts about the candidate):\n{base_cv_text}\n\n"
        f"PERSON BEING WRITTEN TO:\n{_contact_block(contact)}\n\n"
        f"{_role_block(offer)}"
        f"{_thread_block(previous)}"
        f"{extra}\n\n"
        "Write the message now."
    )
    raw = _run_claude(prompt)
    subject = _extract_subject(raw) if channel == "email" else None
    return subject, clean_outreach(raw)


def humanize_outreach(text: str, kind: str) -> str:
    """Second CLI pass with the vendored humanizer guide. A cold message that
    reads as machine-written is worse than no message at all, so this matters
    more here than on a cover letter. Falls back to the input on any failure."""
    guide = _humanizer_instructions()
    if not guide.strip() or not (text or "").strip():
        return text
    limit = (
        "Keep the result under 300 characters total: this is a LinkedIn connection note."
        if kind == "connection_note" else
        "Keep the length within a few words of the original."
    )
    prompt = (
        f"{guide}\n\n"
        "======================================================================\n"
        "TASK: Apply the humanizing guide above to the OUTREACH MESSAGE below.\n\n"
        "This is a real message to a real person, so obey these hard limits:\n"
        "- Do NOT invent, add, drop, or alter any fact, name, school, employer, "
        "date or claim. Change only HOW it reads, never WHAT it says.\n"
        f"- {limit}\n"
        "- Keep first person. Keep the same ask and the same order of ideas.\n"
        "- No em dashes, no emojis, no markdown, no subject line.\n"
        "- Output ONLY the final message between <MSG> and </MSG> tags.\n\n"
        f"<BODY>\n{text.strip()}\n</BODY>"
    )
    try:
        out = _run_claude(prompt)
    except ClaudeCliError as e:
        logger.warning("outreach humanizer failed (%s); using original draft", e)
        return text
    if "<MSG>" not in out:
        logger.warning("outreach humanizer output missing <MSG> tags; keeping original draft")
        return text
    return clean_outreach(out) or text


def shorten_to_limit(text: str, limit: int = CONNECTION_NOTE_MAX) -> str:
    """Ask the CLI to cut an overlong connection note down to the hard limit.

    Never truncates mid-sentence: a note cut at 300 characters reads as broken,
    which is worse than a slightly blander one. Returns the input unchanged if
    the CLI cannot get it under the limit, and the caller reports the overrun.
    """
    if len(text) <= limit:
        return text
    prompt = (
        "Shorten this LinkedIn connection note so it fits within "
        f"{limit} characters INCLUDING SPACES. It is currently {len(text)}.\n\n"
        "Rules:\n"
        "- Keep the specific reason for writing and the ask. Cut adjectives, "
        "throat-clearing and any sentence that is not doing work.\n"
        "- Do NOT invent, add or alter any fact.\n"
        "- Do not truncate mid-sentence. It must read as a complete message.\n"
        "- No em dashes, no emojis, no markdown.\n"
        "- Output ONLY the shortened note between <MSG> and </MSG> tags.\n\n"
        f"<BODY>\n{text.strip()}\n</BODY>"
    )
    try:
        out = clean_outreach(_run_claude(prompt))
    except ClaudeCliError as e:
        logger.warning("connection-note shortening failed (%s); returning original", e)
        return text
    return out if out and len(out) <= limit else text
