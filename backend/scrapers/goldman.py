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
      startDate
      externalSource { sourceId }
    }
  }
}"""

# Goldman's student/intern roles live in the CAMPUS experience bucket (~150,
# incl. the "FICC & Equities (Sales & Trading) | Seasonal/Off Cycle Internship"
# postings) — NOT EARLY_CAREER (~6) and NOT PROFESSIONAL. We sweep EARLY_CAREER
# and CAMPUS in full (small buckets) and keyword-narrow the large PROFESSIONAL
# bucket (some S&T summer analysts also sit there).
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
            sweeps: list[tuple[str, list[str]]] = [("", ["EARLY_CAREER"]), ("", ["CAMPUS"])]
            sweeps += [(kw, ["PROFESSIONAL"]) for kw in KEYWORDS if kw]
            sem = asyncio.Semaphore(3)
            ok_requests = 0

            async def sweep(kw: str, exp: list[str]) -> None:
                nonlocal ok_requests
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
                        ok_requests += 1
                        items = data.get("items") or []
                        for it in items:
                            self._collect(it, offers)
                        total = data.get("totalCount", 0)
                        page_n += 1
                        if page_n * PAGE_SIZE >= total or page_n >= 20:
                            break
                        await asyncio.sleep(0.2)

            await asyncio.gather(*(sweep(kw, exp) for kw, exp in sweeps))
            if ok_requests == 0:
                raise RuntimeError("Goldman Sachs: every GraphQL sweep failed")

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
        # Dedupe on ext_id (what becomes Offer.external_id): keying on roleId
        # while persisting sourceId could emit two ScrapedOffers with the same
        # external_id when a posting appears in two sweeps under different
        # roleIds — a within-batch duplicate.
        role_id = str(it.get("roleId") or it.get("externalSource", {}).get("sourceId") or "")
        ext_id = str(it.get("externalSource", {}).get("sourceId") or role_id)
        if not ext_id or ext_id in out:
            return
        locs = it.get("locations") or []
        # Drop unless at least one location is in Europe.
        eu_loc = next((l for l in locs if (l.get("country") or "") in EUROPE_COUNTRIES), None)
        if not eu_loc:
            return
        title = it.get("jobTitle") or ""
        loc_str = ", ".join(filter(None, [eu_loc.get("city"), eu_loc.get("state"), eu_loc.get("country")]))
        out[ext_id] = ScrapedOffer(
            bank="Goldman Sachs",
            external_id=ext_id,
            role_title=title,
            location=loc_str,
            apply_url=ROLE_URL_TMPL.format(role_id=ext_id),
            source_url=self.careers_url,
            # GS `startDate` = when the posting went live on higher.gs.com.
            posted_raw=it.get("startDate"),
            extras={
                "division": it.get("division"),
                "job_function": it.get("jobFunction"),
                "corporate_title": it.get("corporateTitle"),
            },
        )
