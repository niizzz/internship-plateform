"""Barclays careers scraper (Phenom People).

search.jobs.barclays is a Phenom SPA that renders the result list client-side
and serves only a bot shell to plain HTTP, so we drive it with Playwright.
Results live in <li class="list-item--card"> nodes; each job URL is
    /job/<city>/<slug>/13015/<id>
so the city (used for the Europe filter) and a stable id come straight from the
link. We sweep early-careers keywords, paginate by incrementing the page index
in /search-jobs/<kw>/13015/<page>, then read the rendered cards.
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

SEARCH_TMPL = "https://search.jobs.barclays/search-jobs/{kw}/13015/{page}"

# Phenom search is substring-based: "intern" covers internship, "placement"
# covers industrial placement — fewer sweeps, same coverage.
QUERIES = [
    "intern", "summer analyst", "off-cycle", "graduate",
    "spring", "apprentice", "placement", "early careers",
]

EU_HINTS = [
    "london", "edinburgh", "manchester", "birmingham", "glasgow", "northampton",
    "united kingdom", "paris", "france", "frankfurt", "germany", "geneva", "zurich",
    "switzerland", "madrid", "spain", "milan", "italy", "amsterdam", "netherlands",
    "brussels", "belgium", "luxembourg", "dublin", "ireland", "lisbon", "portugal",
    "warsaw", "poland", "prague", "czech",
]

_JOBURL_RE = re.compile(r"/job/([^/]+)/[^/]+/\d+/(\d+)")


def _looks_eu(*parts: str) -> bool:
    blob = " ".join(p or "" for p in parts).lower()
    return any(h in blob for h in EU_HINTS)


class BarclaysScraper(BankScraper):
    bank_name = "Barclays"
    careers_url = "https://search.jobs.barclays/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        for kw in QUERIES:
            kw_path = urllib.parse.quote(kw)
            kw_added = 0
            # Fresh tab per keyword — Phenom soft-throttles rapid same-tab nav.
            page = await self.new_page()
            try:
                for page_no in range(1, 6):
                    url = SEARCH_TMPL.format(kw=kw_path, page=page_no)
                    cards = []
                    # The result list is injected after the search XHR resolves;
                    # wait for the first job link instead of a fixed 2s pause.
                    for attempt in range(2):
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                            try:
                                await page.wait_for_selector('a[href*="/job/"]', timeout=10_000)
                            except Exception:
                                pass
                            await page.wait_for_timeout(400)
                            cards = await self._read_cards(page)
                            if cards:
                                break
                        except Exception:
                            await page.wait_for_timeout(1500)
                    if not cards:
                        break  # no more results → next keyword

                    new = 0
                    for c in cards:
                        m = _JOBURL_RE.search(c["href"])
                        if not m:
                            continue
                        city, jid = m.group(1).replace("-", " "), m.group(2)
                        if jid in offers:
                            continue
                        location = c.get("loc") or city
                        if not _looks_eu(location, city):
                            continue
                        new += 1
                        offers[jid] = ScrapedOffer(
                            bank="Barclays",
                            external_id=jid,
                            role_title=c["title"],
                            location=location,
                            apply_url=c["href"],
                            source_url=self.careers_url,
                            extras={"detail_url": c["href"]},
                        )
                    kw_added += new
                    if new == 0:
                        break  # page added nothing new/EU → stop paginating this kw
                    await asyncio.sleep(0.2)
            finally:
                await page.close()
            logger.info("Barclays kw=%r added %d EU offers", kw, kw_added)

        to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
        if to_enrich:
            page = await self.new_page()
            try:
                await self._enrich(page, to_enrich)
            finally:
                await page.close()

        logger.info("Barclays: collected %d EU offers (%d enriched)", len(offers), len(to_enrich))
        return list(offers.values())

    async def _read_cards(self, page) -> list[dict]:
        # Class-agnostic: collect every job link except those in the
        # "recommended jobs" carousel; the city comes from the URL itself.
        return await page.evaluate(
            """() => [...document.querySelectorAll('a[href*="/job/"]')]
                .filter(a => !a.closest('.slick-track, .slick-slide, [class*="carousel"], [class*="recommend"]'))
                .map(a => {
                    const li = a.closest('li, [class*="list-item"], article');
                    const loc = li && li.querySelector('[class*="location"], [class*="Location"]');
                    return {
                        href: a.href,
                        title: (a.innerText || '').trim().split('\\n')[0],
                        loc: loc ? loc.innerText.trim() : '',
                    };
                })
                .filter(c => c.href && c.title)""",
        )

    async def _enrich(self, page, offers: list[ScrapedOffer]) -> None:
        for o in offers:
            try:
                await page.goto(o.extras["detail_url"], wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(400)
                # Phenom embeds a JobPosting JSON-LD blob server-side — much
                # cleaner than scraping rendered text (which drags in nav/footer).
                raw = await page.evaluate(
                    """() => {
                        for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                            try {
                                const j = JSON.parse(s.textContent);
                                for (const it of (Array.isArray(j) ? j : [j])) {
                                    if (it && it['@type'] === 'JobPosting' && it.description) return it.description;
                                }
                            } catch (e) {}
                        }
                        const el = document.querySelector('.ats-description, [class*="job-description"], main');
                        return el ? el.innerHTML : '';
                    }""")
                clean = html_to_text(raw or "")
                if clean:
                    o.description = clean
            except Exception:
                continue
