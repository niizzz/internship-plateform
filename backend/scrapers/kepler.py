"""Kepler Cheuvreux careers scraper (Teamtailor).

Their Teamtailor career site exposes a public JSON Feed:
    GET https://keplercheuvreux.teamtailor.com/jobs.json
with title, url and full content_html per job. Titles are of the form
"INTERNSHIP - STRUCTURED PRODUCT FINANCIAL ENGINEER ASSISTANT - NYON";
the trailing segment is the city (sometimes in French: LONDRES).
"""
from __future__ import annotations

import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, html_to_text

logger = logging.getLogger(__name__)

FEED_URL = "https://keplercheuvreux.teamtailor.com/jobs.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# French / local spellings → canonical English city names.
_CITY_CANON = {
    "londres": "London", "london": "London",
    "paris": "Paris", "nyon": "Nyon", "geneve": "Geneva", "genève": "Geneva",
    "geneva": "Geneva", "zurich": "Zurich", "francfort": "Frankfurt",
    "frankfurt": "Frankfurt", "madrid": "Madrid", "milan": "Milan",
    "amsterdam": "Amsterdam", "bruxelles": "Brussels", "brussels": "Brussels",
    "stockholm": "Stockholm", "oslo": "Oslo", "vienne": "Vienna", "vienna": "Vienna",
}
_CITY_COUNTRY = {
    "London": "United Kingdom", "Paris": "France", "Nyon": "Switzerland",
    "Geneva": "Switzerland", "Zurich": "Switzerland", "Frankfurt": "Germany",
    "Madrid": "Spain", "Milan": "Italy", "Amsterdam": "Netherlands",
    "Brussels": "Belgium", "Stockholm": "Sweden", "Oslo": "Norway",
    "Vienna": "Austria",
}

_LOCATION_LINE_RE = re.compile(r"location\s*:\s*([A-Za-zÀ-ÿ' -]+)", re.I)
_JOB_ID_RE = re.compile(r"/jobs/(\d+)")


class KeplerScraper(BankScraper):
    bank_name = "Kepler Cheuvreux"
    careers_url = "https://keplercheuvreux.teamtailor.com/jobs"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: list[ScrapedOffer] = []
        url = FEED_URL
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            for _page in range(10):  # JSON Feed pagination guard
                r = await client.get(url, follow_redirects=True)
                r.raise_for_status()
                feed = r.json()
                for item in feed.get("items", []) or []:
                    o = self._collect(item)
                    if o:
                        offers.append(o)
                url = feed.get("next_url")
                if not url:
                    break
        logger.info("Kepler Cheuvreux: %d offers", len(offers))
        return offers

    def _collect(self, item: dict) -> ScrapedOffer | None:
        title = (item.get("title") or "").strip()
        job_url = item.get("url") or ""
        if not title or not job_url:
            return None
        m = _JOB_ID_RE.search(job_url)
        external_id = m.group(1) if m else (item.get("id") or job_url)
        description = html_to_text(item.get("content_html") or "")

        city = self._extract_city(title, description)
        if not city:
            return None  # non-European (e.g. New York, Dubai) or unrecognized
        location = f"{city}, {_CITY_COUNTRY[city]}"

        return ScrapedOffer(
            bank="Kepler Cheuvreux",
            external_id=str(external_id),
            role_title=title.title() if title.isupper() else title,
            location=location,
            apply_url=job_url,
            source_url=self.careers_url,
            description=description or None,
        )

    def _extract_city(self, title: str, description: str) -> str | None:
        # Trailing title segment ("... - NYON"), then a "Location: X" line in the body.
        candidates = []
        parts = [p.strip() for p in title.split(" - ") if p.strip()]
        if len(parts) >= 2:
            candidates.append(parts[-1])
        m = _LOCATION_LINE_RE.search(description or "")
        if m:
            candidates.append(m.group(1).strip())
        for cand in candidates:
            canon = _CITY_CANON.get(cand.lower())
            if canon:
                return canon
        return None
