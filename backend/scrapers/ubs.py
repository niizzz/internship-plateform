"""UBS careers scraper (IBM Kenexa BrassRing / TGnewUI).

BrassRing requires session cookies; its JSON Ajax endpoints reject direct hits.
We drive the listing page with Playwright, scroll to load all roles, then read
the rendered DOM. siteid=5012 is UBS's external careers site.
"""
from __future__ import annotations

import asyncio
import logging
import re

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text

logger = logging.getLogger(__name__)

MAX_DETAIL_PAGES = 40  # detail visits are Playwright navigations — keep bounded
MAX_LIST_PAGES = 10    # BrassRing pages; each next-click waits on the network

LISTING_URL = "https://jobs.ubs.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid=25008&siteid=5012&PageType=searchResults"
JOB_URL_TMPL = "https://jobs.ubs.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid=25008&siteid=5012&PageType=JobDetails&jobid={jobid}"

JOBID_RE = re.compile(r"[?&]jobid=(\d+)")


class UBSScraper(BankScraper):
    bank_name = "UBS"
    careers_url = "https://www.ubs.com/global/en/careers/students.html"

    async def scrape(self) -> list[ScrapedOffer]:
        page = await self.new_page()
        offers: dict[str, ScrapedOffer] = {}
        try:
            await page.goto(LISTING_URL, wait_until="networkidle", timeout=45_000)
            await page.wait_for_timeout(1500)

            # Paginate by clicking "next" pagination links until they disappear
            for _ in range(MAX_LIST_PAGES):
                await self._scroll_full(page)
                await self._collect(page, offers)
                # BrassRing pagination — look for next-page link
                next_btn = await page.query_selector(
                    "a[aria-label='Next page'], a[title='Next'], button[aria-label='Next page'], a.next, li.next a"
                )
                if not next_btn:
                    break
                try:
                    await next_btn.click(timeout=2_000)
                    await page.wait_for_load_state("domcontentloaded", timeout=5_000)
                    await page.wait_for_timeout(500)
                except Exception:
                    break
        finally:
            await page.close()

        # The card only carries a truncated snippet; pull the full description
        # for likely early-careers roles from their detail pages.
        eligible = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
        to_enrich = eligible[:MAX_DETAIL_PAGES]
        if len(eligible) > len(to_enrich):
            logger.warning("UBS: enrich cap hit — %d early-careers offers left without "
                           "descriptions", len(eligible) - len(to_enrich))
        if to_enrich:
            await self._enrich(to_enrich)

        logger.info("UBS: collected %d candidate offers (%d enriched)", len(offers), len(to_enrich))
        return list(offers.values())

    async def _enrich(self, offers: list[ScrapedOffer]) -> None:
        page = await self.new_page()
        try:
            for o in offers:
                try:
                    await page.goto(o.apply_url, wait_until="domcontentloaded", timeout=30_000)
                    try:
                        await page.wait_for_selector(
                            ".jobdescription, [class*='jobDescription'], .jobDisplay", timeout=5_000)
                    except Exception:
                        pass
                    raw = await page.evaluate(
                        """() => {
                            // BrassRing embeds a JobPosting JSON-LD blob with the full description.
                            for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                                try {
                                    const j = JSON.parse(s.textContent);
                                    for (const it of (Array.isArray(j) ? j : [j])) {
                                        if (it && it['@type'] === 'JobPosting' && it.description) return it.description;
                                    }
                                } catch (e) {}
                            }
                            const el = document.querySelector('.jobdescription, [class*="jobDescription"], .jobDisplay');
                            return el ? el.innerHTML : '';
                        }""")
                    clean = html_to_text(raw or "")
                    if clean and len(clean) > len(o.description or ""):
                        o.description = clean
                except Exception:
                    continue
        finally:
            await page.close()

    async def _scroll_full(self, page) -> None:
        prev = -1
        for _ in range(8):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(500)
            h = await page.evaluate("document.body.scrollHeight")
            if h == prev:
                break
            prev = h

    async def _collect(self, page, offers: dict[str, ScrapedOffer]) -> None:
        items = await page.eval_on_selector_all(
            "li.job",
            """(els) => els.map(card => {
                const a = card.querySelector('a.jobtitle');
                return {
                    href: a?.href || '',
                    title: a?.innerText?.trim() || '',
                    location: card.querySelector('.jobProperty.position3')?.innerText?.trim() || '',
                    description: card.querySelector('.jobProperty.jobdescription')?.innerText?.trim() || '',
                };
            })""",
        )
        for it in items:
            m = JOBID_RE.search(it.get("href") or "")
            if not m:
                continue
            jid = m.group(1)
            if jid in offers or not it.get("title"):
                continue
            offers[jid] = ScrapedOffer(
                bank="UBS",
                external_id=jid,
                role_title=it["title"],
                location=it.get("location") or "",
                apply_url=JOB_URL_TMPL.format(jobid=jid),
                source_url=self.careers_url,
                description=it.get("description") or None,
            )
