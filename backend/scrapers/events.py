"""Recruiting / networking EVENTS published by banks — a second feed next to
offers, shown on the Events page and included in the new-items email.

Each source is a public, unauthenticated listing (verified 2026-10-04):

  JPMorgan      www.jpmorganchase.com/services/json/v1/careers/gate/events.json
                (the JSON behind /careers/events). `signup_link` is None once
                sign-up closes, so its absence = registration closed.
  Goldman Sachs recruiting360.avature.net/candidates/SearchEvents ("My GS
                Events") — a plain HTML table, 50 rows/page, Region column.
                Detail pages need a login, so list-level data only; the event
                link IS the register flow (login -> register).
  Deutsche Bank project-ouroboros-p-pub.s3.amazonaws.com/db/feeds/events.xml
                (the feed behind careers.db.com/students-graduates/events).
                Holds internal rows too: only `shown_externally=true` with a
                signup_link is public.
  HSBC          portal.careers.hsbc.com/api/events/open/list (Eightfold).
                Epoch timestamps + an IANA timezone, which is also our
                Europe signal (UK events carry Europe/London).
  Bank of America  bankcampuscareers.tal.net boards 2 (Campus Events) and 4
                (Events, register on day) — Atom feeds, Region field.

Not covered (checked): Morgan Stanley's tal.net boards are not public feeds,
Citi/Barclays/UBS/BNP have no public event listing (Citi registers through
Workday postings; spring weeks / insight programmes that banks post AS jobs
already arrive through the offer scrapers as program_type spring_week).

Like offer scrapers, a source RAISES on failure (never returns [] after a
swallowed error), so a broken source can't deactivate that bank's events.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlmodel import Session, select

from db import ENGINE, RecruitingEvent
from .base import parse_location, html_to_text

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"}
SOURCE_TIMEOUT_S = 60


@dataclass
class ScrapedEvent:
    bank: str
    external_id: str
    title: str
    register_url: str
    source_url: str
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    all_day: bool = False
    timezone: Optional[str] = None
    registration_deadline: Optional[datetime] = None
    registration_open: bool = True
    location: Optional[str] = None
    country: Optional[str] = None
    is_virtual: bool = False
    region: Optional[str] = None      # source's own region label
    division: Optional[str] = None
    school: Optional[str] = None
    description: Optional[str] = None


# --- Classification ---------------------------------------------------------

_MARKETS_RE = re.compile(
    # "Capital Markets" is the IB side (DCM/ECM), not a trading floor.
    r"\b((?<!capital )markets?|sales|trading|trader|structuring|ficc|fixed income|equities|"
    r"equity derivatives|derivatives|currencies|fx|rates|commodities|quant\w*|"
    r"securities|prime (?:brokerage|financing)|s&t|marketmind)\b", re.I)
_OTHER_RE = re.compile(
    r"\b(tech|technology|technologists?|engineering|engineers?|data|ai|cyber|"
    r"codility|software|developers?|asset management|wealth|private bank\w*|"
    r"corporate functions|payments|operations|hr|human resources|audit|"
    r"compliance|legal|chief financial|cfo|finance and business management|"
    r"risk|consulting|investment bank\w*|m&a|corporate bank\w*|real estate|"
    r"retail|premier|relationship manager|treasury|merrill|financing services?|"
    r"auswahltag|apprentice\w*|school leavers?|interviews?|assessment centre)\b", re.I)

_TYPE_RULES = [
    (re.compile(r"\b(challenge|competition|case study|game)\b", re.I), "competition"),
    (re.compile(r"\b(workshop|skills?|tips|prepare|preparation|cv|masterclass|bootcamp|academy)\b", re.I), "workshop"),
    (re.compile(r"\b(networking|meet|evening|coffee|reception|drinks|breakfast|dinner|fair)\b", re.I), "networking"),
    (re.compile(r"\b(insights?|inside|explore|this is|discover|open (?:day|house)|pathways|spring|"
                r"experience|immersion)\b", re.I), "insight"),
    (re.compile(r"\b(information|info|overview|presentation|recruiting 101|webinar|power hour|"
                r"launch|q&a|panel|session|talk)\b", re.I), "info_session"),
]


def classify_relevance(title: str, division: str = "") -> str:
    """markets (an S&T desk / markets division), other (a clearly different
    division: tech, AM, IB, ops...), or general (firm-wide: open days,
    recruiting 101, city insight evenings) — the last two matter differently
    to an S&T candidate, so the UI defaults to markets + general."""
    hay = f"{title} {division}"
    if _MARKETS_RE.search(hay):
        return "markets"
    if _OTHER_RE.search(hay):
        return "other"
    return "general"


def classify_type(title: str) -> str:
    for rx, kind in _TYPE_RULES:
        if rx.search(title):
            return kind
    return "event"


_EUROPE_REGION_RE = re.compile(r"\b(emea|europe|uk|united kingdom)\b", re.I)


def is_european(e: ScrapedEvent) -> bool:
    for s in (e.country, e.location):
        if s and any(parse_location(s)):
            return True
    if e.timezone and e.timezone.startswith("Europe/"):
        return True
    return bool(e.region and _EUROPE_REGION_RE.search(e.region))


# --- Helpers ---------------------------------------------------------------

def _clean(s: Optional[str]) -> str:
    return " ".join(_html.unescape(s or "").split())


def _parse_dt(raw: str, fmts: tuple[str, ...]) -> Optional[datetime]:
    raw = _clean(raw)
    for f in fmts:
        try:
            return datetime.strptime(raw, f)
        except ValueError:
            continue
    return None


async def _get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    r = await client.get(url, **kw)
    r.raise_for_status()
    return r


# --- JPMorgan ---------------------------------------------------------------

JPM_FEED = "https://www.jpmorganchase.com/services/json/v1/careers/gate/events.json"
JPM_PAGE = "https://www.jpmorganchase.com/careers/events"


async def fetch_jpmorgan(client: httpx.AsyncClient) -> list[ScrapedEvent]:
    data = (await _get(client, JPM_FEED)).json()
    rows = data.get("events")
    if not isinstance(rows, list):
        raise ValueError("JPM events feed: no 'events' list")
    out = []
    for e in rows:
        if (e.get("showEvent") or "Yes") != "Yes":
            continue
        name = _clean(e.get("event_name"))
        d0, d1 = e.get("date_start") or "", e.get("date_end") or d0
        start = _parse_dt(f"{d0} {e.get('start_time') or ''}", ("%d/%m/%Y %I:%M %p", "%d/%m/%Y"))
        end = _parse_dt(f"{d1} {e.get('end_time') or ''}", ("%d/%m/%Y %I:%M %p", "%d/%m/%Y"))
        deadline = _parse_dt(e.get("signup_deadline") or "", ("%d/%m/%Y",))
        link = (e.get("signup_link") or "").strip()
        if link.lower() == "none":
            link = ""
        if link.startswith("http://"):
            link = "https://" + link[len("http://"):]
        where = _clean(e.get("location_name") or e.get("city"))
        city = _clean(e.get("city"))
        country = _clean(e.get("country"))
        virtual = where.lower() in ("online", "virtual") or not (city or where)
        out.append(ScrapedEvent(
            bank="JPMorgan",
            # name + start date: the feed has no id, and the same programme
            # recurs in several cities under distinct names.
            external_id=f"{name}|{d0}",
            title=name,
            register_url=link or JPM_PAGE,
            source_url=JPM_PAGE,
            starts_at=start, ends_at=end,
            all_day=not e.get("start_time"),
            registration_deadline=deadline.replace(hour=23, minute=59) if deadline else None,
            registration_open=bool(link),
            location=", ".join(x for x in (where if not virtual else "Virtual", country) if x),
            country=country,
            is_virtual=virtual,
            region=e.get("participating_region"),
            division=_clean(e.get("event_classification")),
            description=html_to_text(e.get("external_description") or "", limit=4000) or None,
        ))
    return out


# --- Goldman Sachs (Avature "My GS Events") -----------------------------------

GS_LIST = "https://recruiting360.avature.net/candidates/SearchEvents/?projectOffset={off}"
_GS_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_GS_TD_RE = re.compile(r"<td>(.*?)</td>", re.S)
_GS_LINK_RE = re.compile(r'href="([^"]*ProjectDetail\?projectId=(\d+))"')
_GS_TOTAL_RE = re.compile(r"Displaying\s+\d+-\d+\s+of\s+(\d+)")


def _strip_tags(s: str) -> str:
    return _clean(re.sub(r"<[^>]+>", " ", s))


async def fetch_goldman(client: httpx.AsyncClient) -> list[ScrapedEvent]:
    out: list[ScrapedEvent] = []
    total, off = None, 0
    while off < 1000:
        page = (await _get(client, GS_LIST.format(off=off))).text
        if total is None:
            m = _GS_TOTAL_RE.search(page)
            total = int(m.group(1)) if m else 0
        rows = 0
        for tr in _GS_ROW_RE.findall(page):
            tds = _GS_TD_RE.findall(tr)
            lm = _GS_LINK_RE.search(tds[0]) if len(tds) == 9 else None
            if not lm:
                continue
            rows += 1
            title, school, category, _aud, _grad, area, date_s, region, city = (
                _strip_tags(t) for t in tds)
            start = _parse_dt(date_s, ("%d-%b-%Y",))
            virtual = city.lower().startswith("virtual")
            out.append(ScrapedEvent(
                bank="Goldman Sachs",
                external_id=lm.group(2),
                title=title,
                register_url=lm.group(1),
                source_url="https://recruiting360.avature.net/candidates/SearchEvents",
                starts_at=start, all_day=True,
                location="Virtual" if virtual else city,
                is_virtual=virtual,
                region=region,
                # A firm-wide event lists EVERY area of the firm (FICC included),
                # which would make it read as a markets event.
                division=area if category.lower() == "divisional" else category,
                school=school or None,
            ))
        if rows == 0 and total:
            raise ValueError(f"Goldman events: page at offset {off} had no rows (layout change?)")
        off += 50
        if not total or off >= total:
            break
    return out


# --- Deutsche Bank ------------------------------------------------------------

DB_FEED = "https://project-ouroboros-p-pub.s3.amazonaws.com/db/feeds/events.xml"
DB_PAGE = "https://careers.db.com/students-graduates/events/"


async def fetch_deutschebank(client: httpx.AsyncClient) -> list[ScrapedEvent]:
    root = ET.fromstring((await _get(client, DB_FEED)).content)
    events = root.findall("event")
    if not events:
        raise ValueError("Deutsche Bank events feed: no <event> rows")
    out = []
    for e in events:
        t = lambda k: _clean(e.findtext(k))  # noqa: E731
        link = t("signup_link")
        if t("shown_externally") != "true" or not link:
            continue
        loc = t("location_location")
        virtual = bool(re.search(r"virtual|teams|zoom|online", loc, re.I))
        out.append(ScrapedEvent(
            bank="Deutsche Bank",
            external_id=t("event_id"),
            title=t("event_name"),
            register_url=link.replace("http://", "https://", 1),
            source_url=DB_PAGE,
            starts_at=_parse_dt(t("event_start_date_time"), ("%Y-%m-%d %H:%M:%S",)),
            ends_at=_parse_dt(t("event_end_date_time"), ("%Y-%m-%d %H:%M:%S",)),
            location=", ".join(x for x in (loc, t("country")) if x),
            country=t("country"),
            is_virtual=virtual,
            region=t("region"),
            division=t("division") or None,
            description=html_to_text(e.findtext("external_description") or "", limit=4000) or None,
        ))
    return out


# --- HSBC (Eightfold) ---------------------------------------------------------

HSBC_API = "https://portal.careers.hsbc.com/api/events/open/list?domain=hsbc.com&page={page}"


async def fetch_hsbc(client: httpx.AsyncClient) -> list[ScrapedEvent]:
    out: list[ScrapedEvent] = []
    page, total = 1, None
    while page <= 20:
        data = (await _get(client, HSBC_API.format(page=page))).json()
        rows = data.get("plannedEventList")
        if not isinstance(rows, list):
            raise ValueError("HSBC events API: no plannedEventList")
        total = data.get("filteredEventsCount") or 0
        for e in rows:
            if e.get("isCancelled") or not e.get("activeRegistrations", True):
                continue
            tzname = e.get("selectedTimezone") or "UTC"
            try:
                tz = ZoneInfo(tzname)
            except Exception:
                tz = ZoneInfo("UTC")

            def wall(ts):
                return (datetime.fromtimestamp(ts, tz).replace(tzinfo=None) if ts else None)
            virtual = e.get("eventLocationType") == "virtual_event"
            venue = _clean(e.get("completeVenue") or e.get("venue"))
            out.append(ScrapedEvent(
                bank="HSBC",
                external_id=e.get("plannedEventEncId") or _clean(e.get("name")),
                title=_clean(e.get("name")),
                register_url=e.get("eventLandingPage") or "https://portal.careers.hsbc.com/events/open?domain=hsbc.com",
                source_url="https://www.hsbc.com/careers/students-and-graduates/events",
                starts_at=wall(e.get("startTimestamp")),
                ends_at=wall(e.get("endTimestamp")),
                timezone=tzname,
                location="Virtual" if virtual else venue,
                is_virtual=virtual,
                region="UK" if " - UK - " in (e.get("name") or "") else None,
                school=_clean(e.get("institution")) or None,
                description=_clean(e.get("description")) or None,
            ))
        if len(out) >= total or not rows:
            break
        page += 1
    return out


# --- Bank of America (tal.net) --------------------------------------------------

BOFA_FEEDS = [
    "https://bankcampuscareers.tal.net/vx/mobile-0/candidate/jobboard/vacancy/2/feed",
    "https://bankcampuscareers.tal.net/vx/mobile-0/candidate/jobboard/vacancy/4/feed",
]
_ATOM = "{http://www.w3.org/2005/Atom}"
_BOFA_DT_RE = re.compile(r"(\d{1,2} \w{3} \d{4} \d{1,2}:\d{2} [AP]M)\s*(\w+)?")


def _bofa_fields(entry: ET.Element) -> dict[str, str]:
    content = entry.find(f"{_ATOM}content")
    raw = ET.tostring(content, encoding="unicode") if content is not None else ""
    text = _html.unescape(re.sub(r"<br\s*/?>", "\n", re.sub(r"<(?!br)[^>]+>", "", raw)))
    out = {}
    for line in text.split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


async def fetch_bofa(client: httpx.AsyncClient) -> list[ScrapedEvent]:
    out = []
    for feed in BOFA_FEEDS:
        root = ET.fromstring((await _get(client, feed)).content)
        if root.tag != f"{_ATOM}feed":
            raise ValueError(f"BofA events: {feed} is not an Atom feed")
        for entry in root.findall(f"{_ATOM}entry"):
            f = _bofa_fields(entry)
            link_el = entry.find(f"{_ATOM}link")
            url = link_el.get("href") if link_el is not None else ""
            m = _BOFA_DT_RE.search(f.get("event date", ""))
            start = _parse_dt(m.group(1), ("%d %b %Y %I:%M %p",)) if m else None
            dm = _BOFA_DT_RE.search(f.get("registration deadline", ""))
            venue = f.get("event address / venue details", "")
            out.append(ScrapedEvent(
                bank="Bank of America",
                external_id=url.split("/opp/")[-1].split("-")[0] if "/opp/" in url else url,
                title=_clean(entry.findtext(f"{_ATOM}title")),
                register_url=url,
                source_url="https://bankcampuscareers.tal.net/vx/mobile-0/candidate/jobboard/vacancy/2/",
                starts_at=start,
                timezone=(m.group(2) if m else None) or f.get("event timezone"),
                registration_deadline=_parse_dt(dm.group(1), ("%d %b %Y %I:%M %p",)) if dm else None,
                location=", ".join(x for x in (venue, f.get("country")) if x) or None,
                country=f.get("country"),
                is_virtual=bool(re.search(r"virtual|online|zoom|teams", venue, re.I)),
                region=f.get("region"),
                division=f.get("line of business"),
                school=f.get("school/university") or None,
                description=f.get("event description") or None,
            ))
    return out


EVENT_SOURCES: list[tuple[str, Callable]] = [
    ("JPMorgan", fetch_jpmorgan),
    ("Goldman Sachs", fetch_goldman),
    ("Deutsche Bank", fetch_deutschebank),
    ("HSBC", fetch_hsbc),
    ("Bank of America", fetch_bofa),
]


# --- Orchestration --------------------------------------------------------------

def keep_event(e: ScrapedEvent, now: Optional[datetime] = None) -> bool:
    """European and not over. Wall-clock dates, so compare on the DATE with a
    day of slack rather than pretend to know the zone."""
    if not e.title or not is_european(e):
        return False
    today = (now or datetime.utcnow()).date()
    last = e.ends_at or e.starts_at
    return last is None or last.date() >= today - timedelta(days=1)


async def _run_source(bank: str, fn: Callable) -> tuple[str, Optional[list[ScrapedEvent]], Optional[str]]:
    try:
        async with httpx.AsyncClient(timeout=40, headers=HEADERS, follow_redirects=True) as client:
            evs = await asyncio.wait_for(fn(client), timeout=SOURCE_TIMEOUT_S)
        logger.info("Events %s: %d raw", bank, len(evs))
        return bank, evs, None
    except Exception as e:
        logger.exception("Events source %s failed", bank)
        return bank, None, f"{type(e).__name__}: {' '.join(str(e).split())[:160]}"


def persist_events(bank: str, scraped: list[ScrapedEvent]) -> dict:
    """Upsert one bank's events. Events of a bank that scraped OK but are no
    longer listed are deactivated (they ended or were pulled); user fields
    (reg_status, notes, hidden) survive every refresh. Manual events are never
    touched."""
    now = datetime.utcnow()
    kept = [e for e in scraped if keep_event(e, now)]
    inserted = updated = removed = 0
    with Session(ENGINE) as s:
        existing = {r.external_id: r for r in s.exec(
            select(RecruitingEvent).where(RecruitingEvent.bank == bank,
                                          RecruitingEvent.manual == False)).all()}  # noqa: E712
        seen: set[str] = set()
        for e in kept:
            if e.external_id in seen:
                continue
            seen.add(e.external_id)
            city, country = parse_location(e.location or e.country or "")
            fields = dict(
                title=e.title,
                event_type=classify_type(e.title),
                relevance=classify_relevance(e.title, e.division or ""),
                division=e.division, is_virtual=e.is_virtual, location=e.location,
                city=city, country=country or e.country, school=e.school,
                starts_at=e.starts_at, ends_at=e.ends_at, all_day=e.all_day,
                timezone=e.timezone, registration_deadline=e.registration_deadline,
                registration_open=e.registration_open and not (
                    e.registration_deadline and e.registration_deadline < now - timedelta(hours=12)),
                register_url=e.register_url,
                source_url=e.source_url, description=e.description,
            )
            row = existing.get(e.external_id)
            if row:
                for k, v in fields.items():
                    setattr(row, k, v)
                row.last_seen_at = now
                row.is_active = True
                updated += 1
            else:
                row = RecruitingEvent(bank=bank, external_id=e.external_id,
                                      first_seen_at=now, last_seen_at=now, **fields)
                inserted += 1
            s.add(row)
        for ext_id, row in existing.items():
            if row.is_active and ext_id not in seen:
                row.is_active = False
                s.add(row)
                removed += 1
        s.commit()
    return {"inserted": inserted, "updated": updated, "removed": removed, "kept": len(seen)}


async def refresh_events() -> dict:
    results = await asyncio.gather(*[_run_source(b, fn) for b, fn in EVENT_SOURCES])
    agg = {"inserted": 0, "updated": 0, "removed": 0, "kept": 0,
           "scraped_banks": [], "failed_detail": []}
    for bank, evs, err in results:
        if evs is None:
            agg["failed_detail"].append({"bank": bank, "reason": err})
            continue
        try:
            res = await asyncio.to_thread(persist_events, bank, evs)
        except Exception as e:
            logger.exception("persist_events failed for %s", bank)
            agg["failed_detail"].append({"bank": bank, "reason": f"saving failed — {e}"})
            continue
        for k in ("inserted", "updated", "removed", "kept"):
            agg[k] += res[k]
        agg["scraped_banks"].append(bank)
    agg["failed"] = [d["bank"] for d in agg["failed_detail"]]
    return agg
