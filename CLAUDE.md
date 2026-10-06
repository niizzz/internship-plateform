# Internship Plateform — agent handoff notes

Personal, single-user platform for landing a Sales & Trading internship:
scrapes 28 bank career sites, tailors CV/cover letters per offer via the
Claude Code CLI, tracks the application funnel, and assist-fills ATS forms.
Owner: Nizar (user's spelling "plateform" is intentional).

## Run / verify

- Fresh PC: `launch.bat` runs `setup.bat` until `backend/.venv/.setup-ok`
  exists (winget-installs Python 3.14 + Node LTS if missing, venv, pinned
  pip install, playwright chromium, `npm ci`). `backend/requirements.txt` is
  an EXACT pip freeze — keep it pinned: an unpinned install pulled sqlmodel
  0.0.47, which rejects naive datetimes, so every offer failed to save on a
  friend's machine. Re-freeze after any deliberate upgrade.
- macOS: `Start (Mac).command` (LF endings, exec bit) = setup + launch in one;
  Python 3.14 via `uv` (no sudo/Homebrew), Node into `.tools/node`. pywin32 is
  marker-gated to win32. Pinned wheels verified to exist for cp314 macOS
  arm64 + x86_64 (opencv needs macOS 14+ on Intel). Not yet run on a real Mac.
- One-click: `launch.bat` (backend uvicorn on 127.0.0.1:8000, Vite on :5173).
  NB `timeout /t` inside the .bat breaks under non-interactive shells — start
  the two processes yourself if driving from an agent shell.
- Backend tests (plain-python, no pytest), from `backend/`:
  `.venv/Scripts/python.exe -m tests.test_persist_guards` (and
  `test_location_and_rules`, `test_llm_guards`, `test_cache_and_db`,
  `test_offer_search`, `test_pipeline`, `test_outreach`,
  `test_linkedin_import`, `test_events`), plus
  `.venv/Scripts/python.exe test_filter.py` (S&T filter, 63 checks).
  ALL of these must stay green before touching persist/filter/llm code.
- Frontend: `npx tsc -b && npx vite build` from `frontend/`.
- If apply-assist, the CV/CL render, or the Playwright scrapers (UBS/SocGen/
  Barclays) all fail at once with "Executable doesn't exist ... chromium-<N>",
  the venv's playwright and the downloaded browsers are out of sync (a newer
  playwright elsewhere prunes old builds). Fix:
  `.venv/Scripts/python.exe -m playwright install chromium`. Three subsystems
  share that binary, so check all three after a playwright upgrade.

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
- `backend/outreach_api.py` (APIRouter, mounted in main) + `backend/outreach_llm.py`
  — networking: contacts, per-contact message DRAFTS via the Claude CLI, and the
  follow-up clock. Two channels (linkedin/email) x two asks (chat/referral);
  email drafts also get a subject and mailto/Gmail compose URLs. Drafting only;
  nothing here touches LinkedIn.
- `backend/email_patterns.py` — derives a LIKELY work address from a name plus
  the employer's published mail format (note: mail domain != web domain, e.g.
  Goldman is gs.com). Derivation only: no SMTP probe, no lookup, no broker.
- `backend/linkedin_import.py` — ranks who to approach from the user's OWN
  LinkedIn data export (Connections.csv), reusing the offer filter's S&T
  vocabulary from `scrapers/base.py`. An import, never a scrape. Bulk selection feeds
  `outreach_api.bulk_draft`, a background thread (mirrors
  `main._auto_tailor_worker`: lock + state dict + usage-limit break) that writes
  one individually-researched message per contact. `GET /api/outreach/queue` is
  the send step: unsent drafts, reviewed and sent one at a time by hand.
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
9. ApplicationEvent is APPEND-ONLY: one row per real status transition. Never
   write one for a no-op re-click or a notes edit — the pipeline's stage clock
   (`days_in_stage`) reads the last transition, and updated_at cannot stand in
   because every notes save moves it.
10. `external_id` must be a key the ATS keeps STABLE across re-imports. BNP's
    WordPress re-imports every posting nightly with a new post id, so
    `scrapers/bnp.py` keys on the `slug`; keying on the id produced 38 rows for
    9 real jobs, reset first_seen_at daily and fired phantom "offer removed"
    notifications. Check this before adding any WordPress/feed-based scraper.
11. Outreach NEVER sends and NEVER reads LinkedIn. Bulk DRAFTING is fine and
   is the point; bulk SENDING is not built and must not be. No SMTP, no stored
   credential, no LinkedIn session — if a future request asks for "send them
   all", the answer is the send queue, not a sender — the user asked directly
   for account access on 2026-09-01 and was declined. The queue's shortcuts
   (o/s/c/j/k) arm only on hover/focus: `s` marks sent, which has NO undo, so a
   stray keystroke elsewhere on the page must not reach it. It drafts, stores and
    schedules; `mark_sent` records something the user did by hand. LinkedIn's
    User Agreement forbids automated access and a ban costs the account the
    whole hunt runs on — do not add a sender, a scraper, or a browser driver
    here. The drafter is also forbidden from inventing a shared tie: only
    `Contact.shared_context` (user-entered) may be referenced, and an empty one
    is passed to the prompt as an explicit "NONE GIVEN, do not manufacture one".
    "Sending" is a HANDOFF: `_send_links` builds mailto/Gmail compose URLs the
    user opens and presses send in themselves. No SMTP, no stored credential.
    `tests/test_outreach.py` asserts all of it.
12. Follow-up cadence is 7 then 14 days, then STOP (`FOLLOWUP_DAYS`,
    `MAX_FOLLOWUPS`). Any status where they engaged or the thread died clears
    `next_followup_at` — chasing someone who replied undoes the reply.


13. Connection suggestions come from LinkedIn's own data export
    (Settings > Data privacy > Get a copy of your data > Connections), which the
    user downloads and uploads. Never add a scraper, a session cookie, or a
    headless browser to reach LinkedIn: enumerating a connection list is the
    exact behaviour its anti-bot systems catch. `linkedin_import.match_employer`
    guards two ways — word boundaries (so "INGenico" is not ING) AND an
    ambiguity check for aliases that are ordinary words or surnames, since
    "Deutsche Telekom"/"Deutsche Post"/"Citadel Bakery"/"Chase Plumbing" would
    otherwise all read as banks. Only rows matching a finance employer AND a
    front-office markets role are stored; the rest of the network is counted and
    discarded rather than retained.
14. A connection NOTE requires a Connect button, which does not exist for
   someone you are already connected to. Everyone promoted from the LinkedIn
   export is 1st-degree, so `Contact.is_connection` is set on promotion and
   both `draft_message` and `_batch_worker` coerce connection_note -> dm for
   them (and for any email draft). Shipping the note default here sent the user
   to a profile with no Connect button and nowhere to paste. The UI also states
   the LinkedIn step per kind: Connect > Add a note for a note, Message for a DM.
15. A DERIVED email is never a send target. `Contact.email` (shared by the
   person or typed by the user) is the only field `_resolve_channel` and
   `_send_links` read; `Contact.email_guess` is offered in the UI and becomes
   real only when the user copies it across. Never wire the guess into
   channel:auto or a compose URL, and never add SMTP verification — probing a
   bank's MX to test a guess is directory harvesting, gets the IP blocklisted,
   and returns nothing because those domains are catch-all.
16. The CV is the only source of facts about the CANDIDATE. `shared_context`
    describes the TIE and is NOT a source of the candidate's own credentials: a
    sloppy note ("same MSc at Dauphine") otherwise relocates him to a school the
    CV never mentions, and the subject line and body then disagree. `_contact_block`
    emits a CONFLICT RULE next to the tie whenever one is supplied — the rule
    only worked once it sat against the data, not as one bullet among many at the
    top of the system prompt.
17. EVENTS (`scrapers/events.py`, table `recruitingevent`, page /events):
    bank recruiting/networking events from public feeds — JPM events.json,
    GS Avature SearchEvents table, DB S3 events.xml (only shown_externally +
    signup_link), HSBC Eightfold /api/events/open/list, BofA tal.net boards
    2+4. Europe-only (`is_european`), past events dropped. Sources RAISE on
    failure so a broken feed never deactivates a bank's events. Refresh keeps
    user fields (reg_status/notes/hidden); manual events are never touched.
    `relevance` = markets | general | other ("capital markets" is IB, not
    markets). Times are WALL-CLOCK at the event + a `timezone` label — never
    convert to UTC (JPM/DB publish no zone). Registering is a link out; the
    platform never registers on the user's behalf.
18. EMAIL DIGEST (`notifier.py`) is SELF-NOTIFICATION ONLY: one recipient,
    `Settings.notify_email`, the user's own address. It is the only SMTP in
    the codebase and must never be reachable from outreach (invariant 11
    stands: outreach still has no sender). Only items first seen after
    `notify_since` (set when alerts are switched on) are mailed, emailed_at is
    stamped ONLY after a successful send (failures retry next refresh), and
    events are mailed only if registration is open and relevance is
    markets/general. SMTP app password lives in the local DB, never returned
    by the API.
19. AUTO-REFRESH: `main._auto_refresh_loop` (started on startup) refreshes
    every `Settings.auto_refresh_hours` (default 24) measured from the last
    line of refresh_history.jsonl, so it survives restarts and catches up
    after the PC was off; at most one auto attempt per hour if refreshes
    crash. It only runs while the backend is running.

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
- 2026-07-25 bank additions (NEXT_BANKS.md work, all done): HSBC re-pointed
  to the Emerging Talent board (apply.careers.hsbc.com — real Markets S&T
  student roles, GSC gap closed); UniCredit added (careers.unicredit.eu
  Avature — surfaces Milan/Munich S&T interns live); Mizuho added
  (careers.mizuhoemea.com SuccessFactors RMK — London EMEA, off-season now);
  Deutsche Bank verified (EN==DE sweep, no change); Rothschild verified +
  filter now catches its Redburn Equity Sales intern. Filter learned
  "equity sales"/Redburn and "tirocinio"/"working student" (test_filter 63).
- KNOWN: Barclays (Playwright, Phenom bot wall) can exceed the 120s scraper
  timeout and land in `failed` — expected; the zero-streak guard preserves
  its offers. Mizuho detail pages are a JS-shell to httpx so descriptions
  aren't enriched (location comes from the slug); title-based classification
  is unaffected. HSBC/Mizuho/Deutsche Bank keep 0 S&T off-season by design —
  their S&T intern intakes open ~Sept-Nov 2026.
- Sept–Nov 2026 is the posting season for GS/JPM/MS/Citi/UBS/BofA/HSBC summer
  2027 internships — expect offer volume to jump; enrich caps log truncation.
- 2026-08-17 UBS: `jobs.ubs.com` has TWO BrassRing sites — 5012 (professional,
  all we scraped) and **5131 = the graduates board**, where every internship /
  Graduate Talent Program lives. `scrapers/ubs.py` now does both, paginates via
  `a.showMoreJobs` (the old next-selectors matched nothing, capping every run at
  50 cards) and enriches only Europe + early-careers cards to stay inside the
  120s budget. Live result: "2027 Summer Internship - Global Markets - London".
  Its apply flow is BrassRing behind a privacy-disclaimer gate + a candidate
  account; the Availability step's custom comboboxes/date pickers are still
  unsupported by the assist engine (see the autofill-coverage memory).
- INVARIANT ADDED: a scraper may only set `ScrapedOffer.category` to a real
  `Category` value — `orchestrator._valid_category` now enforces it. Passing an
  ATS's own taxonomy string used to bypass the S&T filter entirely (it let 64
  non-S&T UBS internships into the DB); put such labels in `extras`.
- 2026-08-25 Santander: the scraper swept ONLY the "Early Careers" job family,
  which is why it had never produced a single row. Santander files a posting
  under exactly one `jobFamilyGroup`, and its markets roles go to `Trading` /
  `Sales` — invisible to that sweep. `scrapers/santander.py` now unions several
  slices (Early Careers + Trading + Sales, workerSubType Intern + Apprentice,
  and free-text S&T sweeps) and DISCOVERS facet ids by descriptor at run time
  (a stale hardcoded id silently returns the unfiltered board instead of
  erroring). Multi-site postings that Workday collapses to "4 Locations" are
  now kept through the prefilter and resolved from the detail payload
  (`location` + `additionalLocations` + `country`), which also backfills
  `posted_raw`/`start_date_raw`. Coverage 7 -> 69 candidates in ~36s.
  Three filter bugs found by that sweep, all fixed in `scrapers/base.py`:
    * "Boadilla del Monte" — Ciudad Santander, where the SCIB Madrid trading
      floor sits — failed `in_europe`, so every Santander HQ role was dropped
      as non-European. Added (+ "Porto"/"Oporto").
    * `_PROGRAM_RE` had FR/IT/DE early-careers words but NO Spanish or
      Portuguese, so "Becario"/"Prácticas"/"Estágio"/"Estagiário" postings were
      not internships to the platform. This silently affected BBVA too.
    * `_STRONG_SNT`/`_VERY_STRONG_SNT` had the French "salle des marchés" but no
      Iberian desk terms; added "sala de mercados", "renta fija/variable",
      "renda fixa/variável", "mercados globales/globais", "productos
      estructurados", "mesa de negociación". Bare "mercado(s)" is deliberately
      excluded — "riesgos de mercado" is market RISK, not a desk.
  Guard added with them: `parse_location` now rejects `_NON_EUROPE_TRAPS`
  (Porto Alegre/Velho/Seguro) BEFORE city matching — Santander Brasil posts
  ~275 roles and whole-word "porto" would have read them all as Portugal.
  `test_filter.py` grew 8 cases (79) and `test_location_and_rules` 8 more.
  NB `test_filter.py` used to `exit 0` even with failures — it now exits 1, so
  any runner checking the exit code sees a broken filter.
  LIVE RESULT: 0 S&T internships. Santander's European S&T postings today are
  all experienced hires (Global Head of FX Forwards, Rates Structuring
  Associate, SCIB Sales-Structuring ED, VP FX Structuring London, Senior
  Institutional Sales Madrid). A true zero, not a broken filter — verified by
  pushing synthetic Santander-style S&T internships through the real gate.
  Expect intake ~Sept-Nov 2026 like the other banks.
- 2026-09-15 Bank of America: the campus programmes were NEVER on the
  careers.bankofamerica.com servlet (it is the lateral/experienced board and
  its /en-us/students/job-search is the same servlet). They live on a Lumesse
  tal.net board, `bankcampuscareers.tal.net` (board 1 = "Global Programs",
  Atom feed at `/vx/mobile-0/candidate/jobboard/vacancy/1/feed` returns the
  whole board in one request with Program ID / City / closing date / region /
  country / LOB / Program type / published date; boards 2+6 are events, 5 is
  Japan). `scrapers/bofa.py` now sweeps both, ids prefixed `campus-`, full
  descriptions from the tal.net detail fields via the new shared
  `base.talnet_detail_fields` (Nomura uses it too). The description's only
  date is the FULL-TIME conversion ("commence in July 2028"), so the scraper
  sets `start_date_raw` from the title year + programme type. Filter learned
  "prime financing" (BofA's prime-brokerage desk) and now rejects "chief
  operating office"/"COO" titles (business management, not a desk seat);
  `_SEASON_RE` accepts "Off-cycle 2027" as well as "2027 Off-cycle".
  Live: 160 feed entries → 76 EMEA → 8 S&T kept (S&T Rotational Summer London
  x2, S&T Off-Cycle Paris/Frankfurt, Prime Financing placement, QSDG x2,
  Quant Research Paris), deadline 11 Oct 2026. test_filter 88.
- 2026-09-26 BNP Paribas: `scrapers/bnp.py` now sweeps the GROUP board
  (group.bnpparibas, all entities/countries — 355 early-careers cards) plus the
  UK WordPress board. The group site is Akamai-walled: a ~5s HEADFUL real-Chrome
  warm-up (needs Google Chrome installed; a window opens off-screen) earns
  cookies that httpx then reuses. Don't raise ENRICH_CONCURRENCY above 3 —
  bursts trigger a temporary IP-wide block. 4 -> 18 S&T kept.
- Deferred nice-to-haves: dry-run mode for apply-assist; surface per-offer
  tailoring failures as a list; commit dist of the demo somewhere if the user
  wants versioning.

Deeper session-by-session context lives in the Claude auto-memory for this
machine (`~/.claude/projects/C--Users-asus/memory/`): bank-scraper ATS map,
autoapply pipeline invariants, project history.
