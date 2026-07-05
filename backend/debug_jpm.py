"""Run JPM scraper standalone and report where each offer dies in the filter."""
import asyncio
import sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from scrapers.jpmorgan import JPMorganScraper
from scrapers.base import is_snt_role, is_internship_or_grad, in_europe


async def main():
    async with JPMorganScraper(headless=True) as s:
        raw = await s.scrape()
    print(f"\n=== JPM returned {len(raw)} raw offers ===\n")
    stats = {"prog_only": 0, "snt_only": 0, "eu_only": 0, "kept": 0,
             "no_prog": 0, "no_snt": 0, "no_eu": 0}
    kept = []
    for o in raw:
        is_p = is_internship_or_grad(o.role_title, o.description or "")
        cat = is_snt_role(o.role_title, o.description or "")
        eu = in_europe(o.location or "")
        if is_p: stats["prog_only"] += 1
        else: stats["no_prog"] += 1
        if cat: stats["snt_only"] += 1
        else: stats["no_snt"] += 1
        if eu: stats["eu_only"] += 1
        else: stats["no_eu"] += 1
        if is_p and cat and eu:
            stats["kept"] += 1
            kept.append(o)
    print(f"Stats: {stats}\n")
    print(f"=== {len(kept)} KEPT ===")
    for o in kept[:20]:
        print(f"  [{o.location!r}] {o.role_title!r}")
    # Show what's in each bucket
    progs = [o for o in raw if is_internship_or_grad(o.role_title, o.description or "")]
    snts = [o for o in raw if is_snt_role(o.role_title, o.description or "")]
    print(f"\n=== {len(progs)} PROGRAMS (any category) ===")
    for o in progs:
        print(f"  [{o.location!r}] {o.role_title!r}")
    print(f"\n=== {len(snts)} S&T (any program) ===")
    for o in snts:
        print(f"  [{o.location!r}] {o.role_title!r}")
    # Show 10 dropped with reasons
    print(f"\n=== 10 sample drops ===")
    dropped = [o for o in raw if not (is_internship_or_grad(o.role_title, o.description or "")
                                       and is_snt_role(o.role_title, o.description or "")
                                       and in_europe(o.location or ""))]
    for o in dropped[:10]:
        why = []
        if not is_internship_or_grad(o.role_title, o.description or ""): why.append("no-prog")
        if not is_snt_role(o.role_title, o.description or ""): why.append("no-snt")
        if not in_europe(o.location or ""): why.append("no-eu")
        print(f"  [{','.join(why)}] loc={o.location!r}  title={o.role_title!r}")
        if o.description:
            print(f"     desc[:150]={o.description[:150]!r}")


asyncio.run(main())
