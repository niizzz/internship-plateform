"""Santander careers scraper (Workday CXS).

Santander runs Workday at santander.wd3.myworkdayjobs.com (site
`SantanderCareers`). It exposes a `jobFamilyGroup` facet with an "Early
Careers" value, so we scope to that (small, ~50 roles globally) and filter to
Europe + S&T downstream. Descriptions are fetched per posting for the S&T scan.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .base import BankScraper, ScrapedOffer, html_to_text

logger = logging.getLogger(__name__)

API = "https://santander.wd3.myworkdayjobs.com/wday/cxs/santander/SantanderCareers/jobs"
JOB_URL_TMPL = "https://santander.wd3.myworkdayjobs.com/SantanderCareers{external_path}"
DETAIL_URL_TMPL = "https://santander.wd3.myworkdayjobs.com/wday/cxs/santander/SantanderCareers{external_path}"

EARLY_CAREERS_FACET = "135a3ebce38101db91d599b919013150"  # jobFamilyGroup = Early Careers

EU_HINTS = [
    "london", "edinburgh", "manchester", "united kingdom", "paris", "france",
    "frankfurt", "germany", "madrid", "barcelona", "boadilla", "spain",
    "milan", "italy", "lisbon", "lisboa", "porto", "portugal", "warsaw", "poland",
    "amsterdam", "netherlands", "dublin", "ireland", "geneva", "zurich", "switzerland",
]

PAGE_SIZE = 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://santander.wd3.myworkdayjobs.com",
    "Referer": "https://santander.wd3.myworkdayjobs.com/SantanderCareers",
}


def _looks_eu(s: str) -> bool:
    sl = (s or "").lower()
    return any(h in sl for h in EU_HINTS)


class SantanderScraper(BankScraper):
    bank_name = "Santander"
    careers_url = "https://www.santandercareers.com/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            offset = 0
            while True:
                body = {
                    "appliedFacets": {"jobFamilyGroup": [EARLY_CAREERS_FACET]},
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": "",
                }
                try:
                    r = await client.post(API, json=body)
                    r.raise_for_status()
                    data = r.json()
                except Exception as e:
                    logger.warning("Santander offset=%d failed: %s", offset, e)
                    break
                postings = data.get("jobPostings") or []
                if not postings:
                    break
                for p in postings:
                    loc = p.get("locationsText") or ""
                    if not _looks_eu(loc):
                        continue
                    ext_path = p.get("externalPath") or ""
                    ext_id = ext_path.rsplit("_", 1)[-1] if "_" in ext_path else ext_path
                    if not ext_id or ext_id in offers:
                        continue
                    offers[ext_id] = ScrapedOffer(
                        bank="Santander",
                        external_id=ext_id,
                        role_title=p.get("title") or "",
                        location=loc,
                        apply_url=JOB_URL_TMPL.format(external_path=ext_path),
                        source_url=self.careers_url,
                        extras={"external_path": ext_path},
                    )
                offset += PAGE_SIZE
                if len(postings) < PAGE_SIZE:
                    break
                await asyncio.sleep(0.4)
            await self._enrich(client, list(offers.values()))
        logger.info("Santander: collected %d EU early-careers offers", len(offers))
        return list(offers.values())

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        sem = asyncio.Semaphore(4)

        async def one(o: ScrapedOffer):
            async with sem:
                try:
                    r = await client.get(DETAIL_URL_TMPL.format(external_path=o.extras["external_path"]))
                    if r.status_code != 200:
                        return
                    d = r.json()
                except Exception:
                    return
                await asyncio.sleep(0.15)
            clean = html_to_text((d.get("jobPostingInfo") or {}).get("jobDescription") or "")
            if clean:
                o.description = clean

        await asyncio.gather(*(one(o) for o in offers))
