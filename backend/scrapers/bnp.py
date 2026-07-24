"""BNP Paribas careers scraper.

The group portal (group.bnpparibas) is behind Akamai (403) and CIB Tal.net
times out. BNP's UK early-careers site runs WordPress with a public REST API
exposing the `gbjf_job_post` type — that's where London S&T internships live.
"""
from __future__ import annotations

import logging
import re
import html

import httpx

from .base import BankScraper, ScrapedOffer, html_to_text

logger = logging.getLogger(__name__)

UK_API = "https://careers.bnpparibas.co.uk/wp-json/wp/v2/gbjf_job_post"
PER_PAGE = 50

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class BNPParibasScraper(BankScraper):
    bank_name = "BNP Paribas"
    careers_url = "https://careers.bnpparibas.co.uk/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            page = 1
            while True:
                try:
                    r = await client.get(UK_API, params={"per_page": PER_PAGE, "page": page})
                    if r.status_code == 400:  # past last page
                        break
                    r.raise_for_status()
                    items = r.json()
                except Exception as e:
                    logger.warning("BNP UK page %d failed: %s", page, e)
                    break
                if not items:
                    break
                for it in items:
                    jid = str(it.get("id") or "")
                    if not jid or jid in offers:
                        continue
                    title = _clean((it.get("title") or {}).get("rendered") or "")
                    city = _clean(it.get("city") or "")
                    location = city or "London, United Kingdom"
                    if "kingdom" not in location.lower() and "london" not in location.lower():
                        location = f"{location}, United Kingdom" if location else "United Kingdom"
                    desc = html_to_text((it.get("content") or {}).get("rendered") or "")
                    offers[jid] = ScrapedOffer(
                        bank="BNP Paribas",
                        external_id=jid,
                        role_title=title,
                        location=location,
                        apply_url=it.get("link") or self.careers_url,
                        source_url=self.careers_url,
                        description=desc or None,
                        program_type=_clean(str(it.get("programme") or "")) or None,
                        # date_posted / WordPress `date` is the POSTING date (used
                        # for posted_at); the real START date is derived from text.
                        posted_raw=it.get("date_posted") or it.get("date"),
                    )
                page += 1
                if len(items) < PER_PAGE:
                    break
        logger.info("BNP Paribas: collected %d UK offers", len(offers))
        return list(offers.values())
