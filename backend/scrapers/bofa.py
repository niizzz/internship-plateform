"""Bank of America careers scraper — TWO boards, merged.

1. CAMPUS board (Lumesse tal.net) — where EVERY internship / placement /
   summer & off-cycle analyst programme lives, EMEA included:
       https://bankcampuscareers.tal.net/vx/mobile-0/candidate/jobboard/vacancy/1/feed
   An Atom feed of board 1 ("Global Programs"); ONE request returns the whole
   board (160 entries on 2026-09-15, the HTML board pages the same set 50 at a
   time). Each entry's <content> is a "Label:value<br/>" block with Program ID,
   Title, City, closing date, region, country, LOB / Sub LOB, Program type and
   a ~250-char description snippet; <published> is the real posting date.
   Full descriptions come from the vacancy page's tal.net form fields (same
   template as Nomura's board — see `talnet_detail_fields`). Boards 2/6 are
   events and 5 is Japan-only; not swept. `campus.bankofamerica.com` and the
   /en-us/students pages only LINK here; their own job search is the lateral
   servlet below, which is why the campus roles were invisible until 2026-09-15.

2. LATERAL board (experienced hires) — the JSON servlet behind
   careers.bankofamerica.com/en-us/job-search:
       GET /services/jobssearchservlet?start=0&rows=N&search=getAllJobs
   Its term/location filters are ignored server-side, so we pull the whole list
   and filter to Europe client-side on the `country` field. Kept for the rare
   early-careers title it carries (0 in Europe all 2026 so far).

   TRAP (fixed 2026-08-23): `start` is NOT an offset — ANY non-zero `start`
   returns an EMPTY `jobsList` (with `totalMatches` still correct), so the old
   paginate-by-start loop silently stopped after page 1 and saw only the first
   500 of 2246 roles. `rows` alone is honoured and uncapped, so we do a 1-row
   probe to read `totalMatches`, then ONE request with rows=total. Never
   reintroduce a `start`-based loop.

   DESCRIPTIONS (changed 2026-08-23): the servlet's `jobDescriptionExternal` is
   None on ALL rows, so descriptions are re-fetched from the job-detail page's
   JobPosting JSON-LD, gated to internship/grad titles.

Both boards land under the same bank name; campus ids are prefixed `campus-`
so the (bank, external_id) unique index can never collide with a lateral
requisition id. `bac.avature.net` (403) is NOT a job board — it is the
application-form host the tal.net vacancy pages hand off to.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re

import httpx

from .base import (
    BankScraper, ScrapedOffer, html_to_text, is_internship_or_grad, is_snt_role,
    talnet_detail_fields,
)

logger = logging.getLogger(__name__)

BANK = "Bank of America"

# --- Campus (tal.net) ---------------------------------------------------------
CAMPUS_FEED = "https://bankcampuscareers.tal.net/vx/mobile-0/candidate/jobboard/vacancy/1/feed"
CAMPUS_BOARD = "https://bankcampuscareers.tal.net/vx/lang-en-GB/mobile-0/brand-4/candidate/jobboard/vacancy/1/adv/"
CAMPUS_EUROPE_REGION = "Europe, Middle East & Africa"   # EMEA; Dubai is dropped on country
CAMPUS_ENRICH_CONCURRENCY = 4

_ENTRY_RE = re.compile(r"<entry\b.*?</entry>", re.S)
_ENTRY_LINK_RE = re.compile(r'<link\s+rel="alternate"\s+href="([^"]+)"')
_ENTRY_PUBLISHED_RE = re.compile(r"<published>([^<]+)</published>")
_ENTRY_CONTENT_RE = re.compile(r"<content[^>]*>\s*<div[^>]*>(.*?)</div>\s*</content>", re.S)
_BR_SPLIT_RE = re.compile(r"<br\s*/?>", re.I)

# The feed's "Program type" labels ("Summer internship", "Off-cycle
# internship", "Industrial placement") already carry the words the
# orchestrator's program matchers look for; only "Full time" (= the graduate
# scheme) has no cue in either the label or the title.
_CAMPUS_PROGRAM_HINT = {"full time": "graduate programme"}

# --- Lateral (careers servlet) -----------------------------------------------
API = "https://careers.bankofamerica.com/services/jobssearchservlet"
JOB_URL_PREFIX = "https://careers.bankofamerica.com"
PROBE_ROWS = 1        # cheap request whose only job is to read totalMatches
ROWS_HEADROOM = 200   # slack for roles posted between the probe and the real call
MAX_ROWS = 20000      # sanity ceiling on a bogus totalMatches
ENRICH_CAP = 60       # detail pages per run (see DESCRIPTIONS note above)
ENRICH_CONCURRENCY = 4

_LD_RE = re.compile(
    r"""<script[^>]*type=['"]?application/ld\+json['"]?[^>]*>(.*?)</script>""",
    re.S | re.I,
)

EUROPE_COUNTRIES = {
    "United Kingdom", "France", "Germany", "Spain", "Italy", "Netherlands",
    "Belgium", "Luxembourg", "Ireland", "Switzerland", "Portugal", "Sweden",
    "Denmark", "Norway", "Finland", "Poland", "Austria", "Czech Republic",
    "Czechia", "Hungary", "Greece", "Romania",
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}
HTML_HEADERS = {**HEADERS, "Accept": "text/html,application/xhtml+xml"}


class BofAScraper(BankScraper):
    bank_name = BANK
    careers_url = "https://careers.bankofamerica.com/en-us/students"

    async def scrape(self) -> list[ScrapedOffer]:
        # Both boards run concurrently; either failing raises so a silently
        # missing board (the bug that hid campus roles) can't look like a
        # healthy zero.
        campus, lateral = await asyncio.gather(self._scrape_campus(), self._scrape_lateral())
        offers = campus + lateral
        logger.info("Bank of America: %d campus + %d lateral Europe offers",
                    len(campus), len(lateral))
        return offers

    # --- Campus board ---------------------------------------------------------

    async def _scrape_campus(self) -> list[ScrapedOffer]:
        async with httpx.AsyncClient(timeout=60, headers=HTML_HEADERS,
                                     follow_redirects=True) as client:
            try:
                r = await client.get(CAMPUS_FEED)
                r.raise_for_status()
            except Exception as e:
                raise RuntimeError(f"Bank of America: campus feed failed: {e}") from e
            entries = _ENTRY_RE.findall(r.text)
            if not entries:
                raise RuntimeError("Bank of America: campus feed returned 0 entries")

            offers: dict[str, ScrapedOffer] = {}
            for entry in entries:
                o = _campus_entry_to_offer(entry)
                if o and o.external_id not in offers:
                    offers[o.external_id] = o
            logger.info("Bank of America: campus feed %d entries, %d in Europe",
                        len(entries), len(offers))

            # The feed snippet is ~250 chars; fetch the full programme
            # description for the offers that can survive the S&T gate (the
            # title decides for every BofA programme name, so IB/tech/ops rows
            # are never fetched).
            targets = [o for o in offers.values()
                       if is_snt_role(o.role_title, o.description or "")]
            await self._enrich_campus(client, targets)
        return list(offers.values())

    async def _enrich_campus(self, client: httpx.AsyncClient,
                             offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        sem = asyncio.Semaphore(CAMPUS_ENRICH_CONCURRENCY)

        async def one(o: ScrapedOffer) -> None:
            async with sem:
                try:
                    r = await client.get(o.apply_url)
                    r.raise_for_status()
                    fields = talnet_detail_fields(r.text)
                except Exception as e:
                    logger.warning("BofA campus enrich failed for %s: %s", o.external_id, e)
                    return
            desc = html_to_text(fields.get("program description") or "")
            if desc:
                o.description = desc
            else:
                logger.warning("BofA campus: no description field on %s", o.apply_url)
            deadline = html_to_text(fields.get("application deadline") or "")
            if deadline:
                o.extras["application_deadline"] = deadline

        await asyncio.gather(*(one(o) for o in offers))
        filled = sum(1 for o in offers if o.description and len(o.description) > 300)
        logger.info("Bank of America: campus enriched %d/%d descriptions", filled, len(offers))

    # --- Lateral board --------------------------------------------------------

    async def _scrape_lateral(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=60, headers=HEADERS) as client:
            try:
                r = await client.get(API, params={
                    "start": 0, "rows": PROBE_ROWS, "search": "getAllJobs",
                })
                r.raise_for_status()
                total = int(r.json().get("totalMatches") or 0)
            except Exception as e:
                raise RuntimeError(f"Bank of America: totalMatches probe failed: {e}") from e
            if total <= 0:
                raise RuntimeError("Bank of America: totalMatches probe returned 0")

            rows = min(total + ROWS_HEADROOM, MAX_ROWS)
            try:
                r = await client.get(API, params={
                    "start": 0, "rows": rows, "search": "getAllJobs",
                })
                r.raise_for_status()
                jobs = r.json().get("jobsList") or []
            except Exception as e:
                raise RuntimeError(f"Bank of America: full fetch (rows={rows}) failed: {e}") from e
            if not jobs:
                raise RuntimeError(
                    f"Bank of America: full fetch returned 0 jobs despite totalMatches={total}"
                )
            if len(jobs) < total * 0.9:
                logger.warning(
                    "BofA: got %d jobs but totalMatches=%d - the servlet may have "
                    "started capping `rows`; check pagination", len(jobs), total,
                )

            for j in jobs:
                if (j.get("country") or "") not in EUROPE_COUNTRIES:
                    continue
                jid = str(j.get("jobRequisitionId") or "")
                if not jid or jid in offers:
                    continue
                path = j.get("jcrURL") or ""
                location = ", ".join(filter(None, [j.get("city"), j.get("country")]))
                offers[jid] = ScrapedOffer(
                    bank=BANK,
                    external_id=jid,
                    role_title=j.get("postingTitle") or "",
                    location=location,
                    apply_url=(JOB_URL_PREFIX + path) if path.startswith("/") else (path or self.careers_url),
                    source_url="https://careers.bankofamerica.com/en-us/job-search",
                    description=html_to_text(j.get("jobDescriptionExternal") or "") or None,
                    posted_raw=j.get("postedDate"),
                    extras={"board": "lateral", "division": j.get("division"),
                            "lob": j.get("lob"), "sub_lob": j.get("subLob"),
                            "posted": j.get("postedDate")},
                )
        logger.info("Bank of America: lateral %d raw roles, %d in Europe", len(jobs), len(offers))

        result = list(offers.values())
        await self._enrich_lateral(result)
        return result

    async def _enrich_lateral(self, offers: list[ScrapedOffer]) -> None:
        """Fill descriptions from each detail page's JobPosting JSON-LD.

        Only offers whose TITLE reads as an internship/grad role are fetched:
        those are the only ones that survive the orchestrator's program gate,
        and it is the S&T check right after it that actually needs the text.
        """
        targets = [o for o in offers if not o.description
                   and is_internship_or_grad(o.role_title or "")]
        if not targets:
            return
        if len(targets) > ENRICH_CAP:
            logger.warning("BofA: %d offers to enrich, truncating to %d",
                           len(targets), ENRICH_CAP)
            targets = targets[:ENRICH_CAP]

        sem = asyncio.Semaphore(ENRICH_CONCURRENCY)

        async with httpx.AsyncClient(timeout=40, headers=HTML_HEADERS,
                                     follow_redirects=True) as client:
            async def one(o: ScrapedOffer) -> None:
                async with sem:
                    try:
                        r = await client.get(o.apply_url)
                        r.raise_for_status()
                        posting = _job_posting_ld(r.text)
                    except Exception as e:
                        logger.warning("BofA enrich failed for %s: %s", o.external_id, e)
                        return
                    if not posting:
                        logger.warning("BofA: no JobPosting JSON-LD on %s", o.apply_url)
                        return
                    o.description = html_to_text(posting.get("description") or "") or None
                    if not o.posted_raw:
                        o.posted_raw = posting.get("datePosted")

            await asyncio.gather(*(one(o) for o in targets))

        filled = sum(1 for o in targets if o.description)
        logger.info("Bank of America: lateral enriched %d/%d descriptions", filled, len(targets))


def _campus_entry_to_offer(entry: str) -> ScrapedOffer | None:
    """One Atom <entry> → ScrapedOffer, or None when it is outside EMEA / malformed."""
    content = _ENTRY_CONTENT_RE.search(entry)
    link = _ENTRY_LINK_RE.search(entry)
    if not content or not link:
        return None
    fields: dict[str, str] = {}
    for line in _BR_SPLIT_RE.split(content.group(1)):
        line = html_lib.unescape(re.sub(r"<[^>]+>", "", line)).strip()
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip().lower()] = v.strip()
    pid = fields.get("program id") or ""
    title = re.sub(r"\s+", " ", fields.get("title") or "").strip()
    if not pid or not title:
        return None
    if fields.get("program region") != CAMPUS_EUROPE_REGION:
        return None
    city = fields.get("city") or ""
    country = fields.get("program country") or ""
    published = _ENTRY_PUBLISHED_RE.search(entry)
    ptype = fields.get("program type") or ""
    # BofA states the intake only as "<year> Summer/Off-Cycle Analyst" in the
    # title; the description's sole date is the FULL-TIME conversion ("may be
    # offered full time employment to commence in July 2028"), which the
    # generic extractor would otherwise take as the start. Set the start here
    # so the orchestrator's scraper-value-first rule keeps the honest one.
    start_raw = None
    year = re.search(r"\b(20\d\d)\b", title)
    season = {"summer internship": "Summer", "off-cycle internship": "Off-cycle"}.get(ptype.lower())
    if year and season:
        start_raw = f"{season} {year.group(1)}"
    return ScrapedOffer(
        bank=BANK,
        external_id=f"campus-{pid}",
        role_title=title,
        location=", ".join(x for x in (city, country) if x),
        apply_url=html_lib.unescape(link.group(1)),
        source_url=CAMPUS_BOARD,
        # Snippet only; `_enrich_campus` replaces it with the full text.
        description=fields.get("program description") or None,
        posted_raw=published.group(1) if published else None,
        start_date_raw=start_raw,
        program_type=_CAMPUS_PROGRAM_HINT.get(ptype.lower(), ptype) or None,
        # LOB / Sub LOB are BofA's own taxonomy — extras only, never `category`.
        extras={
            "board": "campus",
            "program_type": fields.get("program type"),
            "lob": fields.get("program lob"),
            "sub_lob": fields.get("program sub lob"),
            "entry_level": fields.get("entry level"),
            "application_deadline": fields.get("program closing date"),
        },
    )


def _job_posting_ld(html: str) -> dict | None:
    """Return the JobPosting JSON-LD block from a detail page, if present.

    BofA detail pages carry several JSON-LD blocks (WebPage, Organization,
    Corporation, JobPosting) — pick by @type rather than taking the first.
    """
    for m in _LD_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        for block in (data if isinstance(data, list) else [data]):
            if isinstance(block, dict) and block.get("@type") == "JobPosting":
                return block
    return None
