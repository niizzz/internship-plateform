"""Goldman Sachs careers scraper (higher.gs.com GraphQL)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

GQL = "https://api-higher.gs.com/gateway/api/v1/graphql"
ROLE_URL_TMPL = "https://higher.gs.com/roles/{role_id}"

EUROPE_COUNTRIES = {
    "United Kingdom", "France", "Germany", "Spain", "Italy", "Netherlands",
    "Belgium", "Luxembourg", "Ireland", "Switzerland", "Portugal",
    "Sweden", "Denmark", "Norway", "Finland", "Poland", "Austria",
    "Czech Republic", "Czechia", "Hungary", "Greece", "Romania",
}

QUERY = """query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items {
      roleId
      corporateTitle
      jobTitle
      jobFunction
      locations { primary state country city }
      status
      division
      externalSource { sourceId }
    }
  }
}"""

# Goldman currently exposes only ~2 EARLY_CAREER roles; most S&T interns sit
# in PROFESSIONAL with "Summer Analyst" / "Intern" in the title. We search by
# keyword across both buckets.
KEYWORDS = [
    "", "summer analyst", "intern", "internship", "off-cycle",
    "industrial placement", "spring", "graduate",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Origin": "https://higher.gs.com",
    "Referer": "https://higher.gs.com/",
    "Accept": "application/json",
}

PAGE_SIZE = 50


class GoldmanScraper(BankScraper):
    bank_name = "Goldman Sachs"
    careers_url = "https://higher.gs.com/results"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            # One full EARLY_CAREER sweep (bucket is small), plus one keyworded
            # PROFESSIONAL sweep per keyword (S&T summer analysts live there).
            # Sweeps run concurrently (bounded) instead of serially.
            sweeps: list[tuple[str, list[str]]] = [("", ["EARLY_CAREER"])]
            sweeps += [(kw, ["PROFESSIONAL"]) for kw in KEYWORDS if kw]
            sem = asyncio.Semaphore(3)

            async def sweep(kw: str, exp: list[str]) -> None:
                async with sem:
                    page_n = 0
                    while True:
                        body = {
                            "operationName": "GetRoles",
                            "variables": {
                                "searchQueryInput": {
                                    "page": {"pageSize": PAGE_SIZE, "pageNumber": page_n},
                                    "sort": {"sortStrategy": "RELEVANCE", "sortOrder": "DESC"},
                                    "filters": [],
                                    "experiences": exp,
                                    "searchTerm": kw,
                                }
                            },
                            "query": QUERY,
                        }
                        try:
                            r = await client.post(GQL, json=body)
                            r.raise_for_status()
                            data = r.json()["data"]["roleSearch"]
                        except Exception as e:
                            logger.warning("GS kw=%r exp=%s page=%d failed: %s", kw, exp, page_n, e)
                            break
                        items = data.get("items") or []
                        for it in items:
                            self._collect(it, offers)
                        total = data.get("totalCount", 0)
                        page_n += 1
                        if page_n * PAGE_SIZE >= total or page_n >= 20:
                            break
                        await asyncio.sleep(0.2)

            await asyncio.gather(*(sweep(kw, exp) for kw, exp in sweeps))

            # Fetch full descriptions for likely early-careers roles — the
            # search payload has no description, and without one generic titles
            # can't be classified and the UI shows an empty posting.
            to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
            await self._enrich(client, to_enrich)

        logger.info("Goldman Sachs: collected %d Europe candidate offers (%d enriched)",
                    len(offers), len(to_enrich))
        return list(offers.values())

    DETAIL_QUERY = """query GetRole($id: String!) {
      role(externalSourceId: $id) { descriptionHtml shortDescription }
    }"""

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        sem = asyncio.Semaphore(4)

        async def one(o: ScrapedOffer):
            async with sem:
                try:
                    r = await client.post(GQL, json={
                        "query": self.DETAIL_QUERY,
                        "variables": {"id": o.external_id},
                    })
                    if r.status_code != 200:
                        return
                    role = (r.json().get("data") or {}).get("role") or {}
                except Exception:
                    return
                await asyncio.sleep(0.1)
            desc = html_to_text(role.get("descriptionHtml") or "") or \
                html_to_text(role.get("shortDescription") or "")
            if desc:
                o.description = desc

        await asyncio.gather(*(one(o) for o in offers))

    def _collect(self, it: dict[str, Any], out: dict[str, ScrapedOffer]) -> None:
        role_id = str(it.get("roleId") or it.get("externalSource", {}).get("sourceId") or "")
        if not role_id or role_id in out:
            return
        locs = it.get("locations") or []
        # Drop unless at least one location is in Europe.
        eu_loc = next((l for l in locs if (l.get("country") or "") in EUROPE_COUNTRIES), None)
        if not eu_loc:
            return
        title = it.get("jobTitle") or ""
        loc_str = ", ".join(filter(None, [eu_loc.get("city"), eu_loc.get("state"), eu_loc.get("country")]))
        ext_id = str(it.get("externalSource", {}).get("sourceId") or role_id)
        out[role_id] = ScrapedOffer(
            bank="Goldman Sachs",
            external_id=ext_id,
            role_title=title,
            location=loc_str,
            apply_url=ROLE_URL_TMPL.format(role_id=ext_id),
            source_url=self.careers_url,
            extras={
                "division": it.get("division"),
                "job_function": it.get("jobFunction"),
                "corporate_title": it.get("corporateTitle"),
            },
        )
