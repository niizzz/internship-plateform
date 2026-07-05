"""Deutsche Bank careers scraper (BeeSite).

DB runs on BeeSite (Milch & Zucker) with a clean JSON API:
    GET https://api-deutschebank.beesite.de/search/?data=<url-encoded JSON>
returning paginated SearchResultItems. Descriptions aren't in the search
payload, so we enrich the early-careers survivors via:
    GET https://api-deutschebank.beesite.de/jobhtml/<id>.json   -> {"html": ...}
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

SEARCH_API = "https://api-deutschebank.beesite.de/search/"
JOBHTML_TMPL = "https://api-deutschebank.beesite.de/jobhtml/{id}.json"
APPLY_TMPL = "https://careers.db.com/index.php?ac=jobad&id={id}"

EUROPE_COUNTRIES = {
    "United Kingdom", "France", "Germany", "Spain", "Italy", "Netherlands",
    "Belgium", "Luxembourg", "Ireland", "Switzerland", "Portugal", "Sweden",
    "Denmark", "Norway", "Finland", "Poland", "Austria", "Czech Republic",
    "Czechia", "Hungary", "Greece", "Romania",
}

DESCRIPTOR_FIELDS = [
    "PositionTitle", "PositionURI", "PositionID",
    "PositionLocation.CountryName", "PositionLocation.CityName",
    "CareerLevel.Name", "PositionOfferingType.Name", "PublicationStartDate",
]

PAGE_SIZE = 100
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
class DeutscheBankScraper(BankScraper):
    bank_name = "Deutsche Bank"
    careers_url = "https://careers.db.com/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            first = 1
            while True:
                payload = {
                    "LanguageCode": "EN",
                    "SearchParameters": {
                        "FirstItem": first,
                        "CountItem": PAGE_SIZE,
                        "MatchedObjectDescriptor": DESCRIPTOR_FIELDS,
                        "Sort": [{"Criterion": "PublicationStartDate", "Direction": "DESC"}],
                    },
                }
                url = SEARCH_API + "?data=" + urllib.parse.quote(json.dumps(payload))
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    sr = r.json().get("SearchResult", {})
                except Exception as e:
                    logger.warning("DB search FirstItem=%d failed: %s", first, e)
                    break
                items = sr.get("SearchResultItems", []) or []
                if not items:
                    break
                for it in items:
                    self._collect(it, offers)
                total = sr.get("SearchResultCountAll", 0)
                first += PAGE_SIZE
                if first > total or first > 3000:
                    break
                await asyncio.sleep(0.15)

            # Enrich likely internships (program word in title) with description.
            to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
            await self._enrich(client, to_enrich)

        logger.info("Deutsche Bank: %d EU offers (%d enriched)", len(offers), len(to_enrich))
        return list(offers.values())

    def _collect(self, it: dict, out: dict[str, ScrapedOffer]) -> None:
        de = it.get("MatchedObjectDescriptor", {})
        locs = de.get("PositionLocation", []) or []
        eu = next((l for l in locs if (l.get("CountryName") or "") in EUROPE_COUNTRIES), None)
        if not eu:
            return
        pid = str(de.get("PositionID") or "")
        if not pid or pid in out:
            return
        location = ", ".join(filter(None, [eu.get("CityName"), eu.get("CountryName")]))
        out[pid] = ScrapedOffer(
            bank="Deutsche Bank",
            external_id=pid,
            role_title=de.get("PositionTitle") or "",
            location=location,
            apply_url=APPLY_TMPL.format(id=pid),
            source_url=self.careers_url,
            extras={
                "career_level": "; ".join(c.get("Name", "") for c in de.get("CareerLevel", []) or []),
                "offering_type": "; ".join(o.get("Name", "") for o in de.get("PositionOfferingType", []) or []),
            },
        )

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        sem = asyncio.Semaphore(5)

        async def one(o: ScrapedOffer):
            async with sem:
                try:
                    r = await client.get(JOBHTML_TMPL.format(id=o.external_id))
                    if r.status_code != 200:
                        return
                    html = r.json().get("html", "")
                except Exception:
                    return
            clean = html_to_text(html)
            if clean:
                o.description = clean

        await asyncio.gather(*(one(o) for o in offers))
