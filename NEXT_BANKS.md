# Adding bank coverage: HSBC · Deutsche Bank · UniCredit · Mizuho · Rothschild & Co

Instructions for the next agent session (written 2026-07-24 for Opus 4.8).
Read `CLAUDE.md` first — the invariants there are binding; this file is the
per-bank plan. Do the work from `backend/` with `.venv/Scripts/python.exe`.

## 0. Current state — do NOT build what already exists

| Bank | State | Work needed |
|---|---|---|
| Deutsche Bank | **LIVE** (`scrapers/deutschebank.py`, BeeSite JSON) | Verify + optional DE-language sweep (§3) |
| Rothschild & Co | **LIVE** (`scrapers/rothschild.py`, Workday CXS) | Verify only (§4) |
| HSBC | LIVE but **points at the wrong board** (GSC/experienced) | Re-point to the student Avature board (§2) — the main job |
| UniCredit | **NOT implemented** (probes from agents got conn-refused/geo-blocked; a real browser on this machine should get through) | Discover ATS, then implement (§5) |
| Mizuho | **NOT implemented** (careers board is a JS SPA) | Discover ATS, then implement (§6) |

Off-season reality check: US/UK/JP banks post 2027-summer S&T internships
**September–November**. In July/August a correctly wired scraper often keeps
0 offers. Judge success by RAW counts and by known full-time postings flowing
through, never by "it found internships today".

## 1. Framework contract (every scraper, no exceptions)

- One module in `backend/scrapers/`, subclass `BankScraper`, implement
  `async def scrape(self) -> list[ScrapedOffer]`. Register the class in
  `scrapers/registry.py::ALL_SCRAPERS`.
- `ScrapedOffer` required: `bank`, `external_id` (STABLE ATS requisition id —
  never a URL slug that can change), `role_title`, `location`, `apply_url`
  (the page a human applies on — the Natixis lesson: marketing "detail" links
  can 302 home; prefer the real apply/posting URL). Strongly wanted:
  `description` via `html_to_text()` (tailoring quality + the filter's
  description-rescue for generic titles) and `posted_raw` (the bank's own
  publication date; see per-ATS field map in the memory file
  `reference_bank_scraper_ats.md`).
- Pull **broad** (early-careers keywords or a Europe facet) and let
  `base.py` filters + orchestrator decide. Only cheap Europe pre-trims in the
  scraper (avoid enriching the whole planet).
- **Raise on total failure** (audit invariant): page-1 request failure with
  nothing collected → `raise RuntimeError(...)`; every keyword sweep failed →
  raise. Partial results after some success are OK, log "partial results
  kept". Copy the pattern from `citi.py` / `jpmorgan.py` / `generic.py`.
- Dedupe inside the scraper by `external_id` (dict keyed on it).
- Budget: the orchestrator kills a scraper at 120 s (`SCRAPER_TIMEOUT_S`).
  Parallelize keyword sweeps with `asyncio.Semaphore(3-4)` like JPM/Goldman.
- If the ATS is Workday / Talentsoft / Greenhouse: subclass the config-driven
  base in `scrapers/generic.py` (~6 lines, see `bbva.py` / `amundi.py` /
  `maven.py`). Oracle HCM: clone `lazard.py`. BeeSite: clone
  `commerzbank.py`. Avature HTML: clone `hsbc.py`. tal.net: clone `nomura.py`.

### Verify loop (after every change)

```
cd backend
.venv/Scripts/python.exe debug_bank.py <module> <ClassName>   # raw/prog/snt/eu/KEPT breakdown
.venv/Scripts/python.exe test_filter.py                        # 57/57 must hold
.venv/Scripts/python.exe -m tests.test_persist_guards          # + the other 3 suites
```

Then a real refresh (backend running): `POST /api/refresh`, watch
`data/app.log` and the RefreshButton ⚠ chip; confirm rows via
`GET /api/offers?bank=<name>`.

Frontend step for any NEW bank: add branding to
`frontend/src/lib/bankMeta.ts` (symbol/color/domain/blurb — the hover card
and logos read from it). Logo domain must resolve on
`icons.duckduckgo.com/ip3/<domain>.ico` or Google favicons, else monogram.

## 2. HSBC — re-point to the student board (the real prize)

Problem (verified live 2026-07-21): `mycareer.hsbc.com/en_GB/external/SearchJobs/<kw>`
is HSBC's service-centre/experienced board — every keyword returns the same
Poland "GSC" pool. Student Global Markets programmes are applied to via a
separate Avature tenant `hsbc.avature.net` (JS-rendered SPA; plain-HTTP
probes of `/careers/SearchJobs`, `/en_GB/careers/SearchJobs`,
`/campus/SearchJobs` all 404 — the search path must be captured, not guessed).

Procedure:
1. Headful capture. Launch Playwright headful (or the assist browser
   profile), go to `https://www.hsbc.com/careers/students-and-graduates`,
   drill into a programme page (`/trading`, `/sales-and-investments`) and
   click through "Apply" / "Search opportunities" until you land on the
   `hsbc.avature.net` (or another tenant's) job list.
2. In DevTools/Network (or `page.on("request")`), record: the LIST request
   (URL, method, params — Avature lists are usually server-rendered
   `SearchJobs` pages with `article--result` cards, sometimes a JSON
   `/api/...` search), the DETAIL page URL shape, and where the requisition
   id lives.
3. If the response is Avature server-rendered HTML: keep `hsbc.py`'s card
   parsing (`_CARD_RE`/`_TITLE_RE`), swap `SEARCH_TMPL`, re-check the CSS
   class names against the new tenant (they differ per Avature skin). If it's
   JSON: parse JSON instead, same keyword sweep.
4. Try plain `httpx` first; if the tenant serves only a JS shell to HTTP,
   drive it with Playwright like Barclays (fresh tab per keyword).
5. Keep `bank_name = "HSBC"` so existing rows revive instead of duplicating.
   Delete the known-gap `logger.warning` in `hsbc.py` and the `*HSBC` note in
   `registry.py` ONLY once the new board demonstrably returns student roles.
6. Off-season the student board may be legitimately empty — success criterion
   in August is "list request returns 200 with a well-formed (possibly empty)
   result set and zero GSC pollution", plus a re-run in September.

## 3. Deutsche Bank — already live; verify, then close the German gap

`deutschebank.py` paginates the full BeeSite EN index and enriches
program-matching titles. Verify with `debug_bank.py deutschebank
DeutscheBankScraper` (expect raw > 0 always — DB lists hundreds of EU roles).

Known improvement worth doing: the search payload sends
`"LanguageCode": "EN"`. German-only postings (Praktikum/Werkstudent in
Frankfurt) may exist only under `DE`. Add a second sweep with
`LanguageCode: "DE"`, merge by `PositionID` (dict dedupe — the id is
language-independent), keep the EN title when both exist. The filter already
understands praktikum/werkstudent. Measure before/after with the harness; if
the DE sweep adds zero rows, drop it and note that in the module docstring.

## 4. Rothschild & Co — already live; verify only

`rothschild.py` pulls the whole `RothschildAndCo_Lateral` Workday site (~130
roles; campus Stage/Alternance roles are folded into it) and EU-filters
client-side. Nothing to build. Run the harness; expect S&T ≈ 0 most of the
year (advisory house; the equities arm is Redburn — titles containing
"Redburn" count as real S&T signals if they ever show). If the user wants
Redburn specifically, check whether Redburn (Atlantic) posts on the same
tenant before adding anything new.

## 5. UniCredit — new scraper (discovery first)

State: `jobs.unicredit.eu` / `ucjobs.unicredit.eu` refused connections from
agent probes (likely geo/bot-blocked at the datacenter level). From this
machine a normal browser very likely works — do discovery here, in a headful
browser.

1. Start at `https://www.unicreditgroup.eu/en/careers.html` → job search.
   Follow it to the actual board and note the final host.
2. Identify the ATS family from the URL/markup — mapping:
   `*.myworkdayjobs.com` → `WorkdayScraper` subclass (6 lines; keyword mode,
   or facet mode via `WORKDAY_EUROPE_COUNTRY_IDS` if the tenant is global);
   `successfactors.eu`/`sfcareer` → SuccessFactors (no base class yet — the
   careers OData/JSON search endpoint is usually
   `.../careersection`... capture the XHR; build bespoke, keep it httpx);
   `avature.net` → clone `hsbc.py`; `fa.*.oraclecloud.com` → clone
   `lazard.py`; Taleo (`tbe.taleo.net`/`.taleo.net`) → capture the JSON API;
   Phenom → clone `barclays.py` (Playwright).
3. S&T reality: UniCredit's markets desks sit in Milan and Munich (plus some
   London). Titles arrive in EN/IT/DE; the program regex already covers
   stage/praktikum/intern/tirocinio? — NB **"tirocinio" (Italian for
   internship) is NOT in `_PROGRAM_RE`**: if UniCredit posts with it, add
   `tirocin(?:io|ante)` to `_PROGRAM_RE` in `base.py` AND add matching cases
   to `test_filter.py` (keep 57+/57 green).
4. `external_id`: use the ATS requisition id from the URL/payload, never the
   slug. `posted_raw`: per ATS map. Register + bankMeta (`UCG`,
   unicreditgroup.eu, red #E4002B-ish — check their brand).

## 6. Mizuho — new scraper (discovery first)

State: probes found only a JS SPA; the ATS behind Mizuho EMEA early-careers
was never identified. Do not trust guesses — capture it.

1. Start at `https://www.mizuhogroup.com/emea/careers` (and
   `/careers/students-and-graduates` if present). Click through to the job
   search / early-careers application portal in a headful browser.
2. Candidate hosts to check quickly along the way (unverified — one curl each
   before the browser session): `mizuhoemea.tal.net`, `mizuho.tal.net`
   (Japanese banks in London often use tal.net — Nomura does; clone
   `nomura.py` if so), any `*.avature.net`, `*.oleeo.com` / `*.wcn.co.uk`
   (Oleeo/WCN is common for London graduate schemes), `workday`, `greenhouse`.
3. Once the list XHR/page is captured: implement per the §5 mapping table.
   Mizuho EMEA is London-centric — expect small volume; S&T titles look like
   "Global Markets Summer Internship", desks under "Markets".
4. If the board is gated behind a cookie-walled SPA that Playwright must
   drive, budget it like Barclays: fresh page, `wait_for_selector` on a job
   link, small page cap, and RAISE if zero cards on every attempt.

## 7. Done-criteria for the whole task

- `debug_bank.py` shows sane raw counts for all five banks; KEPT counts
  reconciled against a manual look at each board.
- All 5 test suites green; new filter vocabulary (e.g. tirocinio) has tests.
- One commit per bank (`feat(scrapers): ...`), registry + bankMeta updated,
  `CLAUDE.md` "known gaps" updated (HSBC line removed if fixed).
- A full `POST /api/refresh` runs clean: no bank in `failed`, new banks
  visible in the dashboard, ⚠ chip empty (or explained).
- Update the auto-memory bank-map file with the captured endpoints (that file
  is the institutional memory that made this handoff possible).
