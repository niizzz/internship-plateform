"""Morgan Stanley careers scraper (Workday CXS)."""
from __future__ import annotations

import asyncio
import logging

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

API = "https://ms.wd5.myworkdayjobs.com/wday/cxs/ms/External/jobs"
JOB_URL_TMPL = "https://ms.wd5.myworkdayjobs.com/External{external_path}"
DETAIL_URL_TMPL = "https://ms.wd5.myworkdayjobs.com/wday/cxs/ms/External{external_path}"

EUROPE_COUNTRY_IDS = [
    "29247e57dbaf46fb855b224e03170bc7",  # United Kingdom
    "9db257f5937e4421b2fac64eec6832f8",  # Hungary
    "dcc5b7608d8644b3a93716604e78e995",  # Germany
    "54c5b6971ffb4bf0b116fe7651ec789a",  # France
    "131d5ac7e3ee4d7b962bdc96e498e412",  # Poland
    "04a05835925f45b3a59406a2a6b72c8a",  # Ireland
]

PAGE_SIZE = 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://ms.wd5.myworkdayjobs.com",
    "Referer": "https://ms.wd5.myworkdayjobs.com/External",
}


class MorganStanleyScraper(BankScraper):
    bank_name = "Morgan Stanley"
    careers_url = "https://www.morganstanley.com/people-opportunities/students-graduates"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            offset = 0
            while True:
                body = {
                    "appliedFacets": {"Location_Country": EUROPE_COUNTRY_IDS},
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": "",
                }
                try:
                    r = await client.post(API, json=body)
                    r.raise_for_status()
                    data = r.json()
                except Exception as e:
                    logger.warning("MS offset=%d failed: %s — backoff", offset, e)
                    await asyncio.sleep(3.0)
                    try:
                        r = await client.post(API, json=body)
                        r.raise_for_status()
                        data = r.json()
                    except Exception as e2:
                        logger.error("MS giving up: %s", e2)
                        break
                postings = data.get("jobPostings") or []
                if not postings:
                    break
                for p in postings:
                    ext_path = p.get("externalPath") or ""
                    ext_id = ext_path.rsplit("_", 1)[-1] if "_" in ext_path else ext_path
                    if not ext_id or ext_id in offers:
                        continue
                    offers[ext_id] = ScrapedOffer(
                        bank="Morgan Stanley",
                        external_id=ext_id,
                        role_title=p.get("title") or "",
                        location=p.get("locationsText") or "",
                        apply_url=JOB_URL_TMPL.format(external_path=ext_path),
                        source_url=self.careers_url,
                        extras={"external_path": ext_path, "posted_on": p.get("postedOn")},
                    )
                offset += PAGE_SIZE
                if len(postings) < PAGE_SIZE:
                    break
                await asyncio.sleep(0.5)
        # Enrich only likely early-careers roles, not every EU posting.
        to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
        logger.info("Morgan Stanley: %d EU offers (%d to enrich)", len(offers), len(to_enrich))
        await self._enrich_descriptions(to_enrich)
        return list(offers.values())

    async def _enrich_descriptions(self, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        async with httpx.AsyncClient(timeout=20, headers=HEADERS) as client:
            sem = asyncio.Semaphore(3)

            async def one(o: ScrapedOffer):
                path = o.extras.get("external_path")
                if not path:
                    return
                async with sem:
                    try:
                        r = await client.get(DETAIL_URL_TMPL.format(external_path=path))
                        if r.status_code != 200:
                            return
                        d = r.json()
                    except Exception:
                        return
                    await asyncio.sleep(0.2)
                jp = d.get("jobPostingInfo", {})
                # Workday startDate is the posting date, not the job start — skip it.
                clean = html_to_text(jp.get("jobDescription") or "")
                if clean:
                    o.description = clean

            await asyncio.gather(*(one(o) for o in offers))
