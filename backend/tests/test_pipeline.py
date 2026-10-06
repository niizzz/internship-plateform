"""Pipeline (application tracker): /api/pipeline + status-transition events.

Run:  .venv/Scripts/python.exe -m tests.test_pipeline   (from backend/)
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import SQLModel, Session, create_engine, select  # noqa: E402

import db as dbmod  # noqa: E402
from db import (  # noqa: E402
    Offer, Application, ApplicationStatus, ApplicationEvent, Category,
)
import main as mainmod  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, got=None) -> None:
    global PASS, FAIL
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + ("" if cond else f"   got={got!r}"))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def mk_offer(s: Session, ext: str, title: str, active: bool = True) -> int:
    o = Offer(bank="TestBank", external_id=ext, role_title=title,
              category=Category.markets, location="London", country="United Kingdom",
              is_active=active, apply_url=f"https://x.test/{ext}")
    s.add(o); s.commit(); s.refresh(o)
    return o.id


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp())
    eng = create_engine(f"sqlite:///{tmpdir / 't.db'}")
    SQLModel.metadata.create_all(eng)
    mainmod.ENGINE = eng
    dbmod.ENGINE = eng

    with Session(eng) as s:
        applied = mk_offer(s, "a", "Applied role")
        interview = mk_offer(s, "b", "Interview role")
        untouched = mk_offer(s, "c", "Never applied")
        closed = mk_offer(s, "d", "Closed posting", active=False)
        long_ago = datetime.utcnow() - timedelta(days=30)
        s.add(Application(offer_id=applied, status=ApplicationStatus.applied,
                          applied_at=long_ago, updated_at=long_ago))
        s.add(Application(offer_id=interview, status=ApplicationStatus.interview,
                          applied_at=long_ago, updated_at=long_ago))
        s.add(Application(offer_id=untouched, status=ApplicationStatus.not_applied))
        s.add(Application(offer_id=closed, status=ApplicationStatus.applied,
                          applied_at=long_ago, updated_at=long_ago))
        s.commit()

    def pipe(**kw):
        with Session(eng) as s:
            return mainmod.pipeline(session=s, **kw)

    # --- what belongs on the page -----------------------------------------
    p = pipe()
    titles = {r.role_title for r in p.rows}
    check("not-applied offers are excluded", "Never applied" not in titles, titles)
    check("applied offers are included", "Applied role" in titles, titles)
    check("a CLOSED posting you applied to still shows",
          "Closed posting" in titles, titles)
    check("closed posting is flagged is_active=False",
          all(not r.is_active for r in p.rows if r.role_title == "Closed posting"))
    check("total counts only sent applications", p.total == 3, p.total)
    check("by_status tallies stages", p.by_status == {"applied": 2, "interview": 1}, p.by_status)
    check("include_not_applied opens it up", pipe(include_not_applied=True).total == 4,
          pipe(include_not_applied=True).total)
    check("status filter narrows", pipe(status=ApplicationStatus.interview).total == 1,
          pipe(status=ApplicationStatus.interview).total)

    # --- ageing ------------------------------------------------------------
    row = next(r for r in p.rows if r.role_title == "Applied role")
    check("days_since_applied computed", row.days_since_applied == 30, row.days_since_applied)
    check("stalest_days reports the oldest live stage", p.stalest_days == 30, p.stalest_days)

    # --- transitions create events ----------------------------------------
    with Session(eng) as s:
        mainmod.update_application(
            applied, mainmod.ApplicationUpdate(status=ApplicationStatus.online_assessment), s)
    with Session(eng) as s:
        evs = s.exec(select(ApplicationEvent).where(ApplicationEvent.offer_id == applied)).all()
    check("a status change records one event", len(evs) == 1, len(evs))
    check("event captures both ends",
          evs[0].from_status == ApplicationStatus.applied
          and evs[0].to_status == ApplicationStatus.online_assessment,
          (evs[0].from_status, evs[0].to_status))

    # Re-selecting the SAME status must not pad the timeline.
    with Session(eng) as s:
        mainmod.update_application(
            applied, mainmod.ApplicationUpdate(status=ApplicationStatus.online_assessment), s)
    with Session(eng) as s:
        evs = s.exec(select(ApplicationEvent).where(ApplicationEvent.offer_id == applied)).all()
    check("re-clicking the current stage adds no event", len(evs) == 1, len(evs))

    # A notes edit is not a transition.
    with Session(eng) as s:
        mainmod.update_application(applied, mainmod.ApplicationUpdate(notes="call thu"), s)
    with Session(eng) as s:
        evs = s.exec(select(ApplicationEvent).where(ApplicationEvent.offer_id == applied)).all()
    check("editing notes adds no event", len(evs) == 1, len(evs))

    # --- stage clock uses the transition, not updated_at -------------------
    row = next(r for r in pipe().rows if r.role_title == "Applied role")
    check("notes survive on the row", row.notes == "call thu", row.notes)
    check("days_in_stage resets on the move, not on the notes edit",
          row.days_in_stage == 0, row.days_in_stage)
    check("days_since_applied is unaffected by the move",
          row.days_since_applied == 30, row.days_since_applied)
    check("timeline is exposed to the UI", len(row.events) == 1, len(row.events))

    # --- rejection is finished, not stale ----------------------------------
    with Session(eng) as s:
        mainmod.update_application(
            interview, mainmod.ApplicationUpdate(status=ApplicationStatus.rejected), s)
    p = pipe()
    check("rejected rows drop out of stalest_days",
          p.stalest_days == 30, p.stalest_days)
    check("rejected still listed", p.by_status.get("rejected") == 1, p.by_status)

    # --- reverting to not_applied ------------------------------------------
    with Session(eng) as s:
        mainmod.update_application(
            applied, mainmod.ApplicationUpdate(status=ApplicationStatus.not_applied), s)
    p = pipe()
    check("reverting to not-applied removes it from the pipeline",
          "Applied role" not in {r.role_title for r in p.rows}, p.by_status)
    with Session(eng) as s:
        a = s.exec(select(Application).where(Application.offer_id == applied)).first()
    check("reverting clears applied_at", a.applied_at is None, a.applied_at)

    # --- backfill ----------------------------------------------------------
    eng2 = create_engine(f"sqlite:///{tmpdir / 't2.db'}")
    SQLModel.metadata.create_all(eng2)
    with Session(eng2) as s:
        oid = mk_offer(s, "z", "Legacy application")
        s.add(Application(offer_id=oid, status=ApplicationStatus.interview,
                          applied_at=long_ago, updated_at=datetime.utcnow()))
        s.commit()
    with eng2.begin() as conn:
        dbmod._backfill_application_events(conn)
        dbmod._backfill_application_events(conn)  # idempotent
    with Session(eng2) as s:
        evs = s.exec(select(ApplicationEvent)).all()
    check("legacy application gets exactly one seeded event", len(evs) == 1, len(evs))
    check("seeded event lands on the current status",
          evs and evs[0].to_status == ApplicationStatus.interview)
    check("seeded event is dated from applied_at, not updated_at",
          evs and abs((evs[0].at - long_ago).total_seconds()) < 2, evs[0].at if evs else None)

    print(f"\nTOTAL: {PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
