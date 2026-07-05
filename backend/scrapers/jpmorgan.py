from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .base import BankScraper, ScrapedOffer, html_to_text

logger = logging.getLogger(__name__)

API = "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
JOB_URL_TMPL = "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/{job_id}"

EUROPE_ISO = {
    "GB", "FR", "DE", "ES", "IT", "NL", "BE", "LU", "IE",
    "CH", "PT", "SE", "DK", "NO", "FI", "PL", "AT", "CZ", "HU", "GR", "RO",
}

# Early-careers keyword set. JPM has no early-careers facet, so we narrow at
# the keyword level — then S&T filter runs in the orchestrator.
QUERIES = [
    "Summer Analyst", "Summer Internship", "Internship", "Intern",
    "Off-Cycle", "Industrial Placement", "Placement",
    "Graduate Programme", "Graduate Program", "Spring",
    "Apprenticeship", "Stagiaire", "Stage",
]

PAGE_SIZE = 50
MAX_PAGES_PER_KEYWORD = 10  # 500 results per kw max


class JPMorganScraper(BankScraper):
    bank_name = "JPMorgan"
    careers_url = "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions"

    async def scrape(self) -> list[ScrapedOffer]:
        seen_ids: set[str] = set()
        offers: list[ScrapedOffer] = []
        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        ) as client:
            # Run keyword searches concurrently (bounded) — serial keyword
            # sweeps were the bulk of this scraper's runtime.
            sem = asyncio.Semaphore(4)

            async def search(kw: str) -> list[dict[str, Any]]:
                async with sem:
                    try:
                        return await self._search_all(client, kw)
                    except Exception as e:
                        logger.warning("JPM query %r failed: %s", kw, e)
                        return []

            results = await asyncio.gather(*(search(kw) for kw in QUERIES))
            for items in results:
                for item in items:
                    job_id = str(item.get("Id") or "")
                    if not job_id or job_id in seen_ids:
                        continue
                    country = item.get("PrimaryLocationCountry") or ""
                    if country not in EUROPE_ISO:
                        secondaries = item.get("secondaryLocations") or []
                        has_eu = any(
                            (s.get("CountryCode") or s.get("PrimaryLocationCountry")) in EUROPE_ISO
                            for s in secondaries
                        )
                        if not has_eu:
                            continue
                    seen_ids.add(job_id)
                    offers.append(ScrapedOffer(
                        bank="JPMorgan",
                        external_id=job_id,
                        role_title=item.get("Title") or "",
                        location=item.get("PrimaryLocation") or "",
                        apply_url=JOB_URL_TMPL.format(job_id=job_id),
                        source_url=self.careers_url,
                        description=html_to_text(item.get("ExternalDescriptionStr") or "") or None,
                        extras={
                            "posted_date": item.get("PostedDate"),
                            "job_family": item.get("JobFamily"),
                            "job_function": item.get("JobFunction"),
                            "country_code": country,
                        },
                    ))
        logger.info("JPMorgan: %d Europe early-careers candidates across %d keywords",
                    len(offers), len(QUERIES))
        return offers

    async def _search_all(self, client: httpx.AsyncClient, keyword: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES_PER_KEYWORD):
            params = {
                "onlyData": "true",
                "expand": "requisitionList.secondaryLocations",
                "finder": (
                    "findReqs;"
                    "siteNumber=CX_1001,"
                    "facetsList=LOCATIONS;CATEGORIES;TITLES,"
                    f"limit={PAGE_SIZE},offset={offset},"
                    "sortBy=POSTING_DATES_DESC,"
                    f"keyword={keyword}"
                ),
            }
            r = await client.get(API, params=params)
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
            if not items:
                break
            block = items[0]
            req_list = block.get("requisitionList") or []
            if not req_list:
                break
            out.extend(req_list)
            total = block.get("TotalJobsCount", 0)
            offset += PAGE_SIZE
            if offset >= total:
                break
            await asyncio.sleep(0.15)
        return out
