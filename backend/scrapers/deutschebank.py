"""Deutsche Bank careers scraper (BeeSite).

DB runs on BeeSite (Milch & Zucker) with a clean JSON API:
    GET https://api-deutschebank.beesite.de/search/?data=<url-encoded JSON>
returning paginated SearchResultItems. Descriptions aren't in the search
payload, so we enrich the early-careers survivors via:
    GET https://api-deutschebank.beesite.de/jobhtml/<id>.json   -> {"html": ...}

DB runs TWO SEPARATE BeeSite indexes and they barely overlap:
  /search/          -> the main/professional board (~1900 reqs, experienced hires)
  /graduatesearch/  -> the STUDENT & GRADUATE board (~70 reqs), which is where every
                       Internship Programme / Graduate Programme / Praktikum lives.
Scraping only /search/ (as this file did until 2026-09-01) meant the platform never
saw a single DB early-careers S&T role — e.g. the London "Quantitative FIC
Internship Programme 2027" and "QRD Lab Sales and Trading Internship Programme"
are graduate-board-only. Always sweep BOTH.

NB `LanguageCode` MUST be uppercase "EN" on /graduatesearch/: lowercase "en" (what
DB's own SPA sends) returns GERMAN country names ("Grossbritannien und Nordirland",
"Deutschland"), which silently fails the EUROPE_COUNTRIES check and drops every
European role. Uppercase "EN" returns "United Kingdom"/"Germany".

NB (measured 2026-07-24): `LanguageCode` in the payload only changes UI labels,
NOT which requisitions come back — an EN sweep and a DE sweep return the exact
same PositionID set (1802 vs 1802, 0 DE-only). So there is no separate German
posting pool to chase; the single EN sweep is complete. Don't re-add a DE sweep.
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
# Student/graduate board — a DIFFERENT index, not a filter on SEARCH_API.
GRADUATE_API = "https://api-deutschebank.beesite.de/graduatesearch/"
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
            # Both indexes, deduped on PositionID (a few reqs appear on both).
            for label, endpoint in (("main", SEARCH_API), ("graduate", GRADUATE_API)):
                await self._sweep(client, label, endpoint, offers)

            # Enrich likely internships (program word in title) with description.
            to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
            await self._enrich(client, to_enrich)

        logger.info("Deutsche Bank: %d EU offers (%d enriched)", len(offers), len(to_enrich))
        return list(offers.values())

    async def _sweep(self, client: httpx.AsyncClient, label: str, endpoint: str,
                     offers: dict[str, ScrapedOffer]) -> None:
        """Page ONE BeeSite index into `offers` (keyed by PositionID, first wins).

        A page-1 failure on EITHER board raises: losing a whole board silently is
        exactly the bug that hid DB's early-careers S&T roles for months. Failures
        part-way through pagination keep the partial results.
        """
        before = len(offers)
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
            url = endpoint + "?data=" + urllib.parse.quote(json.dumps(payload))
            try:
                r = await client.get(url)
                r.raise_for_status()
                sr = r.json().get("SearchResult", {})
            except Exception as e:
                if first == 1:
                    raise RuntimeError(
                        f"Deutsche Bank: {label} board page 1 failed: {e}") from e
                logger.warning("DB %s board FirstItem=%d failed (partial results kept): %s",
                               label, first, e)
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
        logger.info("Deutsche Bank %s board: +%d EU offers", label, len(offers) - before)

    def _collect(self, it: dict, out: dict[str, ScrapedOffer]) -> None:
        de = it.get("MatchedObjectDescriptor", {})
        locs = de.get("PositionLocation", []) or []
        eu = next((l for l in locs if (l.get("CountryName") or "") in EUROPE_COUNTRIES), None)
        if not eu:
            return
        pid = str(de.get("PositionID") or "")
        if not pid or pid in out:
            return
        uri = (de.get("PositionURI") or "").strip()
        location = ", ".join(filter(None, [eu.get("CityName"), eu.get("CountryName")]))
        out[pid] = ScrapedOffer(
            bank="Deutsche Bank",
            external_id=pid,
            role_title=de.get("PositionTitle") or "",
            location=location,
            # Graduate board gives an ABSOLUTE apply URL (db.recsolu.com); the main
            # board only a relative "/index.php?ac=jobad&id=N" -> use APPLY_TMPL there.
            apply_url=uri if uri.startswith("http") else APPLY_TMPL.format(id=pid),
            source_url=self.careers_url,
            posted_raw=de.get("PublicationStartDate"),
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
