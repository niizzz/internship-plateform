"""Outreach: follow-up cadence, status transitions, and prompt-safety guards.

The prompt guards matter as much as the cadence here: a drafter that invents a
shared connection produces a message that gets the candidate caught lying, which
is worse than sending nothing. These assert the FACTS reaching the prompt, which
is the part we control (the model's output is not deterministic).

Run:  .venv/Scripts/python.exe -m tests.test_outreach   (from backend/)
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import SQLModel, Session, create_engine  # noqa: E402

import db as dbmod  # noqa: E402
from db import (  # noqa: E402
    AskType, Channel, ConnectionSuggestion, Contact, ContactType, MessageKind,
    OutreachMessage, OutreachStatus,
)
import outreach_api as oa  # noqa: E402
import outreach_llm as ol  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, got=None) -> None:
    global PASS, FAIL
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + ("" if cond else f"   got={got!r}"))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def mk_contact(s: Session, **kw) -> Contact:
    c = Contact(full_name=kw.pop("full_name", "A Person"),
                bank=kw.pop("bank", "TestBank"), **kw)
    s.add(c); s.commit(); s.refresh(c)
    return c


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp())
    eng = create_engine(f"sqlite:///{tmpdir / 't.db'}")
    SQLModel.metadata.create_all(eng)
    dbmod.ENGINE = eng

    now = datetime(2026, 9, 1, 12, 0, 0)

    # ---------------------------------------------------------- cadence
    with Session(eng) as s:
        c = mk_contact(s, status=OutreachStatus.sent)
        oa._schedule_next_followup(c, now)
        check("first follow-up lands 7 days out",
              c.next_followup_at == now + timedelta(days=7), c.next_followup_at)

        c.followup_count = 1
        oa._schedule_next_followup(c, now)
        check("second follow-up lands 14 days out",
              c.next_followup_at == now + timedelta(days=14), c.next_followup_at)

        # Two nudges is the whole cadence; a third is spam and costs the contact.
        c.followup_count = oa.MAX_FOLLOWUPS
        oa._schedule_next_followup(c, now)
        check("cadence stops after MAX_FOLLOWUPS", c.next_followup_at is None,
              c.next_followup_at)

        # Chasing someone who already answered is the fastest way to undo a reply.
        for st in (OutreachStatus.replied, OutreachStatus.call_booked,
                   OutreachStatus.referred, OutreachStatus.closed):
            c2 = mk_contact(s, status=st)
            c2.followup_count = 0
            oa._schedule_next_followup(c2, now)
            check(f"no follow-up scheduled once {st.value}",
                  c2.next_followup_at is None, c2.next_followup_at)

    # ------------------------------------------------- mark_sent transitions
    with Session(eng) as s:
        c = mk_contact(s, full_name="Sent Flow")
        m = OutreachMessage(contact_id=c.id, kind=MessageKind.connection_note, body="hi")
        s.add(m); s.commit(); s.refresh(m)
        oa.mark_sent(m.id, session=s)
        s.refresh(c); s.refresh(m)
        check("mark_sent moves to_contact -> sent", c.status == OutreachStatus.sent, c.status)
        check("mark_sent stamps the message", m.sent_at is not None)
        check("mark_sent stamps the contact", c.last_sent_at is not None)
        check("mark_sent schedules a nudge", c.next_followup_at is not None)
        check("a first message is not counted as a follow-up",
              c.followup_count == 0, c.followup_count)

        f = OutreachMessage(contact_id=c.id, kind=MessageKind.followup, body="nudge")
        s.add(f); s.commit(); s.refresh(f)
        oa.mark_sent(f.id, session=s)
        s.refresh(c)
        check("sending a follow-up increments the counter",
              c.followup_count == 1, c.followup_count)

    # A thank-you goes out AFTER they engaged: it must not drag the contact back
    # to "awaiting reply" or restart the nudge clock.
    with Session(eng) as s:
        c = mk_contact(s, full_name="Thanks Flow", status=OutreachStatus.replied)
        t = OutreachMessage(contact_id=c.id, kind=MessageKind.thank_you, body="thanks")
        s.add(t); s.commit(); s.refresh(t)
        oa.mark_sent(t.id, session=s)
        s.refresh(c)
        check("thank-you leaves the status alone", c.status == OutreachStatus.replied, c.status)
        check("thank-you does not reopen the nudge cadence",
              c.next_followup_at is None, c.next_followup_at)

    # ------------------------------------------------- prompt safety guards
    # Absent facts must be stated as absent. If a field silently vanished from
    # the prompt the model would be free to fill the gap with an invention.
    block = ol._contact_block({"full_name": "X", "bank": "Y"})
    check("missing school is marked as unknown, not omitted",
          "do not invent" in block.lower(), block)
    check("empty shared context is called out explicitly",
          "NONE GIVEN" in block, block)
    check("no-context block forbids manufacturing a tie",
          "do not manufacture" in block.lower(), block)

    with_ctx = ol._contact_block({
        "full_name": "X", "bank": "Y", "shared_context": "Same MSc, two years above",
    })
    check("real shared context is passed through verbatim",
          "Same MSc, two years above" in with_ctx, with_ctx)
    check("real shared context is not also flagged as missing",
          "NONE GIVEN" not in with_ctx, with_ctx)

    # A follow-up that recycles the opener which already failed is worse than none.
    thread = ol._thread_block([{"kind": "connection_note", "body": "First try",
                               "created_at": "2026-08-01T10:00:00"}])
    check("prior messages reach the prompt", "First try" in thread, thread)
    check("prior messages are marked do-not-repeat",
          "never repeat" in thread.lower(), thread)
    check("empty thread contributes nothing", ol._thread_block([]) == "")

    # ------------------------------------------------- output cleaning
    check("tagged output is unwrapped",
          ol.clean_outreach("<MSG>Hello there</MSG>") == "Hello there")
    check("preamble before an untagged draft is dropped",
          ol.clean_outreach("Here is the draft:\nHello there") == "Hello there",
          ol.clean_outreach("Here is the draft:\nHello there"))
    check("em dashes are softened (the guide bans them)",
          "—" not in ol.clean_outreach("<MSG>a — b</MSG>"))
    check("markdown lead-in is stripped",
          ol.clean_outreach("<MSG>## Hi there</MSG>") == "Hi there",
          ol.clean_outreach("<MSG>## Hi there</MSG>"))

    # Every relationship and format the API exposes must have a steer, or a
    # contact silently falls back to generic prompting.
    for t in ContactType:
        check(f"angle defined for {t.value}", t.value in ol.ANGLE)
    for k in MessageKind:
        check(f"format rules defined for {k.value}", k.value in ol.KIND_RULES)

    # The connection-note cap is LinkedIn's, so the two must not drift apart.
    check("connection-note cap agrees between modules",
          ol.CONNECTION_NOTE_MAX == dbmod.CONNECTION_NOTE_MAX == 300,
          (ol.CONNECTION_NOTE_MAX, dbmod.CONNECTION_NOTE_MAX))
    check("an over-limit note is left alone rather than truncated mid-sentence",
          ol.shorten_to_limit("x" * 50, limit=300) == "x" * 50)

    # ------------------------------------------------- channel + ask
    # A connection note is LinkedIn-only; over email it must fall back to the
    # first-email format rather than producing a 300-character stub.
    check("email falls back to the first-email format for a connection note",
          ol.format_rules("email", "connection_note") == ol.EMAIL_RULES["dm"])
    check("email followup uses the email rules",
          ol.format_rules("email", "followup") == ol.EMAIL_RULES["followup"])
    check("linkedin still uses the LinkedIn rules",
          ol.format_rules("linkedin", "connection_note") == ol.KIND_RULES["connection_note"])
    for a in AskType:
        check(f"ask block defined for {a.value}", a.value in ol.ASK)

    # A chat ask must not turn into a disguised application.
    chat = ol._ask_block("chat", None)
    check("chat ask forbids mentioning the CV", "do not mention your cv" in chat.lower(), chat)
    ref_named = ol._ask_block("referral", "Structured Products Sales 2027")
    check("referral ask carries the named role",
          "Structured Products Sales 2027" in ref_named, ref_named)
    ref_bare = ol._ask_block("referral", None)
    check("referral with no role names the desk instead of inventing a title",
          "inventing a job title" in ref_bare, ref_bare)
    check("referral ask keeps it proportionate to the relationship",
          "proportionate" in ref_bare.lower(), ref_bare)

    # The candidate's own credentials come from the CV, never from the tie the
    # user typed: a sloppy shared_context must not relocate them to another school.
    conflict = ol._contact_block({
        "full_name": "X", "bank": "Y", "shared_context": "Same MSc at Dauphine",
    })
    check("a supplied tie triggers the CV-wins conflict rule",
          "CONFLICT RULE" in conflict, conflict)
    check("conflict rule is absent when there is no tie to conflict with",
          "CONFLICT RULE" not in ol._contact_block({"full_name": "X", "bank": "Y"}))

    # ------------------------------------------------- send links
    with Session(eng) as s:
        c = mk_contact(s, full_name="Link Test", email="x@example.com",
                       linkedin_url="https://www.linkedin.com/in/x")
        em = OutreachMessage(contact_id=c.id, kind=MessageKind.dm, channel=Channel.email,
                             subject="Sub ject", body="line one & two")
        s.add(em); s.commit(); s.refresh(em)
        links = oa._send_links(em, c)
        check("mailto is built for an email message",
              (links.mailto or "").startswith("mailto:x%40example.com?"), links.mailto)
        check("gmail compose is built too", "mail.google.com" in (links.gmail or ""), links.gmail)
        check("spaces are percent-encoded, never '+' (a literal + would arrive as a space)",
              "+" not in (links.mailto or ""), links.mailto)
        check("the ampersand in the body is escaped, not treated as a param break",
              "%26" in (links.mailto or ""), links.mailto)
        check("linkedin profile link is offered",
              links.linkedin == "https://www.linkedin.com/in/x")

        li = OutreachMessage(contact_id=c.id, kind=MessageKind.connection_note,
                             channel=Channel.linkedin, body="hi")
        s.add(li); s.commit(); s.refresh(li)
        check("a LinkedIn message gets no mail links", oa._send_links(li, c).mailto is None)

        # No address on file must degrade quietly, not 500.
        c2 = mk_contact(s, full_name="No Email")
        em2 = OutreachMessage(contact_id=c2.id, kind=MessageKind.dm,
                              channel=Channel.email, subject="s", body="b")
        s.add(em2); s.commit(); s.refresh(em2)
        check("a contact without an address yields no mail links",
              oa._send_links(em2, c2).mailto is None)

        # The 300-char cap belongs to LinkedIn's connection note alone.
        long_email = OutreachMessage(contact_id=c.id, kind=MessageKind.dm,
                                     channel=Channel.email, subject="s", body="x" * 900)
        s.add(long_email); s.commit(); s.refresh(long_email)
        check("a long email is not flagged over-limit",
              oa._msg_out(long_email, c).over_limit is False)
        long_note = OutreachMessage(contact_id=c.id, kind=MessageKind.connection_note,
                                    channel=Channel.linkedin, body="x" * 900)
        s.add(long_note); s.commit(); s.refresh(long_note)
        check("a long connection note IS flagged over-limit",
              oa._msg_out(long_note, c).over_limit is True)


    # ------------------------------------------------- batch channel routing
    # "auto" is the right default for a mixed batch: the export gives an address
    # for some connections and not others.
    with Session(eng) as s:
        with_mail = mk_contact(s, full_name="Has Mail", email="a@b.com")
        no_mail = mk_contact(s, full_name="No Mail")
        check("auto picks email when there is an address",
              oa._resolve_channel("auto", with_mail) == Channel.email)
        check("auto falls back to linkedin with no address",
              oa._resolve_channel("auto", no_mail) == Channel.linkedin)
        # An address of spaces is not an address.
        blank = mk_contact(s, full_name="Blank Mail", email="   ")
        check("a whitespace address does not count as email",
              oa._resolve_channel("auto", blank) == Channel.linkedin)
        check("an explicit channel overrides auto",
              oa._resolve_channel("linkedin", with_mail) == Channel.linkedin
              and oa._resolve_channel("email", no_mail) == Channel.email)

    # The batch state must start clean, or a stale "running" blocks every later
    # batch behind a 409.
    check("no batch is running at rest", oa._batch_state["running"] is False)
    check("the batch lock is free at rest", oa._batch_lock.acquire(blocking=False))
    oa._batch_lock.release()


    # ------------------------------------------------- guesses never auto-send
    # The whole safety property of derived addresses: they are offered, never
    # used. A guess must not become the send channel or reach a compose URL
    # until the user has explicitly copied it into `email`.
    with Session(eng) as s:
        g = mk_contact(s, full_name="Guess Only",
                       email_guess="a.b@gs.com", email_guess_confidence="known")
        check("auto does NOT route to a guessed address",
              oa._resolve_channel("auto", g) == Channel.linkedin)
        em = OutreachMessage(contact_id=g.id, kind=MessageKind.dm,
                             channel=Channel.email, subject="s", body="b")
        s.add(em); s.commit(); s.refresh(em)
        links = oa._send_links(em, g)
        check("no mailto is built from a guess alone", links.mailto is None, links.mailto)
        check("no gmail link is built from a guess alone", links.gmail is None, links.gmail)

        # Once accepted into `email`, it behaves like any confirmed address.
        g.email = g.email_guess
        s.add(g); s.commit()
        check("accepting the guess enables the email channel",
              oa._resolve_channel("auto", g) == Channel.email)
        check("accepting the guess enables the compose links",
              oa._send_links(em, g).mailto is not None)


    # -------------------------------- connection notes need a Connect button
    # Everyone from the LinkedIn export is ALREADY 1st-degree. There is no
    # Connect button on their profile, so a connection note has nothing to
    # attach to and the user hits a dead end on LinkedIn. It must become a
    # normal message instead.
    with Session(eng) as s:
        conn_c = mk_contact(s, full_name="Already Connected", is_connection=True)
        stranger = mk_contact(s, full_name="Not Connected", is_connection=False)

        def coerce(kind: MessageKind, channel: Channel, c: Contact) -> MessageKind:
            """Mirrors the rule in draft_message / _batch_worker."""
            if kind == MessageKind.connection_note and (
                    channel == Channel.email or c.is_connection):
                return MessageKind.dm
            return kind

        check("a connection note to an existing connection becomes a message",
              coerce(MessageKind.connection_note, Channel.linkedin, conn_c) == MessageKind.dm)
        check("a connection note to a stranger stays a connection note",
              coerce(MessageKind.connection_note, Channel.linkedin, stranger)
              == MessageKind.connection_note)
        check("a connection note over email becomes a message either way",
              coerce(MessageKind.connection_note, Channel.email, stranger) == MessageKind.dm)
        check("other kinds are untouched",
              coerce(MessageKind.followup, Channel.linkedin, conn_c) == MessageKind.followup)

    # Promotion from a suggestion must mark the contact as a connection, or the
    # coercion above never fires for imported people.
    with Session(eng) as s:
        sug = ConnectionSuggestion(
            full_name="From Import", bank="Goldman Sachs", company_raw="Goldman Sachs",
            employer_tier="target_bank", position="Analyst, Equity Derivatives Sales",
            category="sales", seniority="junior", score=90, reasons=["x"],
            linkedin_url="https://www.linkedin.com/in/fi")
        s.add(sug); s.commit(); s.refresh(sug)
        promoted = oa.add_suggestion(sug.id, session=s)
        check("a promoted suggestion is marked as an existing connection",
              promoted.is_connection is True, promoted.is_connection)


    print(f"\nTOTAL: {PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
