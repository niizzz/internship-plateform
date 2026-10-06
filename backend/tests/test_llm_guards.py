"""Tailoring must fail loudly, never silently ship untailored output.

Run:  .venv/Scripts/python.exe -m tests.test_llm_guards   (from backend/)
Mocks the Claude CLI boundary (llm._run_claude) — no CLI calls are made.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, got=None) -> None:
    global PASS, FAIL
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + ("" if cond else f"   got={got!r}"))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


BASE_CV = {
    "name": "A B",
    "contact": ["a@b.c"],
    "sections": [
        {"type": "text", "heading": "PROFILE", "text": "Finance student."},
        {"type": "entries", "heading": "PROFESSIONAL EXPERIENCE", "entries": [
            {"left": "Bank Z", "right": "2025", "bullets": ["Did a thing with numbers 12%."]},
        ]},
    ],
}
OFFER = {"bank": "TestBank", "role_title": "S&T Intern", "location": "London",
         "program_type": "summer_internship", "description": "FICC desk."}


def main() -> int:
    real_run = llm._run_claude

    # 1. Garbage (no JSON) from the model -> must RAISE, not return 0 changes.
    llm._run_claude = lambda prompt: "sorry, here is prose with no json at all"
    try:
        raised = False
        try:
            llm.tailor_cv_json(BASE_CV, OFFER)
        except llm.ClaudeCliError:
            raised = True
        check("no-JSON tailoring response raises ClaudeCliError", raised)

        # 2. Valid JSON but zero effective edits -> must also raise.
        llm._run_claude = lambda prompt: '{"profile": "", "experience": []}'
        raised = False
        try:
            llm.tailor_cv_json(BASE_CV, OFFER)
        except llm.ClaudeCliError:
            raised = True
        check("zero-change tailoring response raises ClaudeCliError", raised)

        # 3. A real edit still works and reports changes.
        llm._run_claude = lambda prompt: (
            '{"profile": "Finance student targeting TestBank S&T.", "experience": []}')
        cv, n = llm.tailor_cv_json(BASE_CV, OFFER)
        check("real edit accepted", n >= 1 and "TestBank" in cv["sections"][0]["text"], n)

        # 4. Humanizer output missing <LETTER> tags -> keep the original draft
        #    (raw model output may contain commentary that must never ship).
        llm._run_claude = lambda prompt: "Here is the rewritten letter:\n\nDear someone..."
        out = llm.humanize_cover_letter("My original body.\n\nSecond paragraph.")
        check("tagless humanizer output falls back to original",
              out == "My original body.\n\nSecond paragraph.", out)

        # 5. Proper <LETTER> output is used.
        llm._run_claude = lambda prompt: "<LETTER>Polished body.\n\nStill two paragraphs.</LETTER>"
        out = llm.humanize_cover_letter("My original body.\n\nSecond paragraph.")
        check("tagged humanizer output used", out.startswith("Polished body."), out)

        # --- CV humanizer pass (added 2026-08-17) ---------------------------
        # It rewrites the tailored CV text; a mangled response must never reach
        # the PDF, so every failure mode falls back to the tailored text.
        cv_payload = {
            "profile": "Leveraged robust frameworks to spearhead seamless delivery.",
            "experience": [{"index": 0, "subtitle": "Sub", "bullets": ["a b c", "d e f"]},
                           {"index": 1, "bullets": ["g h i"]}],
        }

        # 6. Well-formed <CV> output is applied and keeps the exact shape.
        llm._run_claude = lambda prompt: (
            '<CV>{"profile": "Priced flow for 50 clients.", "experience": ['
            '{"index": 0, "subtitle": "New sub", "bullets": ["aa", "bb"]},'
            '{"index": 1, "bullets": ["gg"]}]}</CV>')
        out = llm.humanize_cv_json(cv_payload)
        check("CV humanizer applies profile + keeps bullet counts",
              out["profile"].startswith("Priced flow")
              and len(out["experience"]) == 2
              and len(out["experience"][0]["bullets"]) == 2
              and "subtitle" not in out["experience"][1], out)

        # 7. Wrong bullet COUNT for an entry -> that entry keeps its bullets.
        llm._run_claude = lambda prompt: (
            '<CV>{"profile": "X.", "experience": [{"index": 0, "bullets": ["only one"]}]}</CV>')
        out = llm.humanize_cv_json(cv_payload)
        check("CV humanizer rejects a changed bullet count",
              out["experience"][0]["bullets"] == ["a b c", "d e f"], out)

        # 8. Missing <CV> tags -> whole payload falls back (never ship commentary).
        llm._run_claude = lambda prompt: 'Here is your CV: {"profile": "nope"}'
        out = llm.humanize_cv_json(cv_payload)
        check("tagless CV humanizer output falls back", out == cv_payload, out)

        # 9. An over-length bullet loses to the text it would replace.
        long_b = "x" * (llm.BULLET_MAX + 40)
        llm._run_claude = lambda prompt: (
            '<CV>{"profile": "P.", "experience": [{"index": 0, "subtitle": "S",'
            ' "bullets": ["' + long_b + '", "short one"]}]}</CV>')
        out = llm.humanize_cv_json(cv_payload)
        check("over-length humanized bullet is discarded",
              out["experience"][0]["bullets"][0] == "a b c"
              and out["experience"][0]["bullets"][1] == "short one", out)

        # 10. Em/en dashes the guide bans are softened, not shipped.
        llm._run_claude = lambda prompt: (
            '<CV>{"profile": "Sales focus \\u2014 equity derivatives.", "experience": []}</CV>')
        out = llm.humanize_cv_json({"profile": "old", "experience": []})
        check("CV humanizer strips em dashes",
              "—" not in out["profile"] and "–" not in out["profile"], out)
    finally:
        llm._run_claude = real_run

    print(f"\nTOTAL: {PASS}/{PASS + FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
