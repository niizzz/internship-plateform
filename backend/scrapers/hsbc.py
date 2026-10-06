"""HSBC early-careers scraper (Emerging Talent board).

HSBC's STUDENT / graduate programmes are NOT on mycareer.hsbc.com — that board
(previously scraped here) is the experienced / Global-Service-Centre pool and
returns only "GSC" Poland roles for every keyword. The real early-careers
programmes (incl. "Markets - Sales and Trading - Internship / Graduate") live on
HSBC's Emerging Talent board:

    https://apply.careers.hsbc.com/emergingtalent/search-jobs/results

a Symphony-Talent site that server-renders the whole result set as HTML (no bot
wall, plain httpx). Each posting is an <li class="job-tile job-id-<ID>"> with a
stable id scheme (job-<ID>-...-section-<field>-value) carrying location,
department (= "Sales and Investments" / "Trading" for S&T), programme type, and
open/close dates; the human title is the tile's anchor text. Full descriptions
come from the detail page's <span class="jobdescription">.

The board is GLOBAL — we keep only European tiles (ISO country codes are mapped
to names so the shared Europe filter recognises "Sheffield, GB" etc.). Off-season
(July–Aug) the board is mostly APAC + a few UK insight programmes; London Markets
S&T internships appear Sept–Nov. NB pagination: at low volume (~20 roles) the
board fits on one page. Pagination is by `startrow` in steps of 25 (verified
in-season 2026-10-01: pageNumber is ignored); the loop stops when a page adds no
new ids.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

BASE = "https://apply.careers.hsbc.com"
# Paginate with startrow, NOT pageNumber: pageNumber is ignored server-side and
# every page re-serves the first 25 tiles (in-season the board had 115 roles and
# we saw only the first 25 — London S&T was beyond row 25).
RESULTS_TMPL = BASE + "/emergingtalent/search-jobs/results?ActiveFacet=0&locale=en_GB&startrow={start}"
PAGE_SIZE = 25
MAX_PAGES = 20

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
}

# ISO-3166 alpha-2 -> country name, for European codes the board emits. The
# shared filter matches on names, not codes, so "Sheffield, GB" would otherwise
# be dropped. Non-European codes (SG/HK/US/CN/IN/AE...) are intentionally absent
# so those tiles fail the Europe check.
_ISO2_EUROPE = {
    "GB": "United Kingdom", "FR": "France", "DE": "Germany", "ES": "Spain",
    "IT": "Italy", "NL": "Netherlands", "BE": "Belgium", "LU": "Luxembourg",
    "IE": "Ireland", "CH": "Switzerland", "PT": "Portugal", "SE": "Sweden",
    "DK": "Denmark", "NO": "Norway", "FI": "Finland", "PL": "Poland",
    "AT": "Austria", "CZ": "Czech Republic", "HU": "Hungary", "GR": "Greece",
    "RO": "Romania",
}

_TILE_SPLIT_RE = re.compile(r'<li class="job-tile job-id-(\d+)', re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_JD_RE = re.compile(r'<span class="jobdescription">(.*?)</span>\s*</div>\s*</div>', re.S)


def _clean(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", s or "")).strip()


def _field(chunk: str, jid: str, field: str) -> str:
    m = re.search(r'id="job-%s[^"]*section-%s-value"[^>]*>(.*?)</div>' % (jid, field),
                  chunk, re.S | re.I)
    return _clean(m.group(1)) if m else ""


def _europe_location(raw: str) -> str:
    """Rewrite "City, ISO2, postcode" into "City, Country" so the shared filter
    recognises it. Returns "" when no European ISO code / city is present."""
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not parts:
        return ""
    city = parts[0]
    for p in parts[1:]:
        if p.upper() in _ISO2_EUROPE:
            return f"{city}, {_ISO2_EUROPE[p.upper()]}"
    # No mappable European code — hand the raw string to the Europe filter, which
    # may still recognise a European city name (London etc.).
    return raw


def _to_iso_date(val: str) -> str | None:
    """'Mon Jul 06, 2026' -> '2026-07-06' (normalize_start_date/posted parser
    handle ISO cleanly); the leading weekday is stripped first."""
    s = re.sub(r"^[A-Za-z]{3,9}\s+", "", (val or "").strip())
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


class HSBCScraper(BankScraper):
    bank_name = "HSBC"
    careers_url = "https://www.hsbc.com/careers/students-and-graduates/find-a-programme"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        pages_ok = 0
        async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            for page in range(1, MAX_PAGES + 1):
                try:
                    r = await client.get(RESULTS_TMPL.format(start=(page - 1) * PAGE_SIZE))
                    r.raise_for_status()
                except Exception as e:
                    if pages_ok == 0:
                        raise RuntimeError(f"HSBC: Emerging Talent board page 1 failed: {e}") from e
                    logger.warning("HSBC: results page %d failed (partial kept): %s", page, e)
                    break
                pages_ok += 1
                added = self._parse(r.text, offers)
                if added == 0:
                    break  # no new tiles -> reached the end (or single-page board)
                await asyncio.sleep(0.2)

            to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
            await self._enrich(client, to_enrich)

        logger.info("HSBC: %d European Emerging-Talent offers (%d enriched)",
                    len(offers), len(to_enrich))
        return list(offers.values())

    def _parse(self, html: str, offers: dict[str, ScrapedOffer]) -> int:
        added = 0
        # Split into per-tile chunks keyed by job id.
        marks = list(_TILE_SPLIT_RE.finditer(html))
        for i, m in enumerate(marks):
            jid = m.group(1)
            if jid in offers:
                continue
            chunk = html[m.start():(marks[i + 1].start() if i + 1 < len(marks) else len(html))]
            data_url = re.search(r'data-url="([^"]+)"', chunk)
            if not data_url:
                continue
            # Title = the tile anchor text (mobile/desktop/tablet copies are identical).
            tm = re.search(r'<a[^>]*href="[^"]*/%s/"[^>]*>(.*?)</a>' % jid, chunk, re.S)
            title = _clean(tm.group(1)) if tm else ""
            if not title:
                continue
            location = _europe_location(_field(chunk, jid, "location"))
            department = _field(chunk, jid, "department")
            programme = _field(chunk, jid, "customfield5")   # e.g. "Graduate Programme"
            travel = _field(chunk, jid, "travel")            # "Mon Jul 26, 2027; 2 years"
            opened = _field(chunk, jid, "customfield1")      # open/posting date

            start_iso = duration = None
            if travel:
                seg = [x.strip() for x in travel.split(";")]
                start_iso = _to_iso_date(seg[0])
                if len(seg) > 1 and seg[1]:
                    duration = seg[1]

            offers[jid] = ScrapedOffer(
                bank="HSBC",
                external_id=jid,
                role_title=title,
                location=location,
                apply_url=data_url.group(1) if data_url.group(1).startswith("http")
                          else BASE + data_url.group(1),
                source_url=self.careers_url,
                # Department names the S&T area ("Sales and Investments" / "Trading");
                # seed the description with it so classification/tailoring see it even
                # before the detail-page enrich runs.
                description=(f"Area of Interest: {department}" if department else None),
                start_date_raw=start_iso,
                duration=duration,
                posted_raw=_to_iso_date(opened),
                program_type=programme or None,
                extras={"detail_url": data_url.group(1)},
            )
            added += 1
        return added

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        sem = asyncio.Semaphore(4)

        async def one(o: ScrapedOffer):
            url = o.extras.get("detail_url") or o.apply_url
            if url and not url.startswith("http"):
                url = BASE + url
            async with sem:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        return
                except Exception:
                    return
                await asyncio.sleep(0.15)
            m = _JD_RE.search(r.text) or re.search(r'<span class="jobdescription">(.*)', r.text, re.S)
            if m:
                clean = html_to_text(m.group(1))
                if clean:
                    # Keep the department line as a lead-in if the JD dropped it.
                    o.description = clean

        await asyncio.gather(*(one(o) for o in offers))
