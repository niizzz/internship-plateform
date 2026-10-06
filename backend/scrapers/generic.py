"""Generic, config-driven scrapers for standardized ATS platforms.

These complement the bespoke per-bank scrapers (citi.py, cacib.py, …), which
stay exactly as they are. A new firm that runs on one of these common platforms
registers as a thin subclass that just sets a few class attributes — no need to
re-derive the whole request/parse flow each time.

Everything Europe/S&T/early-careers is still decided downstream by the shared
filter in base.py + the orchestrator; these scrapers only pull the raw postings
(broad, then a light Europe pre-filter to avoid enriching the whole planet).

Platforms covered here:
  * WorkdayScraper    — Workday CXS  (BBVA, Euronext, RBC, CMC Markets)
  * TalentsoftScraper — Talentsoft   (Amundi; same family as CACIB)
"""
from __future__ import annotations

import asyncio
import html as _htmllib
import json
import logging
import re

import httpx

from .base import BankScraper, ScrapedOffer, _PROGRAM_RE, html_to_text, in_europe

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Early-careers keywords, sent as Workday searchText so we pull the handful of
# student/graduate postings instead of the tenant's entire (often 500-2000) job
# base. Covers EN / FR / DE labels banks actually use in titles.
_EARLY_CAREERS_QUERIES = [
    "intern", "internship", "graduate", "summer analyst", "placement",
    "apprentice", "trainee", "off-cycle", "spring",
    "stage", "stagiaire", "alternance",
    "working student", "werkstudent", "praktikum",
]

_MULTI_LOC_RE = re.compile(r"\d+\s+locations", re.I)

# Workday uses GLOBAL country facet ids (shared across tenants), so the same ids
# work for Citi, RBC, … Use these to pull only a huge tenant's European subset
# server-side instead of paginating its whole global job base. Covers the main
# European S&T hubs (extend as needed).
WORKDAY_EUROPE_COUNTRY_IDS = [
    "29247e57dbaf46fb855b224e03170bc7",  # United Kingdom
    "54c5b6971ffb4bf0b116fe7651ec789a",  # France
    "04a05835925f45b3a59406a2a6b72c8a",  # Ireland
    "187134fccb084a0ea9b4b95f23890dbe",  # Switzerland
    "dcc5b7608d8644b3a93716604e78e995",  # Germany
    "9696868b09c64d52a62ee13b052383cc",  # Netherlands
    "328b82f597514643a7683a78fc67c3f1",  # Luxembourg
    "8cd04a563fd94da7b06857a79faaf815",  # Italy
    "fc078443155c4ad294201ecf5a61a499",  # Czechia
    "566388c1eb974c42bd9e3da4c2f57d60",  # Greece
    "6a800a4736884df5826858d435650f45",  # Sweden
    "131d5ac7e3ee4d7b962bdc96e498e412",  # Poland
    "9db257f5937e4421b2fac64eec6832f8",  # Hungary
]


class WorkdayScraper(BankScraper):
    """Workday CXS careers API.

    Subclass sets: ``wd_host`` (e.g. ``bbva.wd3.myworkdayjobs.com``),
    ``tenant`` (e.g. ``bbva``) and either ``site`` or ``sites`` (the career-site
    path(s) after ``/wday/cxs/<tenant>/``). ``careers_url`` is the human page.

    We search a small set of early-careers keywords rather than paginating the
    whole tenant, dedupe across keywords/sites, keep European + ambiguous
    multi-location hits, then fetch the detail endpoint only for the postings
    whose title looks like an internship/grad role — where we also resolve the
    real (possibly multi-) location and the description.
    """

    wd_host: str = ""
    tenant: str = ""
    site: str = ""
    sites: list[str] = []
    queries: list[str] = _EARLY_CAREERS_QUERIES
    # When set, pull the European subset server-side via a country facet instead
    # of keyword-searching the whole tenant — needed for huge global tenants (RBC).
    country_facet: str = ""
    europe_country_ids: list[str] = []
    page_size: int = 20   # Workday rate-limits aggressive bursts; keep pages small
    max_pages: int = 25

    def _site_list(self) -> list[str]:
        return self.sites or ([self.site] if self.site else [])

    async def scrape(self) -> list[ScrapedOffer]:
        headers = {
            "User-Agent": _UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": f"https://{self.wd_host}",
            "Referer": f"https://{self.wd_host}/",
        }
        facet_mode = bool(self.country_facet and self.europe_country_ids)
        offers: dict[str, ScrapedOffer] = {}
        sweeps_tried = sweeps_ok = 0
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            for site in self._site_list():
                if facet_mode:
                    sweeps_tried += 1
                    try:
                        await self._sweep_facet(client, site, offers)
                        sweeps_ok += 1
                    except Exception as e:
                        logger.warning("%s Workday %s facet sweep failed: %s", self.bank_name, site, e)
                    continue
                for kw in self.queries:
                    sweeps_tried += 1
                    try:
                        await self._search(client, site, kw, offers)
                        sweeps_ok += 1
                    except Exception as e:
                        logger.warning("%s Workday %s/%r failed: %s", self.bank_name, site, kw, e)
                    await asyncio.sleep(0.2)
            if sweeps_tried and sweeps_ok == 0:
                # Every request failed — endpoint down/changed/rate-limited.
                # Raise so the orchestrator records a FAILED scrape instead of
                # deactivating the bank's offers as "removed".
                raise RuntimeError(f"{self.bank_name}: all {sweeps_tried} Workday sweeps failed")

            to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
            await self._enrich(client, to_enrich)

        logger.info("%s: %d Europe early-careers candidates (%d enriched)",
                    self.bank_name, len(offers), len(to_enrich))
        return list(offers.values())

    async def _search(self, client: httpx.AsyncClient, site: str, kw: str,
                      offers: dict[str, ScrapedOffer]) -> None:
        api = f"https://{self.wd_host}/wday/cxs/{self.tenant}/{site}/jobs"
        offset, total, seen = 0, None, set()
        for _ in range(self.max_pages):
            body = {"appliedFacets": {}, "limit": self.page_size, "offset": offset, "searchText": kw}
            r = await client.post(api, json=body)
            r.raise_for_status()
            data = r.json()
            posts = data.get("jobPostings", []) or []
            # Past the end Workday wraps around to page 1 (and sends `total`
            # on page 1 only): stop at total or on a page with nothing new.
            if total is None:
                total = data.get("total") or 0
            paths = {p.get("externalPath") for p in posts}
            if not posts or paths <= seen:
                break
            seen |= paths
            for p in posts:
                path = p.get("externalPath") or ""
                ext = path.rsplit("_", 1)[-1] if "_" in path else path
                if not ext or ext in offers:
                    continue
                loc = p.get("locationsText") or ""
                # Keep European hits and ambiguous "N Locations" (resolved at
                # enrich); drop clearly non-European single locations to avoid
                # enriching the firm's US/APAC early-careers pipeline.
                if loc and not in_europe(loc) and not _MULTI_LOC_RE.search(loc):
                    continue
                offers[ext] = ScrapedOffer(
                    bank=self.bank_name,
                    external_id=ext,
                    role_title=p.get("title") or "",
                    location=loc,
                    apply_url=f"https://{self.wd_host}/{site}{path}",
                    source_url=self.careers_url,
                    posted_raw=p.get("postedOn"),  # relative; enrich overrides with startDate
                    extras={"external_path": path, "site": site},
                )
            offset += self.page_size
            if len(posts) < self.page_size or (total and offset >= total):
                break
            await asyncio.sleep(0.4)

    async def _sweep_facet(self, client: httpx.AsyncClient, site: str,
                           offers: dict[str, ScrapedOffer]) -> None:
        """Paginate one site's European subset via the country facet (no keyword
        search) — the whole result set is already Europe-bounded and small."""
        api = f"https://{self.wd_host}/wday/cxs/{self.tenant}/{site}/jobs"
        offset, total, seen = 0, None, set()
        for _ in range(self.max_pages):
            body = {
                "appliedFacets": {self.country_facet: self.europe_country_ids},
                "limit": self.page_size,
                "offset": offset,
                "searchText": "",
            }
            r = await client.post(api, json=body)
            r.raise_for_status()
            data = r.json()
            posts = data.get("jobPostings", []) or []
            # Past the end Workday wraps around to page 1 (and sends `total`
            # on page 1 only): stop at total or on a page with nothing new.
            if total is None:
                total = data.get("total") or 0
            paths = {p.get("externalPath") for p in posts}
            if not posts or paths <= seen:
                break
            seen |= paths
            for p in posts:
                path = p.get("externalPath") or ""
                ext = path.rsplit("_", 1)[-1] if "_" in path else path
                if not ext or ext in offers:
                    continue
                offers[ext] = ScrapedOffer(
                    bank=self.bank_name,
                    external_id=ext,
                    role_title=p.get("title") or "",
                    location=p.get("locationsText") or "",
                    apply_url=f"https://{self.wd_host}/{site}{path}",
                    source_url=self.careers_url,
                    posted_raw=p.get("postedOn"),  # relative; enrich overrides with startDate
                    extras={"external_path": path, "site": site},
                )
            offset += self.page_size
            if len(posts) < self.page_size or (total and offset >= total):
                break
            await asyncio.sleep(0.4)

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        sem = asyncio.Semaphore(3)

        async def one(o: ScrapedOffer) -> None:
            path = o.extras.get("external_path")
            site = o.extras.get("site")
            if not path:
                return
            async with sem:
                try:
                    r = await client.get(
                        f"https://{self.wd_host}/wday/cxs/{self.tenant}/{site}{path}")
                    if r.status_code != 200:
                        return
                    jp = r.json().get("jobPostingInfo", {})
                except Exception:
                    return
                await asyncio.sleep(0.2)
            # NB: Workday's jobPostingInfo.startDate is the POSTING date (absolute),
            # not the job start — use it for posted_at; the real start comes from
            # title/desc via the orchestrator.
            if jp.get("startDate"):
                o.posted_raw = jp.get("startDate")
            clean = html_to_text(jp.get("jobDescription") or "")
            if clean:
                o.description = clean
            # Resolve the real location (search's locationsText can be "2 Locations").
            loc = jp.get("location") or ""
            addl = [a for a in (jp.get("additionalLocations") or []) if a]
            all_locs = ([loc] if loc else []) + addl
            eu = next((l for l in all_locs if in_europe(l)), None)
            if eu:
                o.location = eu
            elif loc:
                o.location = loc

        await asyncio.gather(*(one(o) for o in offers))


# --- Talentsoft -------------------------------------------------------------

_TS_JOB_RE = re.compile(r"/job/(job-[a-z0-9-]+_(\d+))\.aspx", re.I)
# Two Talentsoft list markups seen in the wild: CACIB uses ts-offer-card,
# Amundi uses ts-offer-list-item. Match either title anchor.
_TS_TITLE_RE = re.compile(
    r'ts-offer-(?:card|list-item)__title-link[^>]*>(.*?)</a>', re.S | re.I)
_TS_ITEM_SPLIT_RE = re.compile(r'<li class="ts-offer-list-item|<div class="ts-offer-card', re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_JSONLD_RE = re.compile(r'<script type="application/ld\+json">\s*(.*?)</script>', re.S | re.I)

# Bare Talentsoft form labels that survive html_to_text as their own lines.
_TS_FORM_LABELS = {
    "vacancy details", "general information", "entity", "reference",
    "publication date", "update date", "job description", "position description",
    "profile", "candidate criteria",
}


def _ts_clean(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", _htmllib.unescape(s or ""))).strip()


class TalentsoftScraper(BankScraper):
    """Talentsoft careers (server-rendered .aspx list, same family as CACIB).

    Subclass sets ``base_url`` (portal root, e.g. ``https://jobs.amundi.com``)
    and ``lcid`` (Talentsoft language id; 2057=EN-intl default, 1036=FR, 1033=EN).
    The list page yields the job id + title; location + description come from the
    detail page (preferring its JobPosting JSON-LD).
    """

    base_url: str = ""
    lcid: str = "2057"
    max_pages: int = 12

    async def scrape(self) -> list[ScrapedOffer]:
        headers = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml"}
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
            for page in range(1, self.max_pages + 1):
                url = f"{self.base_url}/offre-de-emploi/liste-offres.aspx?LCID={self.lcid}&page={page}"
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                except Exception as e:
                    if page == 1:
                        raise RuntimeError(f"{self.bank_name}: Talentsoft page 1 failed: {e}") from e
                    logger.warning("%s Talentsoft list page %d failed (partial results kept): %s",
                                   self.bank_name, page, e)
                    break
                if not self._parse_list(r.text, offers):
                    break
                await asyncio.sleep(0.3)

            to_enrich = [o for o in offers.values() if _PROGRAM_RE.search(o.role_title.lower())]
            await self._enrich(client, to_enrich)

        logger.info("%s: %d offers (%d early-careers enriched)",
                    self.bank_name, len(offers), len(to_enrich))
        # Only offers we could locate in Europe survive the orchestrator filter;
        # keep them all here and let it decide.
        return list(offers.values())

    def _parse_list(self, htmltext: str, offers: dict[str, ScrapedOffer]) -> int:
        added = 0
        chunks = _TS_ITEM_SPLIT_RE.split(htmltext)[1:]
        for chunk in chunks:
            mj = _TS_JOB_RE.search(chunk)
            mt = _TS_TITLE_RE.search(chunk)
            if not mj or not mt:
                continue
            jid = mj.group(2)
            if jid in offers:
                continue
            title = _ts_clean(mt.group(1))
            if not title:
                continue
            # Inline location tokens (CACIB-style card) if present after the title.
            after = chunk[mt.end():]
            tokens = [t for t in (_ts_clean(x) for x in re.split(r"<[^>]+>", after)) if t and len(t) > 1]
            loc_tokens = [t for t in tokens if not re.match(
                r"^(internship|trainee|permanent|fixed[- ]term|temporary|vie|apprentice|"
                r"stage|cdi|cdd|alternance|graduate|contract|regular|full[- ]time)", t, re.I)][:2]
            location = ", ".join(dict.fromkeys(loc_tokens)) if loc_tokens else ""
            offers[jid] = ScrapedOffer(
                bank=self.bank_name,
                external_id=jid,
                role_title=title,
                location=location,
                apply_url=f"{self.base_url}/job/{mj.group(1)}.aspx",
                source_url=self.careers_url or self.base_url,
            )
            added += 1
        return added

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        sem = asyncio.Semaphore(5)

        async def one(o: ScrapedOffer) -> None:
            async with sem:
                try:
                    r = await client.get(o.apply_url)
                    if r.status_code != 200:
                        return
                except Exception:
                    return
                await asyncio.sleep(0.15)
            text = r.text
            # Prefer JobPosting JSON-LD (carries description + jobLocation).
            for m in _JSONLD_RE.finditer(text):
                try:
                    data = json.loads(m.group(1))
                except Exception:
                    continue
                if isinstance(data, list):
                    data = next((d for d in data if isinstance(d, dict)
                                 and d.get("@type") == "JobPosting"), None)
                if not isinstance(data, dict) or data.get("@type") != "JobPosting":
                    continue
                desc = html_to_text(_htmllib.unescape(data.get("description") or ""))
                if desc:
                    o.description = desc
                if data.get("datePosted"):
                    o.posted_raw = data.get("datePosted")
                loc = self._jsonld_location(data)
                if loc and not o.location:
                    o.location = loc
                break
            # Fallback for the publication date when the JSON-LD blob didn't
            # parse (malformed/escaped) — read the field straight out of the HTML.
            if not o.posted_raw:
                mp = re.search(r'"datePosted"\s*:\s*"([^"]+)"', text)
                if not mp:
                    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
                    mp = re.search(
                        r"(?:update date|publication date|date de (?:mise \S+ jour|publication))"
                        r"\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", flat, re.I)
                if mp:
                    o.posted_raw = mp.group(1)
            # Fallback description: the detail_offre container (never the whole
            # page — it's full of nav/search noise).
            if not o.description:
                m = re.search(
                    r'id="detail_offre"[^>]*>(.*?)<div class="ts-offer-page__cta',
                    text, re.S | re.I)
                if m:
                    body = html_to_text(m.group(1))
                    body = "\n".join(
                        ln for ln in body.split("\n")
                        if ln.strip().lower() not in _TS_FORM_LABELS)
                    o.description = re.sub(r"\n{3,}", "\n\n", body).strip() or o.description

        await asyncio.gather(*(one(o) for o in offers))

    @staticmethod
    def _jsonld_location(data: dict) -> str:
        jl = data.get("jobLocation")
        if isinstance(jl, list):
            jl = jl[0] if jl else None
        if isinstance(jl, dict):
            addr = jl.get("address") or {}
            if isinstance(addr, dict):
                parts = [addr.get("addressLocality"), addr.get("addressCountry")]
                return ", ".join(p for p in parts if p and isinstance(p, str))
        return ""


# --- Greenhouse -------------------------------------------------------------

class GreenhouseScraper(BankScraper):
    """Greenhouse public boards API. Subclass sets ``gh_token`` (the board
    token, e.g. ``mavensecuritiesholdingltd``). One request returns every
    posting with its full HTML content inline — no per-job fetch needed.
    Used by prop shops / market makers (Maven Securities, …).
    """

    gh_token: str = ""

    async def scrape(self) -> list[ScrapedOffer]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.gh_token}/jobs?content=true"
        headers = {"User-Agent": _UA, "Accept": "application/json"}
        offers: list[ScrapedOffer] = []
        async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            jobs = r.json().get("jobs", []) or []
        for job in jobs:
            loc = ((job.get("location") or {}).get("name") or "").strip()
            if not in_europe(loc):
                continue  # skip clearly non-European postings
            jid = str(job.get("id") or job.get("internal_job_id") or "")
            if not jid:
                continue
            offers.append(ScrapedOffer(
                bank=self.bank_name,
                external_id=jid,
                role_title=(job.get("title") or "").strip(),
                location=loc,
                apply_url=job.get("absolute_url") or self.careers_url,
                source_url=self.careers_url,
                description=html_to_text(_htmllib.unescape(job.get("content") or "")) or None,
            ))
        logger.info("%s: %d European postings", self.bank_name, len(offers))
        return offers
