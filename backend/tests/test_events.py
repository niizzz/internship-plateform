"""Events feed + new-item email digest.

Run:  .venv/Scripts/python.exe -m tests.test_events   (from backend/)
Plain-assert style. Throwaway SQLite file; no network, no SMTP.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import SQLModel, Session, create_engine, select  # noqa: E402

import db as dbmod  # noqa: E402
from db import Offer, RecruitingEvent, EventRegStatus, Settings  # noqa: E402
from scrapers import events as ev  # noqa: E402
import notifier  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def fresh_engine():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    eng = create_engine(f"sqlite:///{tmp.name}")
    SQLModel.metadata.create_all(eng)
    ev.ENGINE = notifier.ENGINE = dbmod.ENGINE = eng
    return eng


def se(ext: str, title: str = "Inside Markets - London", days: int = 10, **kw) -> ev.ScrapedEvent:
    base = dict(bank="JPMorgan", external_id=ext, title=title,
                register_url=f"https://x.example/{ext}", source_url="https://x.example",
                starts_at=datetime.utcnow() + timedelta(days=days),
                location="London, United Kingdom", country="United Kingdom")
    base.update(kw)
    return ev.ScrapedEvent(**base)


# --- classification ------------------------------------------------------------
R = ev.classify_relevance
check("FIC desk event is markets", R("This is Deutsche Bank – Fixed Income & Currencies") == "markets")
check("Inside Markets is markets", R("Inside Markets at JPMorganChase Copenhagen") == "markets")
check("trading challenge is markets", R("Quant Edge x Trading Challenge - Paris") == "markets")
check("capital markets is NOT markets", R("Investment Banking & Capital Markets evening") != "markets")
check("tech power hour is other", R("HSBC Early Careers - UK - Cyber Security at HSBC Power Hour") == "other")
check("asset management is other", R("Inside Asset Management - London") == "other")
check("city open evening is general", R("Explore Milan at JPMorganChase") == "general")
check("GS divisional FICC area is markets",
      R("Cornell | Overview Session", "Global Banking & Markets - FICC and Equities") == "markets")
check("type: challenge -> competition", ev.classify_type("Trading Challenge") == "competition")
check("type: networking evening", ev.classify_type("London Networking Event") == "networking")

# --- Europe / date gate ------------------------------------------------------------
check("London kept", ev.keep_event(se("a")))
check("Tokyo dropped", not ev.keep_event(se("b", location="Tokyo", country="Japan")))
check("virtual EMEA kept via region",
      ev.keep_event(se("c", location="Virtual", country=None, region="EMEA")))
check("Europe/London tz kept",
      ev.keep_event(se("d", location="Virtual", country=None, timezone="Europe/London")))
check("past event dropped", not ev.keep_event(se("e", days=-5)))
check("multi-week event still running kept",
      ev.keep_event(se("f", days=-5, ends_at=datetime.utcnow() + timedelta(days=20))))

# --- persist: upsert keeps user fields, deactivates vanished, skips manual ----------
eng = fresh_engine()
r1 = ev.persist_events("JPMorgan", [se("1"), se("2"), se("1"), se("tok", location="Tokyo", country="Japan")])
check("insert dedupes + filters", r1["inserted"] == 2)
with Session(eng) as s:
    row = s.exec(select(RecruitingEvent).where(RecruitingEvent.external_id == "1")).one()
    row.reg_status = EventRegStatus.registered
    row.notes = "bring CV"
    s.add(row)
    s.add(RecruitingEvent(bank="JPMorgan", external_id="manual-1", title="Fair", register_url="https://f", manual=True))
    s.commit()
r2 = ev.persist_events("JPMorgan", [se("1", title="Inside Markets - London (updated)")])
with Session(eng) as s:
    rows = {r.external_id: r for r in s.exec(select(RecruitingEvent)).all()}
check("update counted", r2["updated"] == 1 and r2["removed"] == 1)
check("user reg_status survives refresh", rows["1"].reg_status == EventRegStatus.registered)
check("user notes survive refresh", rows["1"].notes == "bring CV")
check("title updated", rows["1"].title.endswith("(updated)"))
check("vanished event deactivated", rows["2"].is_active is False)
check("manual event untouched", rows["manual-1"].is_active is True)
ev.persist_events("JPMorgan", [se("1"), se("late", registration_deadline=datetime.utcnow() - timedelta(days=2))])
with Session(eng) as s:
    late = s.exec(select(RecruitingEvent).where(RecruitingEvent.external_id == "late")).one()
check("passed deadline -> registration closed", late.registration_open is False)

# --- digest: only new, only since notify_since, only relevant+open events -------------
eng = fresh_engine()
sent: list = []
notifier._send = lambda st, subj, text, body: sent.append((st.notify_email, subj, text))
past = datetime.utcnow() - timedelta(days=3)
with Session(eng) as s:
    s.add(Settings(id=1, email_notifications=True, notify_email="me@example.edu",
                   smtp_user="u@example.com", smtp_password="x",
                   notify_since=datetime.utcnow() - timedelta(hours=1)))
    s.add(Offer(bank="UBS", external_id="old", role_title="Old S&T intern", location="London",
                country="United Kingdom", apply_url="https://o", first_seen_at=past))
    s.add(Offer(bank="UBS", external_id="new", role_title="New S&T intern", location="London",
                country="United Kingdom", apply_url="https://n"))
    s.commit()
ev.persist_events("JPMorgan", [se("m1"), se("tech", title="Inside JPMC: Your Future in Tech"),
                               se("shut", title="Inside Markets Paris", registration_open=False)])
res = notifier.send_new_items_digest()
check("digest sent", res.get("sent") is True and len(sent) == 1)
check("digest goes only to notify_email", sent and sent[0][0] == "me@example.edu")
check("backlog offer (before notify_since) not emailed", sent and "Old S&T intern" not in sent[0][2])
check("new offer emailed", sent and "New S&T intern" in sent[0][2])
check("markets event emailed", sent and "Inside Markets - London" in sent[0][2])
check("other-division event not emailed", sent and "Your Future in Tech" not in sent[0][2])
check("closed-registration event not emailed", sent and "Inside Markets Paris" not in sent[0][2])
res2 = notifier.send_new_items_digest()
check("nothing re-sent on next refresh", res2.get("sent") is False and len(sent) == 1)


def boom(*a):
    raise notifier.NotifyError("smtp down")


notifier._send = boom
with Session(eng) as s:
    s.add(Offer(bank="Citi", external_id="n2", role_title="Another", location="Paris",
                country="France", apply_url="https://a"))
    s.commit()
notifier.send_new_items_digest()
with Session(eng) as s:
    o = s.exec(select(Offer).where(Offer.external_id == "n2")).one()
check("failed send leaves item pending", o.emailed_at is None)
with Session(eng) as s:
    st = s.get(Settings, 1)
    st.email_notifications = False
    s.add(st)
    s.commit()
check("disabled -> no send", notifier.send_new_items_digest().get("reason") == "disabled")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
