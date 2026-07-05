"""Run any single bank scraper and report filter pass-through."""
import asyncio
import sys
import importlib

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from scrapers.base import is_snt_role, is_internship_or_grad, in_europe


async def main(module: str, cls: str):
    mod = importlib.import_module(f"scrapers.{module}")
    scraper_cls = getattr(mod, cls)
    async with scraper_cls(headless=True) as s:
        raw = await s.scrape()
    print(f"\n=== {cls} returned {len(raw)} raw offers ===")
    progs = [o for o in raw if is_internship_or_grad(o.role_title, o.description or "")]
    snts = [o for o in raw if is_snt_role(o.role_title, o.description or "")]
    eus = [o for o in raw if in_europe(o.location or "")]
    kept = [o for o in raw if (is_internship_or_grad(o.role_title, o.description or "")
                                and is_snt_role(o.role_title, o.description or "")
                                and in_europe(o.location or ""))]
    print(f"  progs={len(progs)}  snt={len(snts)}  eu={len(eus)}  KEPT={len(kept)}")
    print(f"\n=== KEPT ({len(kept)}) ===")
    for o in kept:
        print(f"  [{o.location!r}] {o.role_title!r}")
    # Show programs that aren't S&T (in case the S&T filter is too strict)
    prog_not_snt = [o for o in progs if not is_snt_role(o.role_title, o.description or "") and in_europe(o.location or "")]
    print(f"\n=== EU PROGRAMS NOT classified as S&T ({len(prog_not_snt)}) — first 20 ===")
    for o in prog_not_snt[:20]:
        print(f"  [{o.location!r}] {o.role_title!r}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
