"""Free-text search + "starts in <year>" filtering on /api/offers.

Run:  .venv/Scripts/python.exe -m tests.test_offer_search   (from backend/)

Regression guard for the search bar: it used to be a single LIKE over
role_title+location only, so a bare "2027" worked by accident (the year is in
some titles) while "summer 2027", "goldman" or "January 2027" all returned
nothing at all.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import SQLModel, Session, create_engine, select  # noqa: E402

import db as dbmod  # noqa: E402
from db import Offer, Category, ProgramType  # noqa: E402
import main as mainmod  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, got=None) -> None:
    global PASS, FAIL
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + ("" if cond else f"   got={got!r}"))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


# (external_id, bank, title, location, country, start_raw, start_parsed, program, description)
FIXTURES = [
    ("gs1", "Goldman Sachs",
     "2027 | EMEA | London | FICC & Equities (Sales & Trading) | Summer Analyst",
     "London", "United Kingdom", None, None, ProgramType.summer_internship,
     "Join our London markets division."),
    ("gs2", "Goldman Sachs",
     "2027 | EMEA | Paris | FICC & Equities (Sales & Trading) | Summer Analyst",
     "Paris", "France", None, None, ProgramType.summer_internship,
     "Rejoignez notre equipe."),
    ("ubs1", "UBS", "2027 Summer Internship - Global Markets - London",
     "London", "United Kingdom", "Summer 2027", date(2027, 6, 1),
     ProgramType.summer_internship, "Global Markets summer programme."),
    ("lfg1", "Lazard Freres Gestion", "Stage H/F - Analyste Fixed Income Janvier",
     "Paris", "France", "January 2027", date(2027, 1, 1),
     ProgramType.off_cycle, "Stage en gestion obligataire."),
    ("citi1", "Citi", "Markets - Sales, Trading & Structuring, Off-Cycle Internship, Milan",
     "Milan", "Italy", "October 2026", date(2026, 10, 1),
     ProgramType.off_cycle, "Graduation Year 2026/2027 required."),
    ("bnp1", "BNP Paribas", "London - Long Internship - ABS/CLO Trading",
     "London", "United Kingdom", None, None, ProgramType.off_cycle,
     "Securitisation desk."),
]


def seed(eng) -> None:
    with Session(eng) as s:
        for ext, bank, title, loc, ctry, raw, parsed, prog, desc in FIXTURES:
            s.add(Offer(bank=bank, external_id=ext, role_title=title,
                        category=Category.markets, location=loc, country=ctry,
                        city=loc, start_date_raw=raw, start_date_parsed=parsed,
                        program_type=prog, description=desc,
                        apply_url=f"https://example.test/{ext}"))
        s.commit()


def ids(rows) -> set[str]:
    return {r.role_title for r in rows}


def search(eng, **kwargs) -> list:
    """Call the real endpoint function with a live session."""
    with Session(eng) as s:
        return mainmod.list_offers(session=s, **kwargs)


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp())
    eng = create_engine(f"sqlite:///{tmpdir / 't.db'}")
    SQLModel.metadata.create_all(eng)
    mainmod.ENGINE = eng
    dbmod.ENGINE = eng
    seed(eng)

    def n(**kw) -> int:
        return len(search(eng, **kw))

    # --- the original bug: multi-word queries matched nothing --------------
    check("bare year still works", n(search="2027") == 5, n(search="2027"))
    check("'summer 2027' finds the summer 2027 roles", n(search="summer 2027") == 3,
          n(search="summer 2027"))
    check("'london 2027' ANDs city + year", n(search="london 2027") == 2,
          n(search="london 2027"))
    check("'2027 london' is order-insensitive", n(search="2027 london") == 2,
          n(search="2027 london"))
    check("token order/case ignored", n(search="LONDON 2027") == n(search="london 2027"))
    check("extra whitespace tolerated", n(search="  london   2027  ") == 2,
          n(search="  london   2027  "))

    # --- fields that were invisible to search -----------------------------
    check("bank name is searchable", n(search="goldman") == 2, n(search="goldman"))
    check("start date text is searchable", n(search="january 2027") == 1,
          n(search="january 2027"))
    check("country is searchable", n(search="italy") == 1, n(search="italy"))
    check("description is searchable", n(search="securitisation") == 1,
          n(search="securitisation"))
    check("nonsense still returns nothing", n(search="zzzz") == 0, n(search="zzzz"))

    # --- start-year filter -------------------------------------------------
    y27 = ids(search(eng, start_year=2027))
    check("start_year=2027 includes title-only years (GS)",
          all(f"2027 | EMEA | {c} | FICC & Equities (Sales & Trading) | Summer Analyst" in y27
              for c in ("London", "Paris")), y27)
    check("start_year=2027 count", len(y27) == 4, len(y27))
    check("start_year=2027 excludes the 2026 Citi role",
          not any("Milan" in t for t in y27), y27)
    check("start_year=2026 count", n(start_year=2026) == 1, n(start_year=2026))
    check("start_year ignores years only mentioned in a description",
          n(start_year=2027) == 4 and n(search="2027") == 5)
    check("unknown start year is excluded, not defaulted",
          not any("ABS/CLO" in t for t in y27), y27)
    check("start_year composes with search",
          n(search="london", start_year=2027) == 2, n(search="london", start_year=2027))
    check("start_year composes with bank", n(bank="UBS", start_year=2027) == 1,
          n(bank="UBS", start_year=2027))
    check("start_year with no matches returns empty", n(start_year=2099) == 0,
          n(start_year=2099))

    # --- the derived-year helper itself ------------------------------------
    with Session(eng) as s:
        by_ext = {o.external_id: o for o in s.exec(select(Offer)).all()}
    sy = mainmod._offer_start_year
    check("parsed date wins", sy(by_ext["ubs1"]) == 2027, sy(by_ext["ubs1"]))
    check("raw text used when unparsed", sy(by_ext["lfg1"]) == 2027, sy(by_ext["lfg1"]))
    check("title year used as last resort", sy(by_ext["gs1"]) == 2027, sy(by_ext["gs1"]))
    check("no year anywhere -> None", sy(by_ext["bnp1"]) is None, sy(by_ext["bnp1"]))
    check("description year is NOT trusted", sy(by_ext["citi1"]) == 2026,
          sy(by_ext["citi1"]))

    print(f"\nTOTAL: {PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
