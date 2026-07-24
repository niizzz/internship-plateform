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
    finally:
        llm._run_claude = real_run

    print(f"\nTOTAL: {PASS}/{PASS + FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
