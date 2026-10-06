"""BNP Paribas careers scraper — two boards.

1. GROUP board, `group.bnpparibas/en/careers/all-job-offers` — every entity
   and country (CIB Paris Global Markets, Fortis, BNL, BGL, AM, ...). This is
   where the bulk of BNP's European S&T internships and VIEs live; the UK
   site below only mirrors a handful of London postings. It sits behind
   Akamai Bot Manager: plain httpx AND headless Chromium (even real Chrome
   with --headless=new) get a 403 "Access Denied". A HEADFUL real-Chrome
   session (`channel="chrome"`, parked off-screen) passes the sensor in a few
   seconds, and the cookies it earns then work for plain httpx — so the
   browser is only a ~5s cookie warm-up and all paging/detail fetches are
   HTTP. The board is server-rendered, 10 cards per page (`?page=N`), one
   path per contract type (`/trainee-internship`, `/international-volunteer-
   program`, `/graduate-programme`, `/summer-job`). EN and FR paths serve the
   same index, so only EN is swept. Detail pages carry a JobPosting JSON-LD
   (description, datePosted, locality/country, entity requisition id) and an
   "Apply" link to the bwelcome.hr.bnpparibas Avature tenant (whose own
   SearchJobs is disabled — 404 — so it can't be the listing source).

2. UK early-careers WordPress REST `careers.bnpparibas.co.uk/wp-json/wp/v2/
   gbjf_job_post` — public, kept as a second source (London roles are
   sometimes posted there first).
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, html_to_text, in_europe, is_internship_or_grad, is_snt_role

logger = logging.getLogger(__name__)

GROUP = "https://group.bnpparibas"
GROUP_LIST = GROUP + "/en/careers/all-job-offers"
# Contract-type paths on the group board that hold student/early-careers roles.
GROUP_TYPES = {
    "trainee-internship": "Internship",
    "international-volunteer-program": "VIE",
    "graduate-programme": "Graduate Programme",
    "summer-job": "Summer Job",
}
MAX_PAGES = 80          # safety stop; internships are ~31 pages today
ENRICH_CAP = 120
# Akamai starts answering 403 to detail fetches at concurrency 5 (102 of
# 200 failed in testing); 3 with a backoff retry stays clean.
ENRICH_CONCURRENCY = 3
# A description can only RESCUE a title if the S&T filter would accept it with
# the strongest possible desk text. Titles that fail even this (marketing, HR,
# compliance, ...) are dropped by the real filter anyway — don't fetch them.
_BEST_CASE_DESC = "Global Markets FICC equity derivatives structured products sales trading"

UK_API = "https://careers.bnpparibas.co.uk/wp-json/wp/v2/gbjf_job_post"
PER_PAGE = 50

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

_CARD_RE = re.compile(r'<article class="card-custom card-offer[^"]*">(.*?)</article>', re.S)
_CARD_HREF_RE = re.compile(r'href="(/en/careers/job-offer/([^"/?#]+))"')
_CARD_TITLE_RE = re.compile(r'<h3[^>]*>(.*?)</h3>', re.S)
_CARD_LOC_RE = re.compile(r'<div class="offer-location">(.*?)</div>', re.S)
_CARD_ENTITY_RE = re.compile(r'<div class="offer-logo">.*?alt="([^"]*)"', re.S)
_LDJSON_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
_APPLY_RE = re.compile(r'href="(https://bwelcome\.hr\.bnpparibas/[^"]*JobDetails\?jobId=[^"]+)"')

_COUNTRY_ISO = {
    "FR": "France", "GB": "United Kingdom", "UK": "United Kingdom", "BE": "Belgium",
    "LU": "Luxembourg", "IT": "Italy", "DE": "Germany", "ES": "Spain", "PT": "Portugal",
    "NL": "Netherlands", "CH": "Switzerland", "IE": "Ireland", "PL": "Poland",
    "AT": "Austria", "SE": "Sweden", "DK": "Denmark", "NO": "Norway", "FI": "Finland",
    "MC": "Monaco", "CZ": "Czech Republic", "GR": "Greece",
}


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class BNPParibasScraper(BankScraper):
    bank_name = "BNP Paribas"
    careers_url = GROUP_LIST

    async def scrape(self) -> list[ScrapedOffer]:
        # Either board failing RAISES: persist() deactivates every stored BNP
        # offer missing from a "successful" scrape, so returning only the UK
        # rows after an Akamai block would wipe all the group-board offers.
        # Raising makes the orchestrator leave the bank's rows untouched.
        try:
            group = await self._scrape_group()
        except Exception as e:
            raise RuntimeError(f"BNP Paribas group board failed: {e}") from e
        uk = await self._scrape_uk()

        # The UK site mirrors London postings that are also on the group
        # board. Keep the group copy (bwelcome apply link, JSON-LD location)
        # but give it the UK slug as its id: London rows were stored under
        # that slug before the group board was scraped, so re-keying them
        # would deactivate rows that carry tailored docs / applications.
        by_title = {_title_key(o.role_title): o for o in group}
        offers: dict[str, ScrapedOffer] = {}
        for u in uk:
            g = by_title.pop(_title_key(u.role_title), None)
            if g is not None:
                g.external_id = u.external_id
                g.description = g.description or u.description
                g.posted_raw = g.posted_raw or u.posted_raw
                offers.setdefault(g.external_id, g)
            else:
                offers.setdefault(u.external_id, u)
        # Iterate `group`, not by_title: two group postings can share a title
        # (two Lisbon "Structured Products and Investment Solutions Trainee"
        # seats) and by_title only holds one of them.
        for g in group:
            offers.setdefault(g.external_id, g)
        logger.info("BNP Paribas: collected %d offers", len(offers))
        return list(offers.values())

    # --- group.bnpparibas -------------------------------------------------

    async def _warm_cookies(self) -> tuple[dict, str]:
        """Pass Akamai once in headful real Chrome; return (cookies, user agent)."""
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                channel="chrome",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--window-position=-2400,-2400",
                    "--window-size=800,600",
                ],
            )
            try:
                ctx = await browser.new_context(locale="en-GB")
                page = await ctx.new_page()
                resp = await page.goto(GROUP_LIST, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(4000)
                if resp is None or resp.status >= 400:
                    # The sensor sometimes needs a second navigation.
                    resp = await page.reload(wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(3000)
                if resp is None or resp.status >= 400:
                    raise RuntimeError(f"Akamai warm-up got HTTP {resp.status if resp else '??'}")
                cookies = {c["name"]: c["value"] for c in await ctx.cookies()}
                ua = await page.evaluate("navigator.userAgent")
            finally:
                await browser.close()
        return cookies, ua

    async def _scrape_group(self) -> list[ScrapedOffer]:
        cookies, ua = await self._warm_cookies()
        found: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(
            cookies=cookies, headers={"User-Agent": ua}, timeout=30, follow_redirects=True
        ) as client:
            for path, label in GROUP_TYPES.items():
                await self._sweep_type(client, path, label, found)
            if not found:
                raise RuntimeError("group board returned zero cards on every contract type")

            # Enrich European cards (description, posted date, apply link). The
            # listing has no description, and generic titles can only be kept
            # via a description desk phrase, so every European card is worth it.
            eligible = [
                o for o in found.values()
                if in_europe(o.location) and is_snt_role(o.role_title, _BEST_CASE_DESC)
            ]
            if len(eligible) > ENRICH_CAP:
                logger.warning("BNP group: enrich capped at %d of %d European cards", ENRICH_CAP, len(eligible))
                eligible = eligible[:ENRICH_CAP]
            sem = asyncio.Semaphore(ENRICH_CONCURRENCY)
            self._blocked = 0

            async def one(o: ScrapedOffer):
                async with sem:
                    # Repeated 403s mean Akamai has flagged the session; pushing
                    # on escalates it to an IP-wide block of the whole site
                    # (seen in testing, lasting well past the run). Stop and
                    # keep the cards — titles still classify without a desc.
                    if self._blocked >= 4:
                        return
                    await self._enrich(client, o)
                    await asyncio.sleep(0.4)

            await asyncio.gather(*(one(o) for o in eligible))
        logger.info("BNP group: %d cards, %d in Europe", len(found), sum(in_europe(o.location) for o in found.values()))
        return list(found.values())

    async def _sweep_type(self, client: httpx.AsyncClient, path: str, label: str, found: dict):
        before = len(found)
        for page in range(1, MAX_PAGES + 1):
            url = f"{GROUP_LIST}/{path}" + (f"?page={page}" if page > 1 else "")
            try:
                r = await client.get(url)
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                if page == 1:
                    raise RuntimeError(f"group /{path} page 1 failed: {e}") from e
                logger.warning("BNP group /%s page %d failed (partial results kept): %s", path, page, e)
                return
            cards = _CARD_RE.findall(r.text)
            if not cards:
                break
            new = 0
            for card in cards:
                o = self._parse_card(card, label)
                if o and o.external_id not in found:
                    found[o.external_id] = o
                    new += 1
            if new == 0:  # past the last page some boards repeat the final page
                break
            await asyncio.sleep(0.3)  # pace the sweep — see the Akamai note in _scrape_group
        else:
            logger.warning("BNP group /%s hit MAX_PAGES=%d — board may be truncated", path, MAX_PAGES)
        logger.info("BNP group /%s: %d cards", path, len(found) - before)

    def _parse_card(self, card: str, label: str) -> ScrapedOffer | None:
        m = _CARD_HREF_RE.search(card)
        t = _CARD_TITLE_RE.search(card)
        if not m or not t:
            return None
        href, slug = m.groups()
        title = _clean(t.group(1))
        loc = _clean((_CARD_LOC_RE.search(card) or [None, ""])[1])
        entity = _clean((_CARD_ENTITY_RE.search(card) or [None, ""])[1])
        # The board files these under an internship/VIE/graduate contract type,
        # but the platform's program gate reads the TITLE only. Titles such as
        # "Structuring Assistant - Global Markets" carry no program word, so
        # tag them with the board's own contract label to keep them visible.
        if not is_internship_or_grad(title):
            title = f"{title} ({label})"
        return ScrapedOffer(
            bank=self.bank_name,
            external_id=f"grp-{slug}",
            role_title=title,
            location=loc,
            apply_url=GROUP + href,
            source_url=GROUP + href,
            program_type=label,
            extras={"entity": entity} if entity else {},
        )

    async def _enrich(self, client: httpx.AsyncClient, o: ScrapedOffer):
        for attempt in range(3):
            try:
                r = await client.get(o.source_url)
                r.raise_for_status()
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 403:
                        self._blocked += 1
                        if self._blocked == 4:
                            logger.warning("BNP group: Akamai is blocking detail pages — stopping enrichment")
                    logger.warning("BNP group detail failed %s: %s", o.source_url, e)
                    return
                await asyncio.sleep(2.0 * (attempt + 1))
        h = r.text
        for block in _LDJSON_RE.findall(h):
            try:
                d = json.loads(block)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("@type") != "JobPosting":
                continue
            o.description = html_to_text(d.get("description") or "") or None
            o.posted_raw = d.get("datePosted") or o.posted_raw
            addr = ((d.get("jobLocation") or {}).get("address") or {})
            city = _clean(addr.get("addressLocality") or "")
            cc = (addr.get("addressCountry") or "").upper()
            country = _COUNTRY_ISO.get(cc)
            if city and country:
                o.location = f"{city}, {country}"
            ident = (d.get("identifier") or {}).get("value")
            if ident:
                o.extras["requisition"] = ident
            break
        a = _APPLY_RE.search(h)
        if a:
            o.apply_url = html.unescape(a.group(1))

    # --- careers.bnpparibas.co.uk -----------------------------------------

    async def _scrape_uk(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            page = 1
            while True:
                try:
                    r = await client.get(UK_API, params={"per_page": PER_PAGE, "page": page})
                    if r.status_code == 400:  # past last page
                        break
                    r.raise_for_status()
                    items = r.json()
                except Exception as e:
                    if not offers:
                        raise RuntimeError(f"BNP Paribas UK: page 1 failed: {e}") from e
                    logger.warning("BNP UK page %d failed (partial results kept): %s", page, e)
                    break
                if not items:
                    break
                for it in items:
                    # Identify a job by its SLUG, not the WordPress post id.
                    # This site re-imports every posting nightly with a fresh
                    # post id (all 8 live rows share one `modified` timestamp),
                    # so keying on the id made every BNP job look brand new
                    # each day: the previous row was deactivated, a duplicate
                    # was inserted, and first_seen_at reset — 38 stored rows
                    # for 9 real jobs. The slug survives the re-import.
                    jid = _clean(str(it.get("slug") or "")) or str(it.get("id") or "")
                    if not jid or jid in offers:
                        continue
                    title = _clean((it.get("title") or {}).get("rendered") or "")
                    city = _clean(it.get("city") or "")
                    location = city or "London, United Kingdom"
                    if "kingdom" not in location.lower() and "london" not in location.lower():
                        location = f"{location}, United Kingdom" if location else "United Kingdom"
                    desc = html_to_text((it.get("content") or {}).get("rendered") or "")
                    offers[jid] = ScrapedOffer(
                        bank="BNP Paribas",
                        external_id=jid,
                        role_title=title,
                        location=location,
                        apply_url=it.get("link") or "https://careers.bnpparibas.co.uk/",
                        source_url="https://careers.bnpparibas.co.uk/",
                        description=desc or None,
                        program_type=_clean(str(it.get("programme") or "")) or None,
                        # date_posted / WordPress `date` is the POSTING date (used
                        # for posted_at); the real START date is derived from text.
                        posted_raw=it.get("date_posted") or it.get("date"),
                    )
                page += 1
                if len(items) < PER_PAGE:
                    break
        logger.info("BNP Paribas: collected %d UK offers", len(offers))
        return list(offers.values())
