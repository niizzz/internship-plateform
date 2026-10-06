"""Morgan Stanley careers scraper (Students & Graduates board).

MS splits its postings across two systems and only ONE of them carries
programmes. The Workday `External` board (ms.wd5.myworkdayjobs.com), which this
scraper used to read, is experienced-hire only: a full EU pull on 2026-09-07
returned 114 roles and not a single internship or graduate programme, which is
why this bank sat at zero offers while S&T summer applications were open. There
is no `Campus`/`University` tenant on that Workday host (all 404), and the
morganstanley.eightfold.ai careers site mirrors the same experienced-hire feed.

Student and graduate programmes live on Morgan Stanley's own AEM career service,
which returns the WHOLE board — full HTML descriptions inline — in one request,
so there is no per-offer enrichment pass. Applications are handed off to tal.net.
"""
from __future__ import annotations

import html
import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, html_to_text

logger = logging.getLogger(__name__)

API = "https://www.morganstanley.com/web/career_services/webapp/service/careerservice/resultset.json"
CAREERS_URL = "https://www.morganstanley.com/careers/career-opportunities-search?opportunity=sg"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": CAREERS_URL,
}

# The feed escapes titles to "&amp;" and then drops the bare "&", leaving stubs
# like "Sales amp; Trading". Repair before is_snt_role ever reads the title.
_ENTITY_STUB_RE = re.compile(r"\bamp;\s*")


def _clean_title(raw: str) -> str:
    return " ".join(_ENTITY_STUB_RE.sub("& ", html.unescape(raw or "")).split())


class MorganStanleyScraper(BankScraper):
    bank_name = "Morgan Stanley"
    careers_url = CAREERS_URL

    async def scrape(self) -> list[ScrapedOffer]:
        params = {"opportunity": "sg", "lang": "EN", "location": ""}
        async with httpx.AsyncClient(timeout=45, headers=HEADERS) as client:
            try:
                r = await client.get(API, params=params)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                raise RuntimeError(
                    f"Morgan Stanley: students & graduates board failed: {e}") from e

        rows = data.get("resultSet") or []
        if not rows:
            # The board is never legitimately empty — MS keeps programmes listed
            # year-round. An empty resultSet means the feed moved or broke.
            raise RuntimeError("Morgan Stanley: board returned an empty resultSet")

        offers: dict[str, ScrapedOffer] = {}
        for j in rows:
            ext_id = str(j.get("jobNumber") or "").strip()
            # jobNumber is the requisition number MS shows as "Job #" and the id
            # tal.net applies against — stable across re-imports (invariant 10).
            if not ext_id or ext_id in offers:
                continue
            desc = html_to_text(j.get("jobHtmlDescription") or "") or (j.get("jobDescription") or "")
            deadline = str(j.get("applicationDate") or "").strip()
            if deadline:
                # No deadline column on Offer — surface it where the user reads.
                desc = f"Application deadline: {deadline}\n\n{desc}"
            location = j.get("location") or ", ".join(
                p for p in (j.get("city"), j.get("country")) if p)
            offers[ext_id] = ScrapedOffer(
                bank=self.bank_name,
                external_id=ext_id,
                role_title=_clean_title(j.get("jobTitle") or ""),
                location=location,
                apply_url=j.get("url") or CAREERS_URL,
                source_url=CAREERS_URL,
                description=desc,
                program_type=j.get("employmentType"),
                extras={
                    "job_number": ext_id,
                    "division": j.get("division"),
                    "business_area": j.get("businessArea"),
                    "application_deadline": deadline,
                },
            )

        logger.info("Morgan Stanley: %d students & graduates postings", len(offers))
        return list(offers.values())
