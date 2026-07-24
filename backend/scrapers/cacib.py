"""Crédit Agricole CIB careers scraper (Talentsoft).

CACIB runs Talentsoft at jobs.ca-cib.com. The English offer list renders
server-side (no JS needed) at:
    /offre-de-emploi/liste-offres.aspx?LCID=2057&page=N
Each `ts-offer-card` carries the title (anchor), then contract type, country
and city as text. Offer detail pages at /job/job-<slug>_<id>.aspx hold the
description, which we fetch for the early-careers survivors.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

LIST_TMPL = "https://jobs.ca-cib.com/offre-de-emploi/liste-offres.aspx?LCID=2057&page={page}"
JOB_TMPL = "https://jobs.ca-cib.com/job/{slug}.aspx"
MAX_PAGES = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

# Each offer is a `<div class="ts-offer-card Layer" ... onclick="location.href=
# '/job/<slug>_<id>.aspx'">` wrapping the title link and contract/country/city.
_CARD_SPLIT_RE = re.compile(r'<div class="ts-offer-card Layer"', re.I)
_SLUG_RE = re.compile(r"/job/(job-[a-z0-9-]+_(\d+))\.aspx", re.I)
_TITLE_RE = re.compile(r'ts-offer-card__title-link[^>]*>(.*?)</a>', re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_CONTRACT_RE = re.compile(
    r"^(internship/trainee|internship|trainee|permanent(?: contract)?|fixed[- ]term.*|"
    r"temporary.*|vie|apprentice.*|stage|cdi|cdd|alternance|graduate|contract)$",
    re.I,
)


def _clean(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", _html.unescape(s or ""))).strip()


# Bare Talentsoft form labels that survive html_to_text as their own lines.
_FORM_LABEL_LINES = {
    "vacancy details", "general information", "entity", "reference",
    "publication date", "update date",
}


def _strip_form_labels(text: str) -> str:
    lines = [ln for ln in text.split("\n") if ln.strip().lower() not in _FORM_LABEL_LINES]
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


class CACIBScraper(BankScraper):
    bank_name = "Crédit Agricole CIB"
    careers_url = "https://jobs.ca-cib.com/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            for page in range(1, MAX_PAGES + 1):
                try:
                    r = await client.get(LIST_TMPL.format(page=page))
                    r.raise_for_status()
                except Exception as e:
                    logger.warning("CACIB list page %d failed: %s", page, e)
                    break
                added = 0
                for chunk in _CARD_SPLIT_RE.split(r.text)[1:]:
                    ms = _SLUG_RE.search(chunk)
                    mt = _TITLE_RE.search(chunk)
                    if not ms or not mt:
                        continue
                    jid = ms.group(2)
                    if jid in offers:
                        continue
                    title = _clean(mt.group(1))
                    # After the title link come contract / country / city as text nodes.
                    after = chunk[mt.end():]
                    tokens = [t for t in (_clean(x) for x in re.split(r"<[^>]+>", after)) if t and len(t) > 1]
                    loc_tokens = [t for t in tokens if not _CONTRACT_RE.match(t)][:3]
                    location = ", ".join(dict.fromkeys(loc_tokens))
                    offers[jid] = ScrapedOffer(
                        bank="Crédit Agricole CIB",
                        external_id=jid,
                        role_title=title,
                        location=location,
                        apply_url=JOB_TMPL.format(slug=ms.group(1)),
                        source_url=self.careers_url,
                    )
                    added += 1
                if added == 0:
                    break
                await asyncio.sleep(0.3)

            to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
            await self._enrich(client, to_enrich)

        logger.info("Crédit Agricole CIB: %d offers (%d enriched)", len(offers), len(to_enrich))
        return list(offers.values())

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        sem = asyncio.Semaphore(5)

        async def one(o: ScrapedOffer):
            async with sem:
                try:
                    r = await client.get(o.apply_url)
                    if r.status_code != 200:
                        return
                except Exception:
                    return
            # Bank's own date for the posting. CACIB's Talentsoft pages carry no
            # JSON-LD — they render an "Update date dd/mm/yyyy" line, which is
            # the only signal of how long the ad has been live (and far closer to
            # the truth than when WE first scraped it). Try JSON-LD first anyway.
            mp = re.search(r'"datePosted"\s*:\s*"([^"]+)"', r.text)
            if not mp:
                # Flatten tags first: the label and the date sit in separate
                # elements, so a raw-HTML regex can't bridge them.
                flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
                mp = re.search(
                    r"(?:update date|publication date|date de (?:mise \S+ jour|publication))"
                    r"\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", flat, re.I)
            if mp:
                o.posted_raw = mp.group(1)
            # The job content lives in the `detail_offre` container (title,
            # contract, description, candidate criteria). Never fall back to the
            # whole page — it's full of nav/login/search-facet noise.
            m = re.search(
                r'id="detail_offre"[^>]*>(.*?)<div class="ts-offer-page__cta is-bottom',
                r.text, re.S | re.I,
            )
            if not m:
                m = re.search(r'<h2 class="JobDescription".*?>(.*?)<h2 class="Location"', r.text, re.S | re.I)
            if m:
                text = html_to_text(m.group(1))
                if text:
                    o.description = _strip_form_labels(text) or o.description

        await asyncio.gather(*(one(o) for o in offers))
