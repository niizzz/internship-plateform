"""Mizuho EMEA careers scraper (SuccessFactors RMK / Symphony-Talent board).

Mizuho EMEA (Mizuho International, London) applies via careers.mizuhoemea.com — a
SuccessFactors Recruiting-Marketing career site (the cbcareers.mizuho-emea.com /
jobs hosts are geo/DNS-blocked from here; this SF host answers plain httpx). The
job list is server-rendered and paginated:

    /tile-search-results/?startrow=N     (25 tiles/page; paginate startrow += 25)

Each posting is an <li class="job-tile job-id-<ID>"> with data-url="/job/<City>-
<slug>-<abbr>/<ID>/" and the title in <a class="jobTitle-link">. The city is the
leading (and repeated) segment of the slug — every European posting's slug
contains a full European city name (London / Frankfurt / Amsterdam …), while
non-European ones (Dubai, Riyadh) contain none, so a EUROPE_CITIES scan of the
slug yields the location without a detail fetch. (The /job/<id>/ detail page is
served only as a JS-shell to httpx/Chromium — no jobGeoLocation/jobdescription —
so descriptions are not enriched; classification is title-driven anyway. If
in-season descriptions are ever needed, the detail page needs the SF session
flow, a future upgrade.)

Mizuho EMEA is London-centric IB/markets with real S&T desks (Securitised
Products Trading, CLO Structuring, Structured Financing). Early-careers roles are
seasonal — the 2027 internship intake opens ~autumn 2026 — so off-season the
board is all full-time VP/Director roles and an empty keep is correct.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, EUROPE_CITIES

logger = logging.getLogger(__name__)

BASE = "https://careers.mizuhoemea.com"
TILES_TMPL = BASE + "/tile-search-results/?startrow={row}"
PAGE_STEP = 25
MAX_PAGES = 20

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
}

_TILE_RE = re.compile(
    r'<li class="job-tile job-id-(\d+).*?data-url="(/job/[^"]+)".*?'
    r'jobTitle-link[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Longest cities first so "the hague" wins over a bare token, word-boundary safe.
_CITY_RES = [(c, re.compile(r"(?<![a-zà-ÿ])" + re.escape(c) + r"(?![a-zà-ÿ])"))
             for c in sorted(EUROPE_CITIES, key=len, reverse=True)]


def _clean(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", _html.unescape(s or ""))).strip()


def _slug_location(slug: str) -> str:
    """First European city named anywhere in the /job/ slug, or "" (non-EU)."""
    s = _html.unescape(slug or "").replace("-", " ").replace("%2C", ",").lower()
    for city, rx in _CITY_RES:
        if rx.search(s):
            return city.title()
    return ""


class MizuhoScraper(BankScraper):
    bank_name = "Mizuho"
    careers_url = "https://careers.mizuhoemea.com/search/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        pages_ok = 0
        async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            for page in range(MAX_PAGES):
                row = page * PAGE_STEP
                try:
                    r = await client.get(TILES_TMPL.format(row=row))
                    r.raise_for_status()
                except Exception as e:
                    if pages_ok == 0:
                        raise RuntimeError(f"Mizuho: tile-search page 1 failed: {e}") from e
                    logger.warning("Mizuho: tile page startrow=%d failed (partial kept): %s", row, e)
                    break
                pages_ok += 1
                if self._parse(r.text, offers) == 0:
                    break
                await asyncio.sleep(0.2)

        logger.info("Mizuho: %d board roles", len(offers))
        return list(offers.values())

    def _parse(self, html: str, offers: dict[str, ScrapedOffer]) -> int:
        added = 0
        for m in _TILE_RE.finditer(html):
            jid, url, title = m.group(1), m.group(2), _clean(m.group(3))
            if not title or jid in offers:
                continue
            slug = url.split("/job/", 1)[-1].rsplit("/", 2)[0]
            offers[jid] = ScrapedOffer(
                bank="Mizuho",
                external_id=jid,
                role_title=title,
                location=_slug_location(slug),
                apply_url=url if url.startswith("http") else BASE + url,
                source_url=self.careers_url,
            )
            added += 1
        return added
