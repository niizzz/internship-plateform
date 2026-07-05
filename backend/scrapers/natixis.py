"""Natixis careers scraper (Groupe BPCE WordPress API).

Natixis recruitment runs on a WordPress backend exposing a clean JSON search:
    POST https://recrutement.natixis.com/app/wp-json/bpce/v1/search/jobs
    body {lang, keyword, external:false, from, size, ...taxonomy filters}
Response: {"code":"all_good","data":{"total":N,"items":[...]}} where each item
carries title, contract, localisation, link and the full HTML description — so
no per-offer enrichment is needed. Listings are in French; S&T desk names
(Trading, Structuring, Sales, FX, ...) and the program filter handle both langs.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, html_to_text

logger = logging.getLogger(__name__)

API = "https://recrutement.natixis.com/app/wp-json/bpce/v1/search/jobs"
PAGE_SIZE = 50
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://recrutement.natixis.com",
    "Referer": "https://recrutement.natixis.com/nos-offres-demploi?external=false",
}
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", _html.unescape(s or ""))).strip()


def _as_text(v) -> str:
    if isinstance(v, list):
        return ", ".join(_as_text(x) for x in v if x)
    if isinstance(v, dict):
        return _as_text(v.get("label") or v.get("name") or v.get("title") or "")
    return _clean(str(v or ""))


class NatixisScraper(BankScraper):
    bank_name = "Natixis"
    careers_url = "https://recrutement.natixis.com/nos-offres-demploi"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            frm = 0
            total = None
            while True:
                body = {
                    "lang": "fr", "keyword": "", "tax_sector": "", "tax_contract": "",
                    "tax_place": "", "tax_job": "", "tax_experience": "", "tax_degree": "",
                    "tax_brands": "", "tax_department": "", "tax_city": "", "tax_country": "",
                    "tax_channel": "", "jobcode": "", "tax_community_job": "",
                    "external": False, "userID": "", "from": frm, "size": PAGE_SIZE,
                }
                try:
                    r = await client.post(API, json=body)
                    r.raise_for_status()
                    data = r.json().get("data", {})
                except Exception as e:
                    logger.warning("Natixis from=%d failed: %s", frm, e)
                    break
                items = data.get("items") or []
                if not items:
                    break
                total = data.get("total", total)
                for it in items:
                    ext_id = str(it.get("advert_id") or it.get("post_id") or "")
                    if not ext_id or ext_id in offers:
                        continue
                    link = it.get("link")
                    if isinstance(link, dict):
                        link = link.get("url") or link.get("href") or ""
                    apply_url = link or f"https://recrutement.natixis.com/nos-offres-demploi?jobid={ext_id}"
                    offers[ext_id] = ScrapedOffer(
                        bank="Natixis",
                        external_id=ext_id,
                        role_title=_clean(it.get("title") or ""),
                        location=_as_text(it.get("localisation") or it.get("localisations")),
                        apply_url=apply_url,
                        source_url=self.careers_url,
                        description=html_to_text(it.get("description") or "") or None,
                        program_type=_as_text(it.get("contract")) or None,
                    )
                frm += PAGE_SIZE
                if total is not None and frm >= total:
                    break
                if frm > 1500:
                    break
                await asyncio.sleep(0.2)
        logger.info("Natixis: collected %d offers (pre-filter)", len(offers))
        return list(offers.values())
