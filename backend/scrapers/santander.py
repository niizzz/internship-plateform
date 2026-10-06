"""Santander careers scraper (Workday CXS).

Santander runs Workday at santander.wd3.myworkdayjobs.com (site
`SantanderCareers`) — one board (~1000 postings) covering the whole group,
Santander CIB/SCIB included, so there is no separate markets site to scrape.

Scoping the sweep to the "Early Careers" job family (what this scraper used to
do) MISSES S&T internships, because Santander files a posting under exactly one
`jobFamilyGroup` and its markets roles go to `Trading` / `Sales`, not to Early
Careers. We therefore sweep several slices and union them:

  * jobFamilyGroup = Early Careers / Trading / Sales
  * workerSubType  = Intern / Apprentice (catches an S&T intern filed under any
                     job family at all — the real safety net)
  * free-text searches for the S&T desk words

Facet ids are DISCOVERED by descriptor at run time: Workday facet ids rotate
when the tenant edits its taxonomy, and a stale hardcoded id silently returns
the unfiltered board instead of erroring.

Europe + S&T + internship filtering is the orchestrator's job; the local
location check is only a cheap prefilter to keep the per-posting description
fetches inside the scraper timeout.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .base import BankScraper, ScrapedOffer, html_to_text, in_europe

logger = logging.getLogger(__name__)

API = "https://santander.wd3.myworkdayjobs.com/wday/cxs/santander/SantanderCareers/jobs"
JOB_URL_TMPL = "https://santander.wd3.myworkdayjobs.com/SantanderCareers{external_path}"
DETAIL_URL_TMPL = "https://santander.wd3.myworkdayjobs.com/wday/cxs/santander/SantanderCareers{external_path}"

# Fallback if facet discovery fails (id observed 2026-08-25).
EARLY_CAREERS_FACET = "135a3ebce38101db91d599b919013150"

# Slices to sweep, by facet parameter -> descriptors (matched case-insensitively,
# on a prefix, so "Intern (Fixed Term)" is found by "intern").
FACET_SWEEPS: dict[str, tuple[str, ...]] = {
    "jobFamilyGroup": ("early careers", "trading", "sales"),
    "workerSubType": ("intern", "apprentice"),
}

# Free-text sweeps: catch an S&T posting filed under none of the slices above.
SEARCH_SWEEPS = ("global markets", "trading", "sales", "structuring")

PAGE_SIZE = 20
MAX_PAGES = 3          # per slice; slices are small, this only bounds runaway paging
MAX_ENRICH = 120       # per-posting detail fetches, to stay inside the 120s budget

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://santander.wd3.myworkdayjobs.com",
    "Referer": "https://santander.wd3.myworkdayjobs.com/SantanderCareers",
}


def _is_hidden_location(s: str) -> bool:
    """Workday collapses a multi-site posting to "4 Locations", hiding the cities.

    Those must survive the prefilter and be resolved from the detail payload —
    dropping them here is how a London markets internship posted alongside three
    other offices would go missing.
    """
    sl = (s or "").strip().lower()
    return not sl or ("location" in sl and any(ch.isdigit() for ch in sl))


def _keep_for_enrich(loc: str) -> bool:
    return in_europe(loc) or _is_hidden_location(loc)


class SantanderScraper(BankScraper):
    bank_name = "Santander"
    careers_url = "https://www.santandercareers.com/"

    async def scrape(self) -> list[ScrapedOffer]:
        offers: dict[str, ScrapedOffer] = {}
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            facet_ids = await self._discover_facets(client)

            sweeps: list[tuple[str, dict, str]] = []
            for param, wanted in FACET_SWEEPS.items():
                for name in wanted:
                    fid = facet_ids.get(param, {}).get(name)
                    if fid:
                        sweeps.append((f"{param}={name}", {param: [fid]}, ""))
                    else:
                        logger.warning("Santander: facet %s=%r not found on board", param, name)
            for term in SEARCH_SWEEPS:
                sweeps.append((f"search={term!r}", {}, term))

            if not sweeps:
                raise RuntimeError("Santander: no sweeps could be built")

            first = True
            for label, applied, search_text in sweeps:
                try:
                    await self._sweep(client, applied, search_text, offers)
                except Exception as e:
                    # Invariant 1: never swallow a total failure into an empty list.
                    if first and not offers:
                        raise RuntimeError(f"Santander: first sweep ({label}) failed: {e}") from e
                    logger.warning("Santander: sweep %s failed (partial results kept): %s", label, e)
                first = False

            await self._enrich(client, list(offers.values()))

        logger.info("Santander: collected %d candidate offers from %d sweeps", len(offers), len(sweeps))
        return list(offers.values())

    async def _discover_facets(self, client: httpx.AsyncClient) -> dict[str, dict[str, str]]:
        """Map facetParameter -> {lowercased descriptor: id} from an unfaceted call."""
        out: dict[str, dict[str, str]] = {}
        try:
            r = await client.post(API, json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning("Santander: facet discovery failed (%s); using fallback id", e)
            return {"jobFamilyGroup": {"early careers": EARLY_CAREERS_FACET}}

        for facet in data.get("facets") or []:
            param = facet.get("facetParameter")
            if not param:
                continue
            bucket = out.setdefault(param, {})
            for v in facet.get("values") or []:
                desc, vid = (v.get("descriptor") or "").strip().lower(), v.get("id")
                if not desc or not vid:
                    continue
                bucket[desc] = vid
                # "Intern (Fixed Term)" must be reachable as "intern".
                head = desc.split("(")[0].strip()
                bucket.setdefault(head, vid)
        return out

    async def _sweep(
        self,
        client: httpx.AsyncClient,
        applied: dict,
        search_text: str,
        offers: dict[str, ScrapedOffer],
    ) -> None:
        offset = 0
        for _ in range(MAX_PAGES):
            body = {
                "appliedFacets": applied,
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": search_text,
            }
            r = await client.post(API, json=body)
            r.raise_for_status()
            postings = r.json().get("jobPostings") or []
            if not postings:
                return
            for p in postings:
                loc = p.get("locationsText") or ""
                if not _keep_for_enrich(loc):
                    continue
                ext_path = p.get("externalPath") or ""
                # Req number: the id the ATS keeps stable across re-imports
                # (invariant 10). Unchanged from the pre-sweep scraper so
                # existing rows keep matching.
                ext_id = ext_path.rsplit("_", 1)[-1] if "_" in ext_path else ext_path
                if not ext_id or ext_id in offers:
                    continue
                offers[ext_id] = ScrapedOffer(
                    bank="Santander",
                    external_id=ext_id,
                    role_title=p.get("title") or "",
                    location=loc,
                    apply_url=JOB_URL_TMPL.format(external_path=ext_path),
                    source_url=self.careers_url,
                    posted_raw=p.get("postedOn"),
                    extras={"external_path": ext_path},
                )
            if len(postings) < PAGE_SIZE:
                return
            offset += PAGE_SIZE
            await asyncio.sleep(0.4)

    async def _enrich(self, client: httpx.AsyncClient, offers: list[ScrapedOffer]) -> None:
        if not offers:
            return
        if len(offers) > MAX_ENRICH:
            logger.warning("Santander: %d candidates, enriching first %d", len(offers), MAX_ENRICH)
            offers = offers[:MAX_ENRICH]
        sem = asyncio.Semaphore(4)

        async def one(o: ScrapedOffer):
            async with sem:
                try:
                    r = await client.get(DETAIL_URL_TMPL.format(external_path=o.extras["external_path"]))
                    if r.status_code != 200:
                        return
                    d = r.json()
                except Exception:
                    return
                await asyncio.sleep(0.15)

            jp = d.get("jobPostingInfo") or {}
            clean = html_to_text(jp.get("jobDescription") or "")
            if clean:
                o.description = clean
            if jp.get("startDate"):
                o.start_date_raw = jp["startDate"]
            if jp.get("postedOn") and not o.posted_raw:
                o.posted_raw = jp["postedOn"]

            # Resolve the real cities behind "4 Locations", and append the
            # country so the Europe check has something to match on for a
            # suburb the city list does not know.
            parts = [jp.get("location") or ""]
            parts += list(jp.get("additionalLocations") or [])
            country = (jp.get("country") or {}).get("descriptor") or ""
            resolved = ", ".join([p for p in parts if p])
            if resolved:
                o.location = f"{resolved}, {country}" if country else resolved
            elif country and _is_hidden_location(o.location):
                o.location = country

        await asyncio.gather(*(one(o) for o in offers))
