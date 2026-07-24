"""UniCredit careers scraper (Avature).

UniCredit runs Avature at careers.unicredit.eu (the group's unified board;
jobs.unicredit.eu / ucjobs.unicredit.eu are geo/WAF-walled and unreachable, and
the www.unicreditgroup.eu careers page hard-blocks automation — this Avature host
is the one that answers plain httpx). Keyword search is the classic Avature path:

    /en_GB/jobsuche/SearchJobs/<keyword>?listFilterMode=1

Result cards are <article class="article article--result"> carrying the title +
a /JobDetail/<slug>/<jobId> link, but NO location — so location, description and
the structured "General Information" fields (Country, City, Job Type, Company)
come from the detail page, whose fields render as ordered
`article__content__view__field` label/value cells.

The board is the whole pan-European group (heavy on CEE retail/branch roles), so
we sweep front-office S&T keywords, enrich only the cards whose TITLE is an
internship/graduate role, and let the shared filter trim to S&T + Europe.
UniCredit's markets desks are Milan (Italy) and Munich (HypoVereinsbank); titles
arrive in EN/IT/DE (tirocinio / working student / Praktikum) — all covered by the
program regex. S&T interns here are seasonal, so an empty keep is normal.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

BASE = "https://careers.unicredit.eu"
SEARCH_TMPL = BASE + "/en_GB/jobsuche/SearchJobs/{kw}?listFilterMode=1"
DETAIL_TMPL = BASE + "/en_GB/jobsuche/JobDetail/{jid}"

# Front-office markets keywords. Broad enough to surface S&T roles across the
# group's languages; the title/desc filter does the precise S&T call downstream.
QUERIES = [
    "markets", "trading", "structuring", "structurer", "quantitative",
    "derivatives", "sales", "fixed income", "securities",
    # local-language desk terms
    "handel", "kapitalmarkt", "strukturierung", "mercati", "negoziazione",
]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
}

_CARD_RE = re.compile(r'<article class="article article--result">(.*?)</article>', re.S)
_TITLE_RE = re.compile(
    r'article__header__text__title[^>]*>\s*<a href="([^"]+)">\s*(.*?)\s*</a>', re.S)
_JOBID_RE = re.compile(r'/JobDetail/(?:[^/"]+/)?(\d+)')
_FIELD_RE = re.compile(r'article__content__view__field[a-z_-]*"[^>]*>(.*?)</', re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", _html.unescape(s or ""))).strip()


class UniCreditScraper(BankScraper):
    bank_name = "UniCredit"
    careers_url = "https://careers.unicredit.eu/en_GB/jobsuche"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        ok = 0
        async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            sem = asyncio.Semaphore(4)

            async def search(kw: str):
                nonlocal ok
                async with sem:
                    try:
                        r = await client.get(SEARCH_TMPL.format(kw=kw))
                        r.raise_for_status()
                    except Exception as e:
                        logger.warning("UniCredit search %r failed: %s", kw, e)
                        return None
                ok += 1
                return r.text

            pages = await asyncio.gather(*(search(kw) for kw in QUERIES))
            if ok == 0:
                raise RuntimeError("UniCredit: every Avature keyword search failed")
            for text in pages:
                if text:
                    self._parse(text, offers)

            # Enrich only internship/graduate-titled cards (bounds detail fetches
            # and is what makes an offer keepable — the program gate is TITLE-only).
            to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
            await self._enrich(client, to_enrich)

        # Drop cards we never enriched (no location -> can't Europe-filter, and
        # they aren't early-careers anyway).
        kept = [o for o in offers.values() if o.location]
        logger.info("UniCredit: %d S&T-keyword cards, %d early-careers enriched, %d located",
                    len(offers), len(to_enrich), len(kept))
        return kept

    def _parse(self, html: str, offers: dict[str, ScrapedOffer]) -> None:
        for card in _CARD_RE.findall(html):
            tm = _TITLE_RE.search(card)
            if not tm:
                continue
            url, title = tm.group(1), _clean(tm.group(2))
            idm = _JOBID_RE.search(url)
            if not idm or not title:
                continue
            jid = idm.group(1)
            if jid in offers:
                continue
            offers[jid] = ScrapedOffer(
                bank="UniCredit",
                external_id=jid,
                role_title=title,
                location="",  # filled at enrich from the detail Country/City fields
                apply_url=url if url.startswith("http") else BASE + url,
                source_url=self.careers_url,
                extras={"jid": jid},
            )

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        sem = asyncio.Semaphore(5)

        async def one(o: ScrapedOffer):
            async with sem:
                try:
                    r = await client.get(DETAIL_TMPL.format(jid=o.extras["jid"]))
                    if r.status_code != 200:
                        return
                except Exception:
                    return
                await asyncio.sleep(0.15)
            labels, prose = self._fields(r.text)
            city, country = labels.get("City"), labels.get("Country")
            o.location = ", ".join(p for p in (city, country) if p) or country or ""
            # The trailing free-text cells are the JD body (role, profile,
            # about-us); join the substantive ones as the description.
            body = "\n\n".join(p for p in prose if len(p) > 60)
            if body:
                o.description = body[:7000]

        await asyncio.gather(*(one(o) for o in offers))

    @staticmethod
    def _fields(html: str) -> tuple[dict, list[str]]:
        """Split the ordered `article__content__view__field` cells into the
        General-Information label/value dict and the trailing free-text prose
        cells (JD body). Pairing stops at the first cell too long to be a label."""
        cells = [_clean(c) for c in _FIELD_RE.findall(html)]
        cells = [c for c in cells if c]
        out: dict = {}
        i = 0
        while i + 1 < len(cells):
            label, value = cells[i], cells[i + 1]
            if len(label) > 40:  # reached description prose, not a label
                break
            out[label] = value
            i += 2
        return out, cells[i:]
