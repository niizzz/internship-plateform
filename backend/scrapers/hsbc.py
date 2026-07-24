"""HSBC careers scraper (Avature).

HSBC runs on Avature at mycareer.hsbc.com — NOT Workday. The search results
render server-side as HTML at:
    /en_GB/external/SearchJobs/<keyword>?listFilterMode=1&pipelineRecordsPerPage=N
Each result is an <article class="article--result"> card carrying the title,
a PipelineDetail link, and a location. We parse the cards over a set of
early-careers keywords, then enrich the likely-internship survivors with their
detail-page description so the S&T description scan has something to work with.
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

SEARCH_TMPL = "https://mycareer.hsbc.com/en_GB/external/SearchJobs/{kw}?listFilterMode=1&pipelineRecordsPerPage=100"

# Early-careers keywords — HSBC's search is substring-based, so these narrow to
# student/graduate postings; the orchestrator's filters do the final trim.
QUERIES = [
    "intern", "internship", "summer analyst", "off-cycle", "industrial placement",
    "graduate", "spring", "apprentice", "placement", "early careers",
]

EU_HINTS = [
    "london", "edinburgh", "manchester", "birmingham", "glasgow", "united kingdom",
    "paris", "france", "frankfurt", "germany", "geneva", "zurich", "switzerland",
    "madrid", "spain", "milan", "italy", "amsterdam", "netherlands",
    "brussels", "belgium", "luxembourg", "dublin", "ireland", "lisbon", "portugal",
    "warsaw", "krakow", "kraków", "poland", "prague", "czech",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

# One <article class="article--result"> ... </article> card.
_CARD_RE = re.compile(r'<article class="article article--result">(.*?)</article>', re.S)
_TITLE_RE = re.compile(r'article__header__text__title[^>]*>\s*<a href="([^"]+)">\s*(.*?)\s*</a>', re.S)
_LOC_RE = re.compile(r'item--location[^>]*>(.*?)</', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ID_RE = re.compile(r"/(\d+)/?$")


def _clean(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", s or "")).strip()


def _looks_eu(s: str) -> bool:
    sl = (s or "").lower()
    return any(h in sl for h in EU_HINTS)


class HSBCScraper(BankScraper):
    bank_name = "HSBC"
    careers_url = "https://mycareer.hsbc.com/en_GB/external/SearchJobs"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            sem = asyncio.Semaphore(4)

            async def search(kw: str) -> str:
                async with sem:
                    try:
                        r = await client.get(SEARCH_TMPL.format(kw=kw))
                        r.raise_for_status()
                        return r.text
                    except Exception as e:
                        logger.warning("HSBC search %r failed: %s", kw, e)
                        return ""

            pages = await asyncio.gather(*(search(kw) for kw in QUERIES))
            if not any(pages):
                # Every keyword request failed — the board is unreachable or
                # moved. Raise so this counts as a FAILED scrape, not "HSBC
                # removed everything".
                raise RuntimeError("HSBC: all search requests failed")
            for text in pages:
                for card in _CARD_RE.findall(text):
                    tm = _TITLE_RE.search(card)
                    if not tm:
                        continue
                    link = tm.group(1).replace("&amp;", "&")
                    title = _clean(tm.group(2))
                    lm = _LOC_RE.search(card)
                    location = _clean(lm.group(1)) if lm else ""
                    if not _looks_eu(location):
                        continue
                    idm = _ID_RE.search(link)
                    ext_id = idm.group(1) if idm else link.rsplit("/", 1)[-1]
                    if not ext_id or ext_id in offers:
                        continue
                    offers[ext_id] = ScrapedOffer(
                        bank="HSBC",
                        external_id=ext_id,
                        role_title=title,
                        location=location,
                        apply_url=link,
                        source_url=self.careers_url,
                        extras={"detail_url": link},
                    )

            # Enrich only the likely internships (program word in title) with the
            # detail page so the S&T description scan can run on them.
            to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
            await self._enrich(client, to_enrich)

        logger.info("HSBC: collected %d EU offers (%d enriched)", len(offers), len(to_enrich))
        # KNOWN COVERAGE GAP (verified live 2026-07-21): mycareer.hsbc.com's
        # SearchJobs is the service-centre/experienced board — London Global
        # Markets STUDENT programmes live on a separate Avature tenant
        # (hsbc.avature.net, JS-only SPA, search path not yet captured). Until
        # that board is wired in, HSBC S&T internships will NOT appear here.
        logger.warning("HSBC: scraping the experienced/GSC board only — student "
                       "Global Markets roles are NOT visible (known gap)")
        return list(offers.values())

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        sem = asyncio.Semaphore(4)

        async def one(o: ScrapedOffer):
            async with sem:
                try:
                    r = await client.get(o.extras["detail_url"])
                    if r.status_code != 200:
                        return
                except Exception:
                    return
                await asyncio.sleep(0.15)
            # Job description sits in the main article body.
            m = re.search(r'class="article__content"[^>]*>(.*?)</article>', r.text, re.S)
            body = m.group(1) if m else r.text
            o.description = html_to_text(body) or o.description

        if offers:
            await asyncio.gather(*(one(o) for o in offers))
