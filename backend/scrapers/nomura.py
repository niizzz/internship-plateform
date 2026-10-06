"""Nomura careers scraper (Lumesse tal.net — "Nomura Global Campus").

The old nomura.tal.net board is retired ("Nomura (Old)", login-walled). The live
early-careers board is:
    https://nomuracampus.tal.net/candidate/jobboard/vacancy/1/adv/
(board 1 = Student Opportunities; board 2 is insight/diversity events, skipped).
Server-rendered HTML; each vacancy is a `candidate-opp-tile` div with
data-oppid/data-title, a subject link, and Location / Application Deadline rows.
Detail pages carry labeled fields (Region, Division, Location, Program type,
Level, Job description). Off-season the board simply renders zero tiles, which
this scraper treats as a clean empty result — offers appear automatically once
programmes open (~September).
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, html_to_text, talnet_detail_fields

logger = logging.getLogger(__name__)

BOARD_URL = "https://nomuracampus.tal.net/candidate/jobboard/vacancy/1/adv/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

_TILE_RE = re.compile(
    r'<div class="opp_\d+ search_res details_row candidate-opp-tile"\s+data-oppid="(?P<oppid>\d+)"[^>]*'
    r'data-title="(?P<title>[^"]*)"(?P<body>.*?)(?=<div class="opp_\d+ search_res details_row candidate-opp-tile"|<!--|$)',
    re.DOTALL,
)
_HREF_RE = re.compile(r'<a class="subject" href="([^"]+)"')
_LOCATION_RE = re.compile(r'Location:</span>\s*([^<]+)')
_DEADLINE_RE = re.compile(r'Application Deadline:</span>\s*([^<]+)')


class NomuraScraper(BankScraper):
    bank_name = "Nomura"
    careers_url = "https://www.nomura.com/careers/early-careers/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: list[ScrapedOffer] = []
        async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            r = await client.get(BOARD_URL)
            r.raise_for_status()
            html = r.text
            for m in _TILE_RE.finditer(html):
                o = self._collect(m)
                if o:
                    offers.append(o)
            if not offers:
                logger.info("Nomura: board has no student vacancies right now (off-season is normal)")
                return []
            await self._enrich(client, offers)
        logger.info("Nomura: %d offers", len(offers))
        return offers

    def _collect(self, m: re.Match) -> ScrapedOffer | None:
        body = m.group("body")
        href = _HREF_RE.search(body)
        if not href:
            return None
        title = re.sub(r"\s+", " ", m.group("title")).replace("�", "–").strip()
        loc = _LOCATION_RE.search(body)
        deadline = _DEADLINE_RE.search(body)
        location = loc.group(1).strip() if loc else ""
        extras = {}
        if deadline:
            extras["application_deadline"] = deadline.group(1).strip()
        return ScrapedOffer(
            bank="Nomura",
            external_id=m.group("oppid"),
            role_title=title,
            location=location,
            apply_url=href.group(1),
            source_url=BOARD_URL,
            extras=extras,
        )

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        sem = asyncio.Semaphore(4)

        async def one(o: ScrapedOffer):
            async with sem:
                try:
                    r = await client.get(o.apply_url)
                    if r.status_code != 200:
                        return
                    fields = talnet_detail_fields(r.text)
                except Exception:
                    return
            desc = html_to_text(fields.get("job description") or "")
            division = html_to_text(fields.get("division") or "")
            program = html_to_text(fields.get("program type") or "")
            if desc:
                # Prepend division/program so the S&T filter and tailoring see them.
                header = " — ".join(x for x in (division, program) if x)
                o.description = (header + "\n\n" + desc).strip() if header else desc
            if not o.location and fields.get("location"):
                o.location = html_to_text(fields["location"])

        await asyncio.gather(*(one(o) for o in offers))
