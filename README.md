# Internship DB

A local platform for tracking S&T / Structuring internships across 19 banks/brokers in Europe, with automatic per-offer CV + cover-letter tailoring and an assisted-apply mode that pre-fills bank application forms.

## Stack

- **Backend:** Python 3.14, FastAPI, SQLite (SQLModel)
- **Scraping:** Playwright (Chromium) — lazy-loaded per scraper; HTTP-only scrapers (like JPMorgan via Oracle HCM API) skip it
- **LLM:** Claude Code CLI (`claude -p`, headless) — uses your Claude subscription, zero API cost. After each refresh, tailored docs are generated automatically for every new offer.
- **Assisted apply:** headful Playwright with a persistent browser profile (ATS logins survive between applications); auto-fills profile fields + uploads docs on every page of the flow, never submits for you.
- **CV handling:** pdf2docx (PDF → DOCX conversion), python-docx (paragraph-level edits preserving formatting)
- **Frontend:** Vite + React 18 + TypeScript + Tailwind + TanStack Query + React Router

## First-time setup

### Backend
```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m playwright install chromium
```

### Frontend
```bash
cd frontend
npm install
```

## Run

Two terminals.

**Terminal 1 (backend):**
```bash
cd backend
.venv/Scripts/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 (frontend):**
```bash
cd frontend
npm run dev
```

Then open http://localhost:5173.

## First-time use

1. Make sure the Claude Code CLI is installed and logged in (`claude --version`).
2. Go to **Settings** → upload your base CV (PDF or DOCX; a base cover letter is optional but improves tone-matching).
3. Fill in **Profile** (name, email, education, work authorization…) — this powers the assisted-apply autofill.
4. Click **Refresh** in the top-right to populate the database. Tailored CV + cover letter are generated automatically for every kept offer (progress chip in the header).
5. On any offer: review/download docs, then **Apply with assist** — a Chromium window opens on the bank's form, auto-filled; you review, solve any CAPTCHA, and hit Submit yourself.

## Architecture

```
backend/
  main.py                     FastAPI app + all routes + auto-tailor queue
  db.py                       SQLModel ORM + SQLite engine
  llm.py                      CV/CL tailoring via Claude Code CLI subprocess (no API key)
  apply_assist.py             Assisted-apply autofill engine (persistent headful Chromium)
  cv_utils.py                 PDF → DOCX conversion, paragraph extraction, in-place rewrites
  scrapers/
    base.py                   BankScraper base class, filter helpers (S&T detection, EU detection)
    orchestrator.py           Runs all scrapers, diffs DB, emits removal notifications
    registry.py               List of implemented scrapers
    jpmorgan.py               JPM scraper (Oracle HCM JSON API — no browser needed)
  storage/
    cvs/                      Uploaded base CV + work files
    offers/<id>_<bank>_<slug>/   Tailored CV + cover letter per offer
  data/internships.db         SQLite database

frontend/
  src/
    api.ts                    Typed API client
    App.tsx                   Layout shell + routing
    components/
      RefreshButton.tsx
      NotificationBell.tsx
      StatusBadge.tsx
    pages/
      OffersPage.tsx          List view with filters
      OfferDetailPage.tsx     Detail + CV/CL builder + status pipeline
      SettingsPage.tsx        API key + base CV upload
```

## Adding a new bank scraper

1. Create `backend/scrapers/<bank>.py` with a class subclassing `BankScraper`.
2. Implement `async def scrape(self) -> list[ScrapedOffer]`. Use `self.new_page()` if you need a browser; pure HTTP scrapers can just use `httpx` and skip the browser entirely.
3. Add the class to `ALL_SCRAPERS` in `scrapers/registry.py`.

The orchestrator handles filtering (S&T category + Europe + intern/grad), deduping, removal detection, and notifications.

## Status (all implemented)

- [x] JPMorgan (Oracle HCM API)
- [x] Citi (Workday CXS)
- [x] Goldman Sachs (GraphQL)
- [x] Morgan Stanley (Workday CXS)
- [x] UBS (BrassRing, Playwright)
- [x] BNP Paribas (WordPress REST)
- [x] HSBC (Avature)
- [x] Deutsche Bank (BeeSite API)
- [x] Barclays (Phenom, Playwright)
- [x] Société Générale (Exalead via Playwright)
- [x] Santander (Workday)
- [x] Crédit Agricole CIB (Talentsoft)
- [x] Natixis (BPCE WordPress API)
- [x] Lazard (Oracle HCM)
- [x] Rothschild & Co
- [x] Bank of America (jobssearchservlet)
- [x] Commerzbank (BeeSite API, same vendor as DB)
- [x] Kepler Cheuvreux (Teamtailor JSON feed)
- [x] Nomura (nomuracampus.tal.net — empty board off-season is a clean zero)
