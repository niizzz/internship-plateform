"""UBS careers scraper (IBM Kenexa BrassRing / TGnewUI).

BrassRing requires session cookies; its JSON Ajax endpoints reject direct hits
(replaying MatchedJobs without the page's own session returns an HTML shell),
so we drive the listing page with Playwright and read the rendered DOM.

UBS runs TWO separate BrassRing sites under partnerid=25008:
  * siteid=5131 — "UBS Job Board for Graduates": the EARLY-CAREERS board, where
    every Summer Internship / Off-Cycle Internship / Graduate Talent Program
    posting lives, including "2027 Summer Internship - Global Markets - London"
    (the S&T one). None of these appear on 5012.
  * siteid=5012 — the professional/experienced board. Its "intern" hits are
    service-centre and support roles; kept because it is one extra page load
    and occasionally carries local internships (Poland/Luxembourg).
Scraping only 5012 (what this module did until 2026-08-17) meant the platform
never saw a single UBS early-careers role.
"""
from __future__ import annotations

import asyncio
import logging
import re

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text, in_europe

logger = logging.getLogger(__name__)

MAX_DETAIL_PAGES = 40  # detail visits are Playwright navigations — keep bounded
MAX_LIST_PAGES = 10    # BrassRing "Next >" clicks; each appends a page of cards
ENRICH_CONCURRENCY = 4  # detail tabs in flight; the whole scraper shares a 120s budget

# siteid -> label. 5131 first: it holds the roles we actually want.
SITE_IDS = {"5131": "graduates", "5012": "professional"}

LISTING_URL_TMPL = ("https://jobs.ubs.com/TGnewUI/Search/home/HomeWithPreLoad"
                    "?partnerid=25008&siteid={siteid}&PageType=searchResults")
JOB_URL_TMPL = ("https://jobs.ubs.com/TGnewUI/Search/home/HomeWithPreLoad"
                "?partnerid=25008&siteid={siteid}&PageType=JobDetails&jobid={jobid}")

JOBID_RE = re.compile(r"[?&]jobid=(\d+)")

# The cookie banner sits over the results and swallows the pagination clicks.
CONSENT_SEL = "#button-container_BUTTON_0"
# TGnewUI paginates with a "Next >" link, NOT a numbered pager or an
# aria-labelled button — the old selector list never matched it, so every run
# stopped at the first 50 cards.
NEXT_SEL = "a.showMoreJobs"


class UBSScraper(BankScraper):
    bank_name = "UBS"
    careers_url = "https://www.ubs.com/global/en/careers/early-careers.html"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        failures: list[str] = []
        for siteid in SITE_IDS:
            try:
                await self._scrape_board(siteid, offers)
            except Exception as exc:  # keep the other board's results
                failures.append(siteid)
                logger.warning("UBS: board %s failed: %s", siteid, exc)

        if len(failures) == len(SITE_IDS):
            raise RuntimeError("UBS: every board failed (%s)" % ", ".join(failures))
        if failures:
            logger.warning("UBS: partial results kept — board(s) %s failed",
                           ", ".join(failures))

        # The card only carries a truncated snippet; pull the full description
        # for likely early-careers roles from their detail pages. Both boards
        # are GLOBAL (5012 alone lists 500+ roles, mostly APAC/US support
        # internships), and a detail visit is a full page load — so only
        # European early-careers cards earn one. Without this gate the enrich
        # pass alone ran ~4 minutes and blew the orchestrator's 120s budget.
        eligible = [o for o in offers.values()
                    if _PROGRAM_RE.search(o.role_title.lower()) and in_europe(o.location)]
        to_enrich = eligible[:MAX_DETAIL_PAGES]
        if len(eligible) > len(to_enrich):
            logger.warning("UBS: enrich cap hit — %d early-careers offers left without "
                           "descriptions", len(eligible) - len(to_enrich))
        if to_enrich:
            await self._enrich(to_enrich)

        logger.info("UBS: collected %d candidate offers across %d board(s) (%d enriched)",
                    len(offers), len(SITE_IDS) - len(failures), len(to_enrich))
        return list(offers.values())

    async def _scrape_board(self, siteid: str, offers: dict[str, ScrapedOffer]) -> None:
        page = await self.new_page()
        before = len(offers)
        try:
            await page.goto(LISTING_URL_TMPL.format(siteid=siteid),
                            wait_until="networkidle", timeout=45_000)
            await page.wait_for_timeout(1500)
            await self._accept_cookies(page)

            for _ in range(MAX_LIST_PAGES):
                # The "Next >" link sits below the fold; one scroll is enough to
                # reach it (the list is already rendered, not lazy-loaded).
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(250)
                next_btn = await page.query_selector(NEXT_SEL)
                if not next_btn or not await next_btn.is_visible():
                    break
                seen = await page.evaluate("document.querySelectorAll('li.job').length")
                try:
                    await next_btn.click(timeout=3_000)
                except Exception:
                    break
                # "Next >" APPENDS the next page of cards to the same list, so
                # wait for the count to grow rather than for a navigation.
                if not await self._wait_for_more_cards(page, seen):
                    break

            # Collect once, after every page has been appended to the DOM.
            await self._collect(page, siteid, offers)
        finally:
            await page.close()
        logger.info("UBS: board %s (%s) -> %d offers",
                    siteid, SITE_IDS[siteid], len(offers) - before)

    async def _accept_cookies(self, page) -> None:
        try:
            btn = await page.query_selector(CONSENT_SEL)
            if btn and await btn.is_visible():
                await btn.click(timeout=3_000)
                await page.wait_for_timeout(1_000)
        except Exception:
            pass

    async def _enrich(self, offers: list[ScrapedOffer]) -> None:
        sem = asyncio.Semaphore(ENRICH_CONCURRENCY)

        async def one(o: ScrapedOffer) -> None:
            async with sem:
                page = await self.new_page()
                try:
                    await page.goto(o.apply_url, wait_until="domcontentloaded", timeout=30_000)
                    await self._accept_cookies(page)
                    try:
                        await page.wait_for_selector(
                            ".jobDetailText, .jobdescription, [class*='jobDescription'], .jobDisplay",
                            timeout=8_000)
                    except Exception:
                        pass
                    data = await page.evaluate(_DETAIL_JS)
                except Exception:
                    return
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass

                if not data:
                    return
                clean = html_to_text(data.get("descHtml") or "")
                if clean and len(clean) > len(o.description or ""):
                    o.description = clean
                # The graduates board only shows the COUNTRY on the card; the
                # city lives in a labelled field on the detail page.
                city = data.get("city")
                if city:
                    o.city = city
                    if not o.location:
                        o.location = city
                    elif city.lower() not in o.location.lower():
                        o.location = "%s, %s" % (city, o.location)
                if data.get("deadline"):
                    o.extras["application_deadline"] = data["deadline"]
                if data.get("reference"):
                    o.extras["job_reference"] = data["reference"]

        await asyncio.gather(*(one(o) for o in offers))

    async def _wait_for_more_cards(self, page, seen: int, timeout_ms: int = 6_000) -> bool:
        """Poll until the appended page of cards lands. False == no growth."""
        waited = 0
        while waited < timeout_ms:
            await page.wait_for_timeout(250)
            waited += 250
            if await page.evaluate("document.querySelectorAll('li.job').length") > seen:
                return True
        return False

    async def _collect(self, page, siteid: str, offers: dict[str, ScrapedOffer]) -> None:
        items = await page.eval_on_selector_all("li.job", _CARD_JS)
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
                apply_url=JOB_URL_TMPL.format(siteid=siteid, jobid=jid),
                source_url=self.careers_url,
                description=it.get("description") or None,
            )
            # UBS's own function taxonomy ("Sales and trading", "Risk",
            # "Information Technology (IT)"). Kept as metadata ONLY — it must
            # never be assigned to ScrapedOffer.category, because the
            # orchestrator treats a scraper-set category as "already
            # classified" and skips the S&T filter entirely, which let 60+
            # sanctions/accounting/IT internships through.
            if it.get("category"):
                offers[jid].extras["function_category"] = it["category"]


# `.jobProperty.position3` repeats: [0]=location, [1]=function category,
# [2]=business division on the graduates board; only [0] exists on 5012.
_CARD_JS = """(els) => els.map(card => {
    const a = card.querySelector('a.jobtitle');
    const props = Array.from(card.querySelectorAll('.jobProperty.position3'))
                       .map(e => (e.innerText || '').trim())
                       .filter(Boolean);
    const desc = card.querySelector('.jobProperty.jobdescription');
    return {
        href: a ? a.href : '',
        title: a && a.innerText ? a.innerText.trim() : '',
        location: props[0] || '',
        category: props[1] || '',
        description: desc && desc.innerText ? desc.innerText.trim() : '',
    };
})"""

# Detail pages on 5131 carry NO JobPosting JSON-LD (5012 does). Both render the
# body as alternating `.question` headings and `.answer.jobDetailText` blocks
# inside `div.questionClass`; the short labelled fields (Job Reference #, City,
# Application Deadline) use that same question/answer pairing.
_DETAIL_JS = """() => {
    const txt = e => ((e && e.innerText) || '').trim();
    const out = {descHtml: '', city: '', deadline: '', reference: ''};

    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
            const j = JSON.parse(s.textContent);
            for (const it of (Array.isArray(j) ? j : [j])) {
                if (it && it['@type'] === 'JobPosting' && it.description) {
                    out.descHtml = it.description;
                }
            }
        } catch (e) {}
    }

    const container = document.querySelector('div.questionClass');
    if (container) {
        const nodes = Array.from(container.querySelectorAll('.question, .answer'));
        const cls = n => (typeof n.className === 'string' ? n.className : '');
        const parts = [];
        for (let i = 0; i < nodes.length; i++) {
            const n = nodes[i];
            if (cls(n).indexOf('jobtitleInJobDetails') >= 0) continue;
            if (cls(n).indexOf('question') < 0) continue;   // answers are consumed below
            const label = txt(n);
            const next = nodes[i + 1];
            if (!label || !next || cls(next).indexOf('question') >= 0) continue;
            const value = txt(next);
            if (!value) continue;
            i++;  // consume the answer
            if (cls(next).indexOf('jobDetailText') >= 0) {
                // A real prose section ("Your role", "Your team", ...).
                parts.push('<h3>' + label + '</h3>');
                parts.push(next.innerHTML);
            } else {
                // A short labelled field (Job Reference #, City, Application
                // Deadline). Emitting the label as a heading with the value
                // dropped left orphan headings in the offer description.
                parts.push('<p><b>' + label + '</b>: ' + value + '</p>');
            }
        }
        if (parts.length) {
            const built = parts.join('\\n');
            if (built.length > out.descHtml.length) out.descHtml = built;
        }

        // Labelled short fields: a .question immediately followed by its .answer.
        for (let i = 0; i < nodes.length - 1; i++) {
            const cls = typeof nodes[i].className === 'string' ? nodes[i].className : '';
            if (cls.indexOf('question') < 0) continue;
            const label = txt(nodes[i]).toLowerCase().replace(/[#:]/g, '').trim();
            const val = txt(nodes[i + 1]);
            if (!val) continue;
            if (label === 'city') out.city = val;
            else if (label === 'application deadline') out.deadline = val;
            else if (label === 'job reference') out.reference = val;
        }
    }

    if (!out.descHtml) {
        const el = document.querySelector('.jobdescription, [class*="jobDescription"], .jobDisplay');
        if (el) out.descHtml = el.innerHTML;
    }
    return out;
}"""
