# Internship Plateform — agent handoff notes

Personal, single-user platform for landing a Sales & Trading internship:
scrapes 26 bank career sites, tailors CV/cover letters per offer via the
Claude Code CLI, tracks the application funnel, and assist-fills ATS forms.
Owner: Nizar (user's spelling "plateform" is intentional).

## Run / verify

- One-click: `launch.bat` (backend uvicorn on 127.0.0.1:8000, Vite on :5173).
  NB `timeout /t` inside the .bat breaks under non-interactive shells — start
  the two processes yourself if driving from an agent shell.
- Backend tests (plain-python, no pytest), from `backend/`:
  `.venv/Scripts/python.exe -m tests.test_persist_guards` (and
  `test_location_and_rules`, `test_llm_guards`, `test_cache_and_db`), plus
  `.venv/Scripts/python.exe test_filter.py` (S&T filter, 57 checks).
  ALL of these must stay green before touching persist/filter/llm code.
- Frontend: `npx tsc -b && npx vite build` from `frontend/`.

## Architecture (as of 2026-07-24)

- `backend/main.py` — FastAPI, all endpoints, tailoring queue, apply-assist
  launch, fingerprint cache (`_tailor_fingerprint`).
- `backend/scrapers/` — one module per bank + `generic.py` (Workday/
  Talentsoft/Greenhouse config-driven bases) + `base.py` (shared S&T/
  internship/Europe filters) + `orchestrator.py` (concurrent refresh,
  per-bank streaming persist, zero-streak deactivation guard).
- `backend/llm.py` — shells out to `claude -p` (subscription, NO API key).
  `backend/cv_render.py` — structured base_cv.json -> one-page PDF via
  headless Chromium with font auto-fit.
- `backend/apply_assist.py` — persistent headful Chromium, fills ATS forms,
  NEVER clicks submit.
- SQLite `backend/data/internships.db` (WAL + busy_timeout via
  `db.configure_engine`). `backend/storage/` + `backend/data/` are gitignored:
  they hold PII, the user's CV, and logged-in ATS browser sessions — never
  commit or publish their contents.
- Logs: `backend/data/app.log` (rotating). Refresh history:
  `backend/data/refresh_history.jsonl`.

## Invariants — do not regress (2026-07-24 audit)

1. Scrapers RAISE on total failure (never return [] after swallowed errors);
   partial mid-pagination results are allowed but logged "partial results kept".
2. `persist()` holds deactivation for a bank whose successful scrape kept 0
   offers while it still has active rows (ScraperState.zero_streak, wipe only
   on 2nd consecutive zero). Duplicate (bank, external_id) within a batch:
   first wins; unique index `ix_offer_bank_external` enforces it.
3. TailoredDocument.fingerprint = sha256(base_cv.json + offer title + desc).
   Mismatch == stale == regenerate. Pre-2026-07-24 docs have NULL fingerprint
   and intentionally regenerate on next use.
4. `tailor_cv_json` raises on zero changes (never ship base CV as "tailored");
   humanizer keeps the original draft when <LETTER> tags are missing.
5. Assist engine: fill-only-empty, never submit, honeypot/e-signature/
   import-widget exclusion, upload dedupe + MAX 3 per doc, phone typed not
   value-set, offer_country "Unknown" -> NO visa/sponsorship prefill.
6. Dismissing an offer-removed notification hard-deletes ONLY offers with no
   application history; applied+ offers archive as inactive.
7. Dashboard numbers must stay REAL — never fabricate funnel data.
8. No Anthropic API key anywhere; generation goes through the Claude CLI.

## Current state / next steps

- Local main is AHEAD of origin (github.com/niizzz/internship-plateform,
  private) — the sandbox blocked `git push`; the USER must push. Do not
  force-push; history is linear.
- Shareable read-only demo (static snapshot, sanitized, fetch-shimmed build
  of this frontend): https://claude.ai/code/artifact/c4f789d0-7006-4e84-a82e-dc5701c5d2af
  To refresh it: rebuild frontend with HashRouter swapped in main.tsx,
  re-run the snapshot+assembly (see git log for the 2026-07-24 session), and
  republish passing that URL as `url` to the Artifact tool. Re-sanitize:
  profile nulls, notes nulls, notifications [], no real filenames.
- KNOWN GAP: HSBC scraper reads the experienced/GSC board; student Global
  Markets programmes live on hsbc.avature.net (JS SPA, search path never
  captured — needs a live Playwright dig). It logs this every refresh.
- NEXT BANK WORK: see `NEXT_BANKS.md` (HSBC repoint, DB German sweep,
  UniCredit + Mizuho discovery/implementation, Rothschild verification).
- Sept–Nov 2026 is the posting season for GS/JPM/MS/Citi/UBS/BofA/HSBC summer
  2027 internships — expect offer volume to jump; enrich caps log truncation.
- Deferred nice-to-haves: dry-run mode for apply-assist; surface per-offer
  tailoring failures as a list; commit dist of the demo somewhere if the user
  wants versioning.

Deeper session-by-session context lives in the Claude auto-memory for this
machine (`~/.claude/projects/C--Users-asus/memory/`): bank-scraper ATS map,
autoapply pipeline invariants, project history.
