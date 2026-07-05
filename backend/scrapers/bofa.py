"""Bank of America careers scraper.

careers.bankofamerica.com exposes a JSON servlet:
    GET /services/jobssearchservlet?start=N&rows=500&search=getAllJobs
Its term/location filters are ignored server-side, so we paginate the full
list (~2k roles, 4 requests at rows=500) and filter to Europe client-side on
the `country` field. `jobDescriptionExternal` ships inline — no per-offer
enrichment needed.

NB: BofA's campus programmes (summer analyst) live on a separate, event-gated
Avature site (bac.avature.net, 403) — this catches everything posted to the
main board (off-cycle, graduate, any intern roles listed there).
"""
from __future__ import annotations

import logging

import httpx

from .base import BankScraper, ScrapedOffer, html_to_text

logger = logging.getLogger(__name__)

API = "https://careers.bankofamerica.com/services/jobssearchservlet"
JOB_URL_PREFIX = "https://careers.bankofamerica.com"
PAGE_SIZE = 500
MAX_ROWS = 4000

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


class BofAScraper(BankScraper):
    bank_name = "Bank of America"
    careers_url = "https://careers.bankofamerica.com/en-us/job-search"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=40, headers=HEADERS) as client:
            start = 0
            while start < MAX_ROWS:
                try:
                    r = await client.get(API, params={
                        "start": start, "rows": PAGE_SIZE, "search": "getAllJobs",
                    })
                    r.raise_for_status()
                    data = r.json()
                except Exception as e:
                    logger.warning("BofA start=%d failed: %s", start, e)
                    break
                jobs = data.get("jobsList") or []
                if not jobs:
                    break
                for j in jobs:
                    if (j.get("country") or "") not in EUROPE_COUNTRIES:
                        continue
                    jid = str(j.get("jobRequisitionId") or "")
                    if not jid or jid in offers:
                        continue
                    path = j.get("jcrURL") or ""
                    location = ", ".join(filter(None, [j.get("city"), j.get("country")]))
                    offers[jid] = ScrapedOffer(
                        bank="Bank of America",
                        external_id=jid,
                        role_title=j.get("postingTitle") or "",
                        location=location,
                        apply_url=(JOB_URL_PREFIX + path) if path.startswith("/") else (path or self.careers_url),
                        source_url=self.careers_url,
                        description=html_to_text(j.get("jobDescriptionExternal") or "") or None,
                        extras={"division": j.get("division"), "posted": j.get("postedDate")},
                    )
                start += PAGE_SIZE
                total = data.get("totalMatches") or 0
                if start >= total:
                    break
        logger.info("Bank of America: collected %d EU offers", len(offers))
        return list(offers.values())
