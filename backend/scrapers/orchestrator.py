from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from db import (
    ENGINE, Offer, Notification, NotificationKind, Category, ProgramType,
    ScraperState,
)
from .base import (
    BankScraper, ScrapedOffer, is_snt_role, is_internship_or_grad,
    parse_location, in_europe, extract_start_date, extract_duration,
    normalize_start_date, parse_posted_date,
)

logger = logging.getLogger(__name__)

# Hard cap per scraper so one slow/hung bank (Playwright stuck on a bot wall,
# deep pagination, etc.) can't stall the whole refresh. Scrapers run
# concurrently and each bank's results are persisted the moment it finishes
# (see refresh_all), so a slow bank only delays ITS OWN offers, never the other
# banks'. A scraper cut off here is a failure (its existing offers are kept, not
# wiped). Most banks finish in <30s; Barclays/SocGen sit behind bot walls.
SCRAPER_TIMEOUT_S = 120


def _coerce_category(raw: Optional[str], fallback: str) -> Category:
    val = (raw or fallback or "markets").lower()
    try:
        return Category(val)
    except ValueError:
        return Category.markets


_PROGRAM_MATCHERS = [
    (re.compile(r"\bspring\s+(?:week|insight|programme|program)\b"), ProgramType.spring_week),
    (re.compile(r"\binsight\s+(?:day|week|programme|program)\b"), ProgramType.spring_week),
    (re.compile(r"\b(?:industrial\s+placement|year[\s-]in[\s-]industry|placement\s+(?:year|programme|program))\b"), ProgramType.industrial_placement),
    (re.compile(r"\boff[\s-]?cycle\b"), ProgramType.off_cycle),
    (re.compile(r"\b(?:graduate|analyst)\s+(?:programme|program|scheme)\b"), ProgramType.graduate),
    (re.compile(r"\bgraduate\b"), ProgramType.graduate),
    (re.compile(r"\bsummer\s+(?:analyst|associate)\b"), ProgramType.summer_internship),
    (re.compile(r"\bintern(?:ship)?\b"), ProgramType.summer_internship),
    (re.compile(r"\b(?:stage|stagiaire)\b"), ProgramType.summer_internship),
]


def _coerce_program(raw: Optional[str], title: str) -> ProgramType:
    haystack = f"{raw or ''} {title}".lower()
    for rx, value in _PROGRAM_MATCHERS:
        if rx.search(haystack):
            return value
    return ProgramType.other


async def _run_one(scraper: BankScraper) -> tuple[str, bool, list[ScrapedOffer]]:
    """Run one scraper. Returns (bank_name, succeeded, offers).

    `succeeded` distinguishes "scraped fine, 0 results" from "scraper errored":
    only successfully-scraped banks have their stale offers deactivated, so a
    transient failure never wipes a bank's listings.
    """
    try:
        async with scraper:
            offers = await asyncio.wait_for(scraper.scrape(), timeout=SCRAPER_TIMEOUT_S)
            logger.info("Scraper %s returned %d offers", scraper.bank_name, len(offers))
            return scraper.bank_name, True, offers
    except asyncio.TimeoutError:
        logger.error("Scraper %s timed out after %ss", scraper.bank_name, SCRAPER_TIMEOUT_S)
        return scraper.bank_name, False, []
    except Exception as e:
        logger.exception("Scraper %s failed: %s", scraper.bank_name, e)
        return scraper.bank_name, False, []


async def run_all_scrapers(scrapers: list[BankScraper]) -> tuple[list[ScrapedOffer], set[str]]:
    """Return (all raw offers, set of banks that scraped successfully)."""
    results = await asyncio.gather(*[_run_one(s) for s in scrapers])
    flat: list[ScrapedOffer] = []
    scraped_ok: set[str] = set()
    for bank, ok, offers in results:
        if ok:
            scraped_ok.add(bank)
        flat.extend(offers)
    return flat, scraped_ok


def _derive_dates(o: ScrapedOffer) -> None:
    """Fill start_date_raw/start_date_parsed/duration from whatever we have.

    Priority: a scraper-provided raw value (normalized), else the title, else
    anchored phrases in the description. Always re-derived on every refresh so
    stale junk (e.g. old posting dates stored as start dates) self-corrects.
    """
    title, desc = o.role_title or "", o.description or ""
    disp, parsed = (None, None)
    if o.start_date_raw:
        disp, parsed = normalize_start_date(o.start_date_raw)
    if not disp:
        disp, parsed = extract_start_date(title, desc)
    o.start_date_raw = disp
    o.start_date_parsed = parsed
    if not o.duration:
        o.duration = extract_duration(title, desc)


def _filter_and_enrich(raw: list[ScrapedOffer]) -> list[ScrapedOffer]:
    out: list[ScrapedOffer] = []
    for o in raw:
        title = o.role_title or ""
        desc = o.description or ""
        if not is_internship_or_grad(title, desc):
            continue
        category = o.category or is_snt_role(title, desc)
        if not category:
            continue
        if not in_europe(o.location or ""):
            continue
        city, country = parse_location(o.location or "")
        o.category = category
        o.city = city
        o.country = country or "Unknown"
        _derive_dates(o)
        out.append(o)
    return out


def persist(scraped: list[ScrapedOffer], scraped_banks: Optional[set[str]] = None) -> dict:
    """Upsert scraped offers; mark missing-from-scrape offers as inactive and notify.

    `scraped_banks` is the set of banks that scraped *successfully* this run. Stale
    offers are deactivated only for those banks — so a bank that scraped fine with
    0 keeps still gets cleaned, while a bank whose scraper errored is left intact.
    If omitted, falls back to the banks present in the (filtered) results.
    """
    scraped = _filter_and_enrich(scraped)
    now = datetime.utcnow()

    seen_keys: set[tuple[str, str]] = set()
    inserted = 0
    updated = 0

    with Session(ENGINE) as session:
        # Index ALL existing offers by (bank, external_id) — including inactive
        # ones, so a posting that reappears revives its row instead of creating
        # a duplicate next to the deactivated one.
        existing_all = {
            (o.bank, o.external_id): o
            for o in session.exec(select(Offer)).all()
        }

        for s in scraped:
            key = (s.bank, s.external_id)
            if key in seen_keys:
                # Same (bank, external_id) twice in one batch (a scraper-side
                # dedupe slip): first wins. Inserting both would create a ghost
                # row that the by-key index below can never update or deactivate.
                logger.warning("persist: duplicate key in batch, skipping %s", key)
                continue
            seen_keys.add(key)
            existing = existing_all.get(key)
            if existing:
                existing.role_title = s.role_title
                existing.location = s.location
                existing.country = s.country or existing.country
                existing.city = s.city or existing.city
                # Dates are re-derived deterministically each refresh — always
                # overwrite so previously stored junk self-corrects.
                existing.start_date_raw = s.start_date_raw
                existing.start_date_parsed = s.start_date_parsed
                existing.posted_at = parse_posted_date(s.posted_raw) or existing.posted_at
                existing.duration = s.duration or existing.duration
                existing.program_type = _coerce_program(s.program_type, s.role_title)
                existing.category = _coerce_category(s.category, "markets")
                existing.description = s.description or existing.description
                existing.apply_url = s.apply_url
                existing.source_url = s.source_url or existing.source_url
                existing.last_seen_at = now
                existing.is_active = True
                updated += 1
            else:
                offer = Offer(
                    bank=s.bank,
                    external_id=s.external_id,
                    role_title=s.role_title,
                    category=_coerce_category(s.category, "markets"),
                    location=s.location,
                    country=s.country or "Unknown",
                    city=s.city,
                    start_date_raw=s.start_date_raw,
                    start_date_parsed=s.start_date_parsed,
                    posted_at=parse_posted_date(s.posted_raw),
                    duration=s.duration,
                    program_type=_coerce_program(s.program_type, s.role_title),
                    description=s.description,
                    apply_url=s.apply_url,
                    source_url=s.source_url,
                    first_seen_at=now,
                    last_seen_at=now,
                    is_active=True,
                )
                session.add(offer)
                inserted += 1

        # Detect removed offers: active in DB but not in this scrape result,
        # AND the bank scraped successfully this run (otherwise a failed scraper
        # would wipe everything). Prefer the authoritative successfully-scraped
        # set; fall back to banks present in the filtered results.
        if scraped_banks is None:
            scraped_banks = {s.bank for s in scraped}

        # Zero-yield guard: a scrape that "succeeded" with ZERO kept offers while
        # the bank still has active rows is far more often a silently-broken
        # scraper (bot wall, layout change, swallowed request errors) than a bank
        # genuinely pulling every posting overnight. Require TWO consecutive
        # zero-yield scrapes before trusting the wipe. A real full-wipe is only
        # delayed by one refresh; a transient failure no longer destroys the
        # bank's listings (and, via notification dismissal, their history).
        kept_by_bank: dict[str, int] = {}
        for s in scraped:
            kept_by_bank[s.bank] = kept_by_bank.get(s.bank, 0) + 1
        active_by_bank: dict[str, int] = {}
        for (bank, _eid), offer in existing_all.items():
            if offer.is_active:
                active_by_bank[bank] = active_by_bank.get(bank, 0) + 1

        suspect_zero: list[str] = []
        trusted_banks: set[str] = set()
        for bank in scraped_banks:
            kept = kept_by_bank.get(bank, 0)
            state = session.get(ScraperState, bank)
            if state is None:
                state = ScraperState(bank=bank)
            if kept == 0 and active_by_bank.get(bank, 0) > 0:
                state.zero_streak += 1
                if state.zero_streak < 2:
                    suspect_zero.append(bank)
                    logger.warning(
                        "persist: %s returned 0 offers but has %d active — "
                        "holding deactivation (zero-streak %d/2)",
                        bank, active_by_bank.get(bank, 0), state.zero_streak)
                else:
                    trusted_banks.add(bank)
                    state.zero_streak = 0
            else:
                state.zero_streak = 0
                trusted_banks.add(bank)
            state.updated_at = now
            session.add(state)

        removed_offers: list[Offer] = []
        for (bank, _eid), offer in existing_all.items():
            if offer.is_active and bank in trusted_banks and (bank, offer.external_id) not in seen_keys:
                removed_offers.append(offer)

        for offer in removed_offers:
            # Snapshot for the notification, then deactivate.
            snapshot = {
                "bank": offer.bank,
                "role_title": offer.role_title,
                "location": offer.location,
                "apply_url": offer.apply_url,
                "last_seen_at": offer.last_seen_at.isoformat() if offer.last_seen_at else None,
            }
            session.add(Notification(
                kind=NotificationKind.offer_removed,
                offer_id=offer.id,
                offer_snapshot=snapshot,
            ))
            # Keep the row but mark inactive; we'll hard-delete on notification dismissal.
            offer.is_active = False

        session.commit()

    return {
        "inserted": inserted,
        "updated": updated,
        "removed": len(removed_offers) if 'removed_offers' in locals() else 0,
        "scraped_banks": sorted(scraped_banks),
        "suspect_zero": sorted(suspect_zero) if 'suspect_zero' in locals() else [],
        "total_kept_after_filter": len(scraped),
    }


async def refresh_all(scrapers: list[BankScraper], progress=None) -> dict:
    """Scrape all banks concurrently, persisting EACH bank's results the moment
    that bank finishes rather than waiting for the slowest one.

    New offers therefore land in the DB (and show up in the UI) within seconds of
    each scraper completing; a bank that is slow or times out — Barclays/SocGen
    behind bot walls — no longer holds back the other two dozen banks, which was
    why a refresh appeared to "take forever" after new banks were added.

    `progress`, if given, is called with a snapshot dict after every bank so the
    UI can show "X/Y banks done" and refresh the list live.
    """
    total = len(scrapers)
    agg = {
        "inserted": 0, "updated": 0, "removed": 0,
        "total_kept_after_filter": 0, "scraped_banks": [], "failed": [],
        "suspect_zero": [], "done": 0, "total": total,
    }

    def report():
        if progress:
            try:
                progress(dict(agg))
            except Exception:
                logger.debug("refresh progress callback error", exc_info=True)

    report()
    tasks = [asyncio.ensure_future(_run_one(s)) for s in scrapers]
    for fut in asyncio.as_completed(tasks):
        bank, ok, offers = await fut
        if ok:
            try:
                # persist() is synchronous (DB writes); run it off the event loop.
                # as_completed awaits one at a time, so no two persists race on
                # SQLite. Each call only touches THIS bank's rows (scraped_banks
                # = {bank}), so per-bank stale-offer cleanup still works.
                res = await asyncio.to_thread(persist, offers, {bank})
                agg["inserted"] += res["inserted"]
                agg["updated"] += res["updated"]
                agg["removed"] += res["removed"]
                agg["total_kept_after_filter"] += res["total_kept_after_filter"]
                agg["scraped_banks"].append(bank)
                agg["suspect_zero"].extend(res.get("suspect_zero") or [])
            except Exception as e:
                logger.exception("persist failed for %s: %s", bank, e)
                agg["failed"].append(bank)
        else:
            agg["failed"].append(bank)
        agg["done"] += 1
        report()

    agg["scraped_banks"].sort()
    agg["failed"].sort()
    return agg
