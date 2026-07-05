"""Rothschild & Co careers scraper (Workday CXS).

Workday tenant `rothschildandco`, site `RothschildAndCo_Lateral` (the only site;
campus roles are folded into it as Stage/Alternance). ~130 roles total, so we
pull all and filter to Europe + S&T downstream. Rothschild is largely advisory
(M&A / wealth & asset management) with a small equities arm (Redburn), so S&T
hits will be few — the scraper exists for coverage and future roles.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

API = "https://rothschildandco.wd3.myworkdayjobs.com/wday/cxs/rothschildandco/RothschildAndCo_Lateral/jobs"
JOB_URL_TMPL = "https://rothschildandco.wd3.myworkdayjobs.com/RothschildAndCo_Lateral{external_path}"
DETAIL_URL_TMPL = "https://rothschildandco.wd3.myworkdayjobs.com/wday/cxs/rothschildandco/RothschildAndCo_Lateral{external_path}"

EU_HINTS = [
    "london", "edinburgh", "manchester", "united kingdom", "paris", "marseille",
    "lyon", "france", "frankfurt", "germany", "madrid", "spain", "milan", "milano",
    "italy", "amsterdam", "netherlands", "brussels", "belgium", "luxembourg",
    "geneva", "zurich", "zürich", "switzerland", "lisbon", "portugal", "dublin",
    "ireland", "stockholm", "sweden", "warsaw", "poland", "vienna", "austria",
]

PAGE_SIZE = 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://rothschildandco.wd3.myworkdayjobs.com",
    "Referer": "https://rothschildandco.wd3.myworkdayjobs.com/RothschildAndCo_Lateral",
}


def _looks_eu(s: str) -> bool:
    sl = (s or "").lower()
    return any(h in sl for h in EU_HINTS)


class RothschildScraper(BankScraper):
    bank_name = "Rothschild & Co"
    careers_url = "https://www.rothschildandco.com/en/careers/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            offset = 0
            while True:
                body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
                try:
                    r = await client.post(API, json=body)
                    r.raise_for_status()
                    data = r.json()
                except Exception as e:
                    logger.warning("Rothschild offset=%d failed: %s", offset, e)
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
                        bank="Rothschild & Co",
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
            # Enrich only likely early-careers roles (Stage / Alternance / intern).
            to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
            await self._enrich(client, to_enrich)
        logger.info("Rothschild & Co: collected %d EU offers (%d enriched)", len(offers), len(to_enrich))
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
