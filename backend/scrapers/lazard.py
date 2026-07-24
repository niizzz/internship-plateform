"""Lazard careers scraper (Oracle HCM Fusion).

Lazard runs Oracle HCM at icbpjb.fa.ocs.oraclecloud.com (site
`LazardProfessionalCareers`) — same shape as JPMorgan. Note Lazard is a pure
advisory house (M&A / restructuring / asset management) with no sales & trading
desks, so the S&T filter will legitimately drop ~everything; the scraper exists
for completeness and to catch any markets-adjacent role that ever appears.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

# Lazard's Oracle site hosts BOTH Lazard (IB / advisory) and Lazard Frères
# Gestion (the French asset-management arm) postings. Only LFG job descriptions
# name "Lazard Frères Gestion" — IB/M&A ones never do — so this marker cleanly
# routes each requisition to the right entity (see LazardFreresGestionScraper).
_FRERES_GESTION_RE = re.compile(r"fr[eè]res\s+gestion", re.I)

API = "https://icbpjb.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
DETAILS_API = "https://icbpjb.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
# The details finder wants the internal site number, not the site alias.
SITE_NUMBER = "CX_45001"
SITE = "LazardProfessionalCareers"
JOB_URL_TMPL = "https://icbpjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/LazardProfessionalCareers/job/{job_id}"

EUROPE_ISO = {
    "GB", "FR", "DE", "ES", "IT", "NL", "BE", "LU", "IE",
    "CH", "PT", "SE", "DK", "NO", "FI", "PL", "AT", "CZ", "HU", "GR", "RO",
}

QUERIES = [
    "Intern", "Internship", "Summer Analyst", "Off-Cycle", "Placement",
    "Graduate", "Stage", "Stagiaire", "Alternance", "Trainee",
]
PAGE_SIZE = 50
MAX_PAGES = 6

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36", "Accept": "application/json"}


class LazardScraper(BankScraper):
    bank_name = "Lazard"
    careers_url = "https://www.lazard.com/careers/"
    # When True, keep only Lazard Frères Gestion postings; when False, keep only
    # the non-LFG (IB / advisory) ones. Both entities share this Oracle site.
    freres_gestion_only = False

    def _keep(self, o: ScrapedOffer) -> bool:
        is_lfg = bool(o.description and _FRERES_GESTION_RE.search(o.description))
        return is_lfg if self.freres_gestion_only else not is_lfg

    async def scrape(self) -> list[ScrapedOffer]:
        seen: set[str] = set()
        offers: list[ScrapedOffer] = []
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            sem = asyncio.Semaphore(4)

            async def search(kw: str) -> list[dict[str, Any]]:
                async with sem:
                    try:
                        return await self._search(client, kw)
                    except Exception as e:
                        logger.warning("Lazard query %r failed: %s", kw, e)
                        return []

            results = await asyncio.gather(*(search(kw) for kw in QUERIES))
            for items in results:
                for item in items:
                    jid = str(item.get("Id") or "")
                    if not jid or jid in seen:
                        continue
                    country = item.get("PrimaryLocationCountry") or ""
                    if country not in EUROPE_ISO:
                        secs = item.get("secondaryLocations") or []
                        if not any((s.get("CountryCode") or s.get("PrimaryLocationCountry")) in EUROPE_ISO for s in secs):
                            continue
                    seen.add(jid)
                    offers.append(ScrapedOffer(
                        bank=self.bank_name,
                        external_id=jid,
                        role_title=item.get("Title") or "",
                        location=item.get("PrimaryLocation") or "",
                        apply_url=JOB_URL_TMPL.format(job_id=jid),
                        source_url=self.careers_url,
                        description=html_to_text(item.get("ExternalDescriptionStr") or "") or None,
                        posted_raw=item.get("PostedDate"),  # Oracle HCM publication date
                    ))

            # Search results rarely carry ExternalDescriptionStr — fetch the
            # details endpoint for likely internships that are missing one.
            to_enrich = [o for o in offers if not o.description and _PROGRAM_RE.search(o.role_title.lower())]
            await asyncio.gather(*(self._enrich_one(client, o) for o in to_enrich))
        # Route each requisition to the right entity (Lazard IB vs LFG).
        kept = [o for o in offers if self._keep(o)]
        logger.info("%s: collected %d of %d Europe early-careers candidates (%d enriched)",
                    self.bank_name, len(kept), len(offers), len(to_enrich))
        return kept

    async def _enrich_one(self, client: httpx.AsyncClient, o: ScrapedOffer) -> None:
        params = {
            "expand": "all", "onlyData": "true",
            "finder": f'ById;Id="{o.external_id}",siteNumber={SITE_NUMBER}',
        }
        try:
            r = await client.get(DETAILS_API, params=params)
            if r.status_code != 200:
                return
            items = r.json().get("items", [])
            desc = items[0].get("ExternalDescriptionStr") if items else None
        except Exception:
            return
        if desc:
            o.description = html_to_text(desc) or None

    async def _search(self, client: httpx.AsyncClient, keyword: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            params = {
                "onlyData": "true",
                "expand": "requisitionList.secondaryLocations",
                "finder": (
                    f"findReqs;siteNumber={SITE},"
                    f"limit={PAGE_SIZE},offset={offset},"
                    f"sortBy=POSTING_DATES_DESC,keyword={keyword}"
                ),
            }
            r = await client.get(API, params=params)
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                break
            block = items[0]
            reqs = block.get("requisitionList") or []
            if not reqs:
                break
            out.extend(reqs)
            offset += PAGE_SIZE
            if offset >= block.get("TotalJobsCount", 0):
                break
            await asyncio.sleep(0.15)
        return out
