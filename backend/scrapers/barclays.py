"""Barclays careers scraper (Phenom People).

search.jobs.barclays is a Phenom SPA that renders the result list client-side
and serves only a bot shell to plain HTTP, so we drive it with Playwright.
Results live in <li class="list-item--card"> nodes; each job URL is
    /job/<city>/<slug>/13015/<id>
so the city (used for the Europe filter) and a stable id come straight from the
link. We sweep early-careers keywords and paginate by CLICKING the pager.

TRAP (fixed 2026-08-23): do NOT paginate by building /search-jobs/<kw>/13015/<N>
or by appending ?p=N. Neither carries the keyword past page 1 — the path form
serves a DIFFERENT, unfiltered slice from page 2 on (pages 2 and 3 come back
byte-identical), and ?p=2 drops the search entirely and returns the whole
985-job board. The old path-based loop therefore mixed unrelated jobs into the
results and still never reached the tail of a keyword. The only correct pager is
the rendered `a.next` control, which the SPA wires to its own search XHR; it
gains a `disabled` class on the last page. Verified: "intern" = 20 results
(16 + 4 then disabled), "sales trading" = 78 across 5 pages.

A cookie banner can overlay the pager and eat the click, so accept it first.

TRAP (fixed 2026-09-09): the pager must be clicked from INSIDE the page, not
through a Playwright ElementHandle. Phenom re-renders the whole pager between
the `query_selector` and the `click`, so the handle detaches and the click
raises "Element is not attached to the DOM". That was caught and turned into
`return False` — i.e. a silent "last page", so a keyword sweep stopped after
page 1 with no error. `intern` therefore only ever yielded its first 16 cards,
and every "Sales, Trading and Structuring Off Cycle Internship Programme 2027"
seat (Frankfurt / Milan / Paris / Zurich) sat on page 2+ and was never seen.
`page.evaluate` re-queries `a.next` at click time, so it cannot go stale.
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse

from .base import (
    BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text, in_europe, is_snt_role,
)

logger = logging.getLogger(__name__)

SEARCH_TMPL = "https://search.jobs.barclays/search-jobs/{kw}"
MAX_PAGES = 12          # 16 cards/page — pager fallback only, "Show All" is the norm
SHOW_ALL_POLLS = 40     # 250ms each — "intern" expands 16 -> 71 cards in ~3s
SWEEP_CONCURRENCY = 3   # parallel keyword tabs
ENRICH_CAP = 32         # detail pages fetched per refresh (see _enrich_all)
ENRICH_CONCURRENCY = 3  # parallel detail tabs; keeps the sweep inside the 120s cap
COOKIE_SELS = (
    "#onetrust-accept-btn-handler",
    "button:has-text('Accept All')",
    "button:has-text('Accept all')",
)

# Phenom search is substring-based: "intern" covers internship/off-cycle intern,
# "placement" covers industrial placement, "graduate" covers early-careers — so a
# small, high-recall set keeps the (Playwright, serial) sweep fast.
#
# "off cycle" and "sales trading" are NOT redundant with "intern". Barclays names
# every off-cycle seat "<Division> Off Cycle Internship Programme <year> <city>",
# so they all match "intern" — but they land deep in that keyword's result set,
# behind the pager. Searching them directly puts the desk-relevant ones on page 1,
# so a single pager hiccup can never again cost us the whole S&T off-cycle cohort.
#
# Keywords must be NARROW enough to stay under Phenom's "Show All" threshold
# (~100 results). Above it the control disappears and the board becomes
# unreadable past page 1 (see _paginate), so a broad keyword silently returns a
# 16-row sample. Bare "spring" is the cautionary case: 106 results, of which the
# early-careers count is ZERO — it matches the Spring *framework* in engineering
# JDs ("Spring Boot"), so it cost a page load, could not paginate, and returned
# nothing but noise. "spring insight" is how Barclays actually names the
# programme and stays on one page.
QUERIES = [
    "intern", "graduate", "placement", "spring insight", "apprentice",
    "off cycle", "sales trading",
]

_JOBURL_RE = re.compile(r"/job/([^/]+)/[^/]+/\d+/(\d+)")


def _looks_eu(*parts: str) -> bool:
    """Europe test, delegated to the project-wide `in_europe`.

    This used to be a local hand-rolled substring list that was NARROWER than
    the canonical one — it had no Stockholm/Sweden, Berlin, Munich, Vienna,
    Barcelona or Rotterdam, so Nordic and DACH postings the rest of the app
    considers in-scope were dropped here, before the orchestrator ever saw
    them. Keep one source of truth.
    """
    return any(in_europe(p) for p in parts if p)


class BarclaysScraper(BankScraper):
    bank_name = "Barclays"
    careers_url = "https://search.jobs.barclays/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        cards_seen = 0

        # Keywords run over a small pool of tabs. Serially, seven keywords plus
        # enrichment took ~175s — past the orchestrator's 120s SCRAPER_TIMEOUT_S,
        # which counts as a scrape FAILURE, so Barclays would keep its stale
        # offers and persist nothing new.
        sem = asyncio.Semaphore(SWEEP_CONCURRENCY)
        results = await asyncio.gather(
            *[self._sweep_keyword(kw, sem) for kw in QUERIES], return_exceptions=True
        )

        for kw, res in zip(QUERIES, results):
            if isinstance(res, BaseException):
                logger.warning("Barclays kw=%r failed: %s", kw, res)
                continue
            kw_added = 0
            for c in res:
                cards_seen += 1
                m = _JOBURL_RE.search(c["href"])
                if not m:
                    continue
                city, jid = m.group(1).replace("-", " "), m.group(2)
                if jid in offers:
                    continue
                location = c.get("loc") or city
                if not _looks_eu(location, city):
                    continue
                kw_added += 1
                offers[jid] = ScrapedOffer(
                    bank="Barclays",
                    external_id=jid,
                    role_title=c["title"],
                    location=location,
                    apply_url=c["href"],
                    source_url=self.careers_url,
                    extras={"detail_url": c["href"]},
                )
            logger.info("Barclays kw=%r: %d cards, %d new EU offers", kw, len(res), kw_added)

        if cards_seen == 0:
            # Every keyword served zero job cards: Phenom always lists thousands
            # of jobs, so this is a bot wall or layout change, not an empty
            # board. Fail the scrape rather than "succeed" empty.
            raise RuntimeError("Barclays: no result cards on any page (bot wall / layout change?)")

        # Enrichment is Playwright navigation — cap it so the tail can't blow the
        # refresh budget (the S&T filter only needs a description for
        # generic-titled roles anyway). S&T-titled offers go FIRST: the cap used
        # to truncate an arbitrary dict order, so a desk role could lose its
        # description to a batch of corporate-banking grad schemes.
        eligible = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
        eligible.sort(key=lambda o: not is_snt_role(o.role_title, ""))
        to_enrich = eligible[:ENRICH_CAP]
        if len(eligible) > len(to_enrich):
            logger.warning("Barclays: enrich cap hit — %d early-careers offers left "
                           "without descriptions", len(eligible) - len(to_enrich))
        if to_enrich:
            await self._enrich_all(to_enrich)

        logger.info("Barclays: collected %d EU offers (%d enriched)", len(offers), len(to_enrich))
        return list(offers.values())

    async def _enrich_all(self, offers: list[ScrapedOffer]) -> None:
        """Fetch descriptions over a small pool of tabs.

        Serial navigation cost ~2s/posting, which pushed the whole sweep past
        the orchestrator's 120s SCRAPER_TIMEOUT_S once the keyword list grew
        (a timeout is a scrape FAILURE, so Barclays kept its stale offers and
        picked up nothing new). A handful of parallel tabs keeps it well inside.
        """
        queue: asyncio.Queue = asyncio.Queue()
        for o in offers:
            queue.put_nowait(o)

        async def worker() -> None:
            page = await self.new_page()
            try:
                while True:
                    try:
                        o = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    await self._enrich_one(page, o)
            finally:
                await page.close()

        n = min(ENRICH_CONCURRENCY, len(offers))
        await asyncio.gather(*[worker() for _ in range(n)], return_exceptions=True)

        # Phenom throttles a burst of simultaneous detail hits and answers some
        # with an empty shell. Those come back describable on a second, serial
        # pass — worth one retry, because an S&T posting with no description is
        # also a posting the tailoring step has nothing to work from.
        missed = [o for o in offers if not o.description]
        if missed:
            logger.info("Barclays: retrying %d empty description(s) serially", len(missed))
            page = await self.new_page()
            try:
                for o in missed:
                    await self._enrich_one(page, o)
            finally:
                await page.close()
            still = sum(1 for o in missed if not o.description)
            if still:
                logger.warning("Barclays: %d offer(s) still have no description", still)

    async def _sweep_keyword(self, kw: str, sem: asyncio.Semaphore) -> list[dict]:
        """All result cards for one keyword, in one pass.

        Uses the pager's "Show All" control, which re-runs the search with no
        page limit and injects every hit into the list — 71 cards for "intern"
        against the 16 a single page holds. That sidesteps the click-through
        pager (and its stale-handle trap) entirely; `_paginate` stays as the
        fallback for when the control is absent.
        """
        async with sem:
            # Fresh tab per keyword — Phenom soft-throttles rapid same-tab nav.
            page = await self.new_page()
            try:
                url = SEARCH_TMPL.format(kw=urllib.parse.quote(kw))
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                # The result list is injected after the search XHR resolves;
                # wait for the first job link instead of a fixed pause.
                try:
                    await page.wait_for_selector('a[href*="/job/"]', timeout=8_000)
                except Exception:
                    pass
                await page.wait_for_timeout(500)
                await self._accept_cookies(page)

                before = len(await self._read_cards(page))
                total = await self._result_count(page)
                if before and await self._show_all(page, before):
                    cards = await self._read_cards(page)
                else:
                    cards = await self._paginate(page)

                # Say so when the board held more than we could read. Phenom's
                # pager is the only way past page 1 without "Show All" and it no
                # longer responds to a click (real or synthetic) or to the
                # page-jump box, so a shortfall here is silent data loss — the
                # exact failure that hid the S&T off-cycle roles for weeks.
                if total and len(cards) < total:
                    logger.warning("Barclays kw=%r: read %d of %d results — narrow this "
                                   "keyword so 'Show All' stays available",
                                   kw, len(cards), total)
                return cards
            finally:
                await page.close()

    async def _result_count(self, page) -> int:
        """Total hits Phenom claims for this search, 0 if it does not say."""
        try:
            return await page.evaluate(
                """() => {
                    const m = document.body.innerText.match(/([0-9,]+)\\s+results?/i);
                    return m ? parseInt(m[1].replace(/,/g, ''), 10) : 0;
                }""") or 0
        except Exception:
            return 0

    async def _show_all(self, page, before: int) -> bool:
        """Click "Show All"; True once the list has actually grown (or is whole).

        A single-page result set keeps the control but has nothing more to load,
        so a count that never grows is only a failure when the pager says there
        ARE more pages — that check is what stops this from silently returning
        page 1 of 5.
        """
        try:
            # The pager renders AFTER the result list, and with several keyword
            # tabs open at once it can lag the first job link by a second or
            # more. Looking for "Show All" too early found nothing and silently
            # kept page 1 of 5, so wait for the nav to exist first.
            try:
                await page.wait_for_selector("nav.pagination", timeout=8_000)
            except Exception:
                pass

            total_pages = await page.evaluate(
                """() => {
                    const i = document.querySelector('input.pagination-current');
                    const n = i && parseInt(i.getAttribute('max'), 10);
                    return Number.isFinite(n) ? n : null;
                }""")
            if total_pages == 1:
                return True
            # Match on the CLASS first: innerText is layout-dependent and comes
            # back empty for the control in some renders, so a text-only lookup
            # reports "no Show All" on a board that has one.
            clicked = await page.evaluate(
                """() => {
                    const e = document.querySelector('a.pagination-show-all')
                          || [...document.querySelectorAll('a, button')]
                             .find(e => (e.textContent || '').trim().toLowerCase() === 'show all');
                    if (!e) return false;
                    e.click();
                    return true;
                }""")
            if not clicked:
                # Phenom drops the control above ~100 results; the caller's
                # shortfall check reports what that costs us.
                return False
            for _ in range(SHOW_ALL_POLLS):
                await page.wait_for_timeout(250)
                if len(await self._read_cards(page)) > before:
                    # The list grows in chunks; let it settle before reading.
                    last = -1
                    for _ in range(SHOW_ALL_POLLS):
                        await page.wait_for_timeout(250)
                        now = len(await self._read_cards(page))
                        if now == last:
                            return True
                        last = now
                    return True
            logger.warning("Barclays: 'Show All' did not expand the list (%d pages) "
                           "— falling back to the pager", total_pages or -1)
            return False
        except Exception as e:
            logger.warning("Barclays: 'Show All' failed (%s) — falling back to the pager", e)
            return False

    async def _paginate(self, page) -> list[dict]:
        """Fallback: walk the pager, accumulating cards across pages.

        Kept because it worked until recently, but as of 2026-09-09 Phenom's
        pager is inert on this board: `a.next` advances for neither a synthetic
        nor a real click, and the "Enter number to jump to a page" box plus its
        Go button do nothing either. So this usually returns page 1 alone —
        which is why the caller compares the haul against the result count and
        warns instead of trusting it.
        """
        out: list[dict] = []
        for _ in range(MAX_PAGES):
            cards = await self._read_cards(page)
            if not cards:
                break
            out.extend(cards)
            if not await self._goto_next(page):
                break
        return out

    async def _read_cards(self, page) -> list[dict]:
        # Class-agnostic: collect every job link except those in the
        # "recommended jobs" carousel; the city comes from the URL itself.
        return await page.evaluate(
            """() => [...document.querySelectorAll('a[href*="/job/"]')]
                .filter(a => !a.closest('.slick-track, .slick-slide, [class*="carousel"], [class*="recommend"]'))
                .map(a => {
                    const li = a.closest('li, [class*="list-item"], article');
                    const loc = li && li.querySelector('[class*="location"], [class*="Location"]');
                    return {
                        href: a.href,
                        title: (a.innerText || '').trim().split('\\n')[0],
                        loc: loc ? loc.innerText.trim() : '',
                    };
                })
                .filter(c => c.href && c.title)""",
        )

    async def _accept_cookies(self, page) -> None:
        """Dismiss the consent banner — it overlays the pager and eats clicks."""
        for sel in COOKIE_SELS:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click(timeout=2_500)
                    await page.wait_for_timeout(400)
                    return
            except Exception:
                continue

    async def _goto_next(self, page) -> bool:
        """Advance the pager. Returns False only on a genuine last page.

        The click happens inside the page (see the module docstring): an
        ElementHandle detaches when Phenom re-renders the pager, and the
        resulting error is indistinguishable from "no more pages".
        """
        try:
            first_before = await page.evaluate(
                """() => {const a = document.querySelector('a[href*="/job/"]');
                          return a ? a.getAttribute('href') : null;}""")
            state = await page.evaluate(
                """() => {
                    const a = document.querySelector('a.next');
                    if (!a) return 'none';
                    const flag = (a.className || '') + ' ' + (a.getAttribute('aria-disabled') || '');
                    if (flag.toLowerCase().includes('disabled')) return 'disabled';
                    a.click();
                    return 'clicked';
                }""")
            if state != "clicked":
                return False
            # The SPA swaps the list in place — wait for the first card to change
            # rather than for a navigation that never happens.
            for _ in range(25):
                await page.wait_for_timeout(200)
                first_now = await page.evaluate(
                    """() => {const a = document.querySelector('a[href*="/job/"]');
                              return a ? a.getAttribute('href') : null;}""")
                if first_now and first_now != first_before:
                    return True
            # Clicked a live, non-disabled pager but the list never changed:
            # that is a stall, not the end of the results. Say so — silently
            # treating it as the last page is exactly what hid the missing
            # off-cycle roles.
            logger.warning("Barclays: pager clicked but list did not change — "
                           "stopping this keyword early (results may be partial)")
            return False
        except Exception as e:
            logger.warning("Barclays: pagination failed: %s", e)
            return False

    async def _enrich_one(self, page, o: ScrapedOffer) -> None:
        try:
            await page.goto(o.extras["detail_url"], wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(400)
            # Phenom embeds a JobPosting JSON-LD blob server-side — much
            # cleaner than scraping rendered text (which drags in nav/footer).
            raw = await page.evaluate(
                """() => {
                    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                        try {
                            const j = JSON.parse(s.textContent);
                            for (const it of (Array.isArray(j) ? j : [j])) {
                                if (it && it['@type'] === 'JobPosting' && it.description) return it.description;
                            }
                        } catch (e) {}
                    }
                    const el = document.querySelector('.ats-description, [class*="job-description"], main');
                    return el ? el.innerHTML : '';
                }""")
            clean = html_to_text(raw or "")
            if clean:
                o.description = clean
        except Exception:
            return
