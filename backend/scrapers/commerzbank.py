"""Commerzbank careers scraper (BeeSite, same vendor as Deutsche Bank).

Search API (found in jobs.commerzbank.com's gjb_scripts.js):
    GET https://api-jobs.commerzbank.com/search/?data=<url-encoded JSON>
returning paginated SearchResultItems. The jobhtml endpoint is 403 here, so
descriptions come from the job-ad page's JobPosting JSON-LD instead:
    GET https://jobs.commerzbank.com/index.php?ac=jobad&id=<n>&language=2
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse

import httpx

import html as _htmllib

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

SEARCH_API = "https://api-jobs.commerzbank.com/search/"

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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

_JSONLD_RE = re.compile(r'<script type="application/ld\+json">\s*(.*?)</script>', re.DOTALL)


class CommerzbankScraper(BankScraper):
    bank_name = "Commerzbank"
    careers_url = "https://jobs.commerzbank.com/"

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
                    logger.warning("Commerzbank search FirstItem=%d failed: %s", first, e)
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

        logger.info("Commerzbank: %d EU offers (%d enriched)", len(offers), len(to_enrich))
        return list(offers.values())

    def _collect(self, it: dict, out: dict[str, ScrapedOffer]) -> None:
        de = it.get("MatchedObjectDescriptor", {})
        locs = de.get("PositionLocation", []) or []
        eu = next((l for l in locs if (l.get("CountryName") or "") in EUROPE_COUNTRIES), None)
        if not eu:
            return
        pid = str(de.get("PositionID") or "")
        uri = de.get("PositionURI") or ""
        if not pid or pid in out:
            return
        # PositionURI already points at the portal job ad; force English UI.
        apply_url = uri + ("&language=2" if "language=" not in uri else "") if uri else self.careers_url
        location = ", ".join(filter(None, [eu.get("CityName"), eu.get("CountryName")]))
        out[pid] = ScrapedOffer(
            bank="Commerzbank",
            external_id=pid,
            role_title=de.get("PositionTitle") or "",
            location=location,
            apply_url=apply_url,
            source_url=self.careers_url,
            extras={
                "career_level": "; ".join(c.get("Name", "") for c in de.get("CareerLevel", []) or []),
                "offering_type": "; ".join(o.get("Name", "") for o in de.get("PositionOfferingType", []) or []),
            },
        )

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        sem = asyncio.Semaphore(4)

        async def one(o: ScrapedOffer):
            async with sem:
                try:
                    r = await client.get(o.apply_url, follow_redirects=True)
                    if r.status_code != 200:
                        return
                    m = _JSONLD_RE.search(r.text)
                    if not m:
                        return
                    data = json.loads(m.group(1))
                    if isinstance(data, list):
                        data = next((d for d in data if d.get("@type") == "JobPosting"), data[0] if data else {})
                except Exception:
                    return
            # JSON-LD descriptions here are double-escaped (&lt;p&gt; inside the
            # JSON string) — unescape first so html_to_text sees real tags.
            clean = html_to_text(_htmllib.unescape(data.get("description") or ""))
            if clean:
                o.description = clean
            if data.get("datePosted"):
                o.posted_raw = data.get("datePosted")
            # JSON-LD title is often more complete ("... (m/f/diverse)").
            title = data.get("title")
            if title and len(title) > len(o.role_title):
                o.role_title = title

        await asyncio.gather(*(one(o) for o in offers))
