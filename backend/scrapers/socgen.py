"""Société Générale careers scraper.

SocGen's careers search is an Exalead index proxied through
`/search-proxy.php`, which requires two headers the page supplies at runtime:
an `authorization-api` JWT (fetched from /sg-careers-offers/get-token, itself
bot-protected) and a fixed `x-proxy-url`. Both are bot-blocked to plain HTTP,
so we load the search page in Playwright, capture those headers from the page's
own request, then call the proxy from within the page context (which carries
the session) paginating over all `sourcestr6=job` documents.

SocGen is a major equity-derivatives / markets house, so this is high-value for
S&T coverage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text, in_europe

logger = logging.getLogger(__name__)

SEARCH_PAGE = "https://careers.societegenerale.com/en/search"
PROXY_PATH = "/search-proxy.php"
PAGE_SIZE = 10   # the proxy rejects larger skipCount
MAX_OFFERS = 1000  # ~100 proxy calls; S&T early-careers roles are well within this

_ID_RE = re.compile(r"-([0-9A-Za-z]{6,})-[a-z]{2}$")


def _geo_to_location(geo: str) -> str:
    """Pull human-readable place names out of SocGen's encoded geo blob.

    Format is repeating `KEY_UPPER;Display Name;offset,data;...`. Display names
    are the mixed-case tokens with no digits (e.g. "London", "United Kingdom",
    "Ile-de-France"); ALL-CAPS keys and numeric offset tokens are skipped.
    """
    if not geo:
        return ""
    names = []
    for tok in geo.split(";"):
        tok = tok.strip()
        if not tok or tok.isupper():
            continue
        if any(c.isdigit() for c in tok):
            continue
        if any(c.isalpha() for c in tok) and tok not in names:
            names.append(tok)
    return ", ".join(names)


class SocGenScraper(BankScraper):
    bank_name = "Société Générale"
    careers_url = "https://careers.societegenerale.com/en/search"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        page = await self.new_page()
        captured: dict[str, str] = {}

        async def on_req(req):
            # Keep refreshing — the JWT is short-lived, so always store the latest.
            if PROXY_PATH in req.url and req.method == "POST":
                captured["auth"] = req.headers.get("authorization-api", "")
                captured["xpu"] = req.headers.get("x-proxy-url", "")

        page.on("request", on_req)
        try:
            # domcontentloaded, not networkidle: the SocGen SPA polls in the
            # background and may never reach networkidle (a 45s hang). The JWT we
            # need rides the page's own search XHR — poll for it instead.
            try:
                await page.goto(SEARCH_PAGE, wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass
            for _ in range(30):          # up to ~15s for the search XHR to fire
                if captured.get("auth"):
                    break
                await page.wait_for_timeout(500)
            if not captured.get("auth"):
                # Total failure (bot wall / page changed) — raise so the
                # orchestrator marks the bank FAILED instead of treating an
                # empty list as "SocGen removed every posting".
                raise RuntimeError("SocGen: could not capture proxy auth headers (bot wall?)")

            skip_from = 0
            total = None
            while skip_from < MAX_OFFERS:
                batch = await self._fetch_page(page, captured, skip_from)
                if batch is None:
                    # Likely an expired JWT — reload the page to mint a fresh
                    # token (re-fires the native request) and retry once.
                    logger.info("SocGen: refreshing token at skip=%d", skip_from)
                    try:
                        try:
                            await page.goto(SEARCH_PAGE, wait_until="domcontentloaded", timeout=30_000)
                        except Exception:
                            pass
                        await page.wait_for_timeout(2500)  # let the fresh JWT XHR fire
                    except Exception:
                        pass
                    batch = await self._fetch_page(page, captured, skip_from)
                    if batch is None:
                        logger.warning("SocGen: page fetch failed at skip=%d after refresh", skip_from)
                        break
                total, docs = batch
                if not docs:
                    break
                for d in docs:
                    url = d.get("url1") or d.get("resulturl") or ""
                    m = _ID_RE.search(url)
                    ext_id = m.group(1) if m else url.rsplit("/", 1)[-1]
                    if not ext_id or ext_id in offers:
                        continue
                    offers[ext_id] = ScrapedOffer(
                        bank=self.bank_name,
                        external_id=ext_id,
                        role_title=(d.get("title") or "").strip(),
                        location=_geo_to_location(d.get("geo") or ""),
                        apply_url=url,
                        source_url=self.careers_url,
                    )
                skip_from += PAGE_SIZE
                if total is not None and skip_from >= total:
                    break
                await asyncio.sleep(0.1)
        finally:
            await page.close()

        # The Exalead docs carry no description at all. Offer detail pages are
        # plain Drupal (only the search proxy is bot-walled), so fetch them
        # with httpx for likely early-careers roles and read the JobPosting
        # JSON-LD (description + datePosted) with an HTML fallback.
        # Only European early-careers roles survive the orchestrator filter, so
        # only fetch descriptions for those (SocGen has ~1250 jobs of every kind;
        # enriching all program-matching ones was the slow tail). Capped for safety.
        eligible = [o for o in offers.values()
                    if o.apply_url and _PROGRAM_RE.search(o.role_title.lower())
                    and in_europe(o.location or "")]
        to_enrich = eligible[:80]
        if len(eligible) > len(to_enrich):
            logger.warning("SocGen: enrich cap hit — %d of %d early-careers offers "
                           "left without descriptions", len(eligible) - len(to_enrich), len(eligible))
        await self._enrich(to_enrich)

        logger.info("Société Générale: collected %d offers (%d enriched)", len(offers), len(to_enrich))
        return list(offers.values())

    async def _enrich(self, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml",
        }
        async with httpx.AsyncClient(timeout=25, headers=headers, follow_redirects=True) as client:
            sem = asyncio.Semaphore(4)

            async def one(o: ScrapedOffer):
                async with sem:
                    try:
                        r = await client.get(o.apply_url)
                        if r.status_code != 200:
                            return
                    except Exception:
                        return
                    await asyncio.sleep(0.1)
                # <main> keeps headings/bullets AND the "Start date YYYY/MM/DD"
                # metadata line; the JSON-LD description is pre-flattened text.
                clean = ""
                m = re.search(r'<main\b[^>]*>(.*?)</main>', r.text, re.S | re.I)
                if m:
                    clean = html_to_text(m.group(1))
                if not clean:
                    for mj in re.finditer(
                            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', r.text, re.S):
                        try:
                            data = json.loads(mj.group(1))
                        except Exception:
                            continue
                        for item in (data if isinstance(data, list) else [data]):
                            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                                clean = html_to_text(item.get("description") or "")
                                break
                        if clean:
                            break
                if not clean:
                    return
                md = re.search(r"start date\s*:?\s*(\d{4}/\d{2}/\d{2})", clean, re.I)
                if md:
                    o.start_date_raw = md.group(1)
                # The header block also carries the bank's own publication date —
                # grab it for posted_at BEFORE that block gets dropped below.
                mp = re.search(r"publication date\s*:?\s*(\d{4}/\d{2}/\d{2})", clean, re.I)
                if mp:
                    o.posted_raw = mp.group(1)
                # Drop the page-header block (breadcrumb / Apply / reference /
                # dates) — the actual posting starts after "Publication date".
                idx = clean.lower().find("publication date")
                if idx != -1:
                    nl = clean.find("\n", idx)
                    if nl != -1:
                        clean = clean[nl + 1:].strip()
                o.description = clean

            await asyncio.gather(*(one(o) for o in offers))

    async def _fetch_page(self, page, captured: dict, skip_from: int):
        """Call the proxy from page context; returns (total, docs) or None."""
        try:
            return await page.evaluate(
                """async (args) => {
                    const [auth, xpu, skipFrom] = args;
                    const body = {
                        profile: 'ces_profile_sgcareers',
                        query: { advanced: [{ type:'simple', name:'sourcestr6', op:'eq', value:'job' }],
                                 skipCount: 10, skipFrom: skipFrom },
                        lang: 'en', responseType: 'SearchResult'
                    };
                    const r = await fetch('/search-proxy.php', {
                        method: 'POST',
                        headers: { 'Content-Type':'application/json', 'authorization-api': auth, 'x-proxy-url': xpu },
                        body: JSON.stringify(body)
                    });
                    const txt = await r.text();
                    let j; try { j = JSON.parse(txt); } catch (e) { return null; }
                    const docs = (j.Result || {}).Docs || [];
                    return [j.TotalCount || 0, docs.map(d => ({
                        title: d.title, geo: d.geo, url1: d.url1, resulturl: d.resulturl
                    }))];
                }""",
                [captured["auth"], captured["xpu"], skip_from],
            )
        except Exception as e:
            logger.warning("SocGen fetch error at skip=%d: %s", skip_from, e)
            return None
