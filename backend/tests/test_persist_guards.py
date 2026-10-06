"""Persist-layer guards: mass-deactivation protection + within-batch dedupe.

Run:  .venv/Scripts/python.exe -m tests.test_persist_guards   (from backend/)
Plain-assert style, same as test_filter.py. Uses a throwaway SQLite file.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import SQLModel, Session, create_engine, select  # noqa: E402

import db as dbmod  # noqa: E402
from db import Offer  # noqa: E402
from scrapers import orchestrator as orch  # noqa: E402
from scrapers.base import ScrapedOffer  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def so(bank: str, ext: str, title: str = "Sales and Trading Summer Internship") -> ScrapedOffer:
    return ScrapedOffer(
        bank=bank, external_id=ext, role_title=title,
        location="London, United Kingdom",
        apply_url=f"https://x.example/{ext}",
        description="Global Markets sales and trading desk internship.",
    )


def fresh_engine():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    eng = create_engine(f"sqlite:///{tmp.name}")
    SQLModel.metadata.create_all(eng)
    return eng


def active_ids(eng, bank: str) -> set[str]:
    with Session(eng) as s:
        return {o.external_id for o in s.exec(
            select(Offer).where(Offer.bank == bank, Offer.is_active == True)).all()}  # noqa: E712


def main() -> int:
    eng = fresh_engine()
    orch.ENGINE = eng
    dbmod.ENGINE = eng

    # Seed: 3 active offers for bank X.
    orch.persist([so("X", "1"), so("X", "2"), so("X", "3")], {"X"})
    check("seed: 3 active", active_ids(eng, "X") == {"1", "2", "3"})

    # A successful-but-empty scrape must NOT wipe a bank on the first zero.
    res = orch.persist([], {"X"})
    check("first zero-yield does not deactivate", active_ids(eng, "X") == {"1", "2", "3"})

    # The hold must also explain itself: the UI health badge shows this reason
    # on hover, so a held bank is diagnosable without opening the backend log.
    det = res.get("suspect_detail") or []
    check("zero-yield hold reports a reason",
          len(det) == 1 and det[0]["bank"] == "X"
          and "0 offers" in det[0]["reason"] and "1/2" in det[0]["reason"])

    # A second consecutive zero-yield is trusted (bank really has nothing).
    orch.persist([], {"X"})
    check("second consecutive zero-yield deactivates", active_ids(eng, "X") == set())

    # Reset with fresh rows; a partial scrape (2 of 3) still removes the third.
    orch.persist([so("X", "1"), so("X", "2"), so("X", "3")], {"X"})
    orch.persist([so("X", "1"), so("X", "2")], {"X"})
    check("partial scrape still deactivates the missing one",
          active_ids(eng, "X") == {"1", "2"})

    # A non-zero scrape resets the zero-streak.
    orch.persist([], {"X"})
    check("zero after non-zero: guarded again", active_ids(eng, "X") == {"1", "2"})
    orch.persist([so("X", "1")], {"X"})   # non-zero resets streak, removes "2"
    orch.persist([], {"X"})
    check("streak reset by non-zero scrape", active_ids(eng, "X") == {"1"})

    # Within-batch duplicate (bank, external_id) must produce exactly ONE row.
    eng2 = fresh_engine()
    orch.ENGINE = eng2
    dbmod.ENGINE = eng2
    orch.persist([so("Y", "7", "Sales and Trading Summer Internship"),
                  so("Y", "7", "Sales and Trading Off-Cycle Internship")], {"Y"})
    with Session(eng2) as s:
        rows = s.exec(select(Offer).where(Offer.bank == "Y")).all()
    check("within-batch duplicate key inserts one row", len(rows) == 1)

    print(f"\nTOTAL: {PASS}/{PASS + FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
