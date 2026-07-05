"""Citi careers scraper (Workday CXS)."""
from __future__ import annotations

import asyncio
import logging

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

API = "https://citi.wd5.myworkdayjobs.com/wday/cxs/citi/2/jobs"
JOB_URL_TMPL = "https://citi.wd5.myworkdayjobs.com/2{external_path}"
DETAIL_URL_TMPL = "https://citi.wd5.myworkdayjobs.com/wday/cxs/citi/2{external_path}"

# Country_and_Jurisdiction facet IDs for European countries Citi posts in.
EUROPE_COUNTRY_IDS = [
    "29247e57dbaf46fb855b224e03170bc7",  # United Kingdom
    "131d5ac7e3ee4d7b962bdc96e498e412",  # Poland
    "9db257f5937e4421b2fac64eec6832f8",  # Hungary
    "04a05835925f45b3a59406a2a6b72c8a",  # Ireland
    "187134fccb084a0ea9b4b95f23890dbe",  # Switzerland
    "dcc5b7608d8644b3a93716604e78e995",  # Germany
    "54c5b6971ffb4bf0b116fe7651ec789a",  # France
    "9696868b09c64d52a62ee13b052383cc",  # Netherlands
    "328b82f597514643a7683a78fc67c3f1",  # Luxembourg
    "fc078443155c4ad294201ecf5a61a499",  # Czechia
    "8cd04a563fd94da7b06857a79faaf815",  # Italy
    "566388c1eb974c42bd9e3da4c2f57d60",  # Greece
    "6a800a4736884df5826858d435650f45",  # Sweden
]

PAGE_SIZE = 20  # smaller pages — Workday rate-limits aggressively on bursts
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://citi.wd5.myworkdayjobs.com",
    "Referer": "https://citi.wd5.myworkdayjobs.com/2",
}


class CitiScraper(BankScraper):
    bank_name = "Citi"
    careers_url = "https://jobs.citi.com/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: list[ScrapedOffer] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            offset = 0
            while True:
                body = {
                    "appliedFacets": {"Country_and_Jurisdiction": EUROPE_COUNTRY_IDS},
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": "",
                }
                try:
                    r = await client.post(API, json=body)
                    r.raise_for_status()
                    data = r.json()
                except Exception as e:
                    logger.warning("Citi page offset=%d failed: %s — backoff+retry", offset, e)
                    succeeded = False
                    for backoff in (2.0, 5.0, 10.0):
                        await asyncio.sleep(backoff)
                        try:
                            r = await client.post(API, json=body)
                            r.raise_for_status()
                            data = r.json()
                            succeeded = True
                            break
                        except Exception as e2:
                            logger.warning("  retry after %ss failed: %s", backoff, e2)
                    if not succeeded:
                        logger.error("Citi giving up at offset=%d", offset)
                        break
                postings = data.get("jobPostings", [])
                if not postings:
                    break
                for p in postings:
                    ext_path = p.get("externalPath") or ""
                    ext_id = ext_path.rsplit("_", 1)[-1] if "_" in ext_path else ext_path
                    if not ext_id or ext_id in seen:
                        continue
                    seen.add(ext_id)
                    offers.append(ScrapedOffer(
                        bank="Citi",
                        external_id=ext_id,
                        role_title=p.get("title") or "",
                        location=p.get("locationsText") or "",
                        apply_url=JOB_URL_TMPL.format(external_path=ext_path),
                        source_url=self.careers_url,
                        extras={"posted_on": p.get("postedOn"), "external_path": ext_path},
                    ))
                # Workday returns total=0 on subsequent paginated requests with the
                # same session — don't trust it. Stop when a page returns < PAGE_SIZE.
                offset += PAGE_SIZE
                if len(postings) < PAGE_SIZE:
                    break
                await asyncio.sleep(0.5)
        # Only fetch detail pages for likely early-careers roles — enriching
        # every EU posting was hundreds of useless requests per refresh.
        to_enrich = [o for o in offers if _PROGRAM_RE.search(o.role_title.lower())]
        logger.info("Citi: collected %d Europe offers (%d to enrich)", len(offers), len(to_enrich))
        await self._enrich_descriptions(to_enrich)
        return offers

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
                # NB: Workday's jobPostingInfo.startDate is the POSTING date,
                # not the job start — the orchestrator derives the real start
                # date from title/description instead.
                clean = html_to_text(jp.get("jobDescription") or "")
                if clean:
                    o.description = clean

            await asyncio.gather(*(one(o) for o in offers))
