"""Outreach API: contacts, drafted messages, follow-up scheduling.

Mounted by main.py as an APIRouter rather than added to it inline, to keep the
outreach surface separable from offers/tailoring.

Scope note: this module never contacts LinkedIn. It stores contacts the user
enters, drafts messages for them, and tracks what was sent and when. Marking a
message "sent" is a user action recording something they did by hand.
"""
from __future__ import annotations

import json
import logging
import threading as _threading
from urllib.parse import quote, urlencode
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from cv_render import flatten_cv_text
from db import (
    ENGINE, AskType, Channel, ConnectionSuggestion, Contact, ContactType,
    MessageKind, Offer, OutreachMessage, OutreachStatus, get_session,
)
from llm import ClaudeCliError, is_usage_limit_error
from linkedin_import import analyse, parse_connections_csv, search_links
from outreach_llm import (
    CONNECTION_NOTE_MAX, draft_outreach, humanize_outreach, shorten_to_limit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/outreach", tags=["outreach"])

BASE_CV_JSON = Path(__file__).parent / "storage" / "cvs" / "base_cv.json"

# Days after a send before the next nudge is due. Two follow-ups, then stop:
# a third unanswered message does not convert and costs the relationship.
FOLLOWUP_DAYS = [7, 14]
MAX_FOLLOWUPS = len(FOLLOWUP_DAYS)

# Statuses where nudging is over: they answered, or the thread is dead.
_NO_FOLLOWUP = {OutreachStatus.replied, OutreachStatus.call_booked,
                OutreachStatus.referred, OutreachStatus.closed}


# ----------------------------------------------------------------- schemas

class ContactIn(BaseModel):
    full_name: str
    bank: str
    is_connection: bool = False
    role_title: Optional[str] = None
    desk: Optional[str] = None
    location: Optional[str] = None
    contact_type: ContactType = ContactType.junior
    linkedin_url: Optional[str] = None
    email: Optional[str] = None
    school: Optional[str] = None
    grad_year: Optional[str] = None
    shared_context: Optional[str] = None
    offer_id: Optional[int] = None
    notes: Optional[str] = None


class ContactUpdate(BaseModel):
    is_connection: Optional[bool] = None
    full_name: Optional[str] = None
    bank: Optional[str] = None
    role_title: Optional[str] = None
    desk: Optional[str] = None
    location: Optional[str] = None
    contact_type: Optional[ContactType] = None
    linkedin_url: Optional[str] = None
    email: Optional[str] = None
    school: Optional[str] = None
    grad_year: Optional[str] = None
    shared_context: Optional[str] = None
    offer_id: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[OutreachStatus] = None


class SendLinks(BaseModel):
    """Ready-to-open compose URLs. The user still presses send in their own
    client: nothing here transmits anything."""
    mailto: Optional[str] = None    # default mail client
    gmail: Optional[str] = None     # Gmail web compose
    linkedin: Optional[str] = None  # their profile, to paste into


class MessageOut(BaseModel):
    id: int
    contact_id: int
    kind: MessageKind
    channel: Channel
    ask_type: AskType
    subject: Optional[str]
    send: SendLinks
    body: str
    char_count: int
    over_limit: bool          # connection note longer than LinkedIn allows
    created_at: datetime
    sent_at: Optional[datetime]
    edited: bool


class ContactOut(BaseModel):
    id: int
    full_name: str
    bank: str
    role_title: Optional[str]
    desk: Optional[str]
    location: Optional[str]
    contact_type: ContactType
    linkedin_url: Optional[str]
    email: Optional[str]
    email_guess: Optional[str]
    email_guess_confidence: Optional[str]
    is_connection: bool
    school: Optional[str]
    grad_year: Optional[str]
    shared_context: Optional[str]
    offer_id: Optional[int]
    offer_title: Optional[str]
    status: OutreachStatus
    notes: Optional[str]
    last_sent_at: Optional[datetime]
    next_followup_at: Optional[datetime]
    replied_at: Optional[datetime]
    followup_count: int
    followups_left: int
    due_in_days: Optional[int]   # negative = overdue
    days_since_sent: Optional[int]
    message_count: int
    created_at: datetime


class DraftRequest(BaseModel):
    kind: MessageKind = MessageKind.connection_note
    channel: Channel = Channel.linkedin
    ask_type: AskType = AskType.chat
    # Free-text role for a referral ask when no offer is linked, e.g.
    # "Structured Products Sales summer internship".
    target_role: Optional[str] = None
    extra_instructions: Optional[str] = None
    # The humanizer is a second CLI call. Worth it for a real send; skip it when
    # iterating on wording, to halve the wait.
    humanize: bool = True


class MessageEdit(BaseModel):
    body: str
    subject: Optional[str] = None   # email only; ignored for LinkedIn


class SummaryOut(BaseModel):
    total: int
    by_status: dict
    by_type: dict
    due_now: int
    sent_total: int
    replied_total: int
    reply_rate: float
    drafts_unsent: int


# ----------------------------------------------------------------- helpers

def _days_between(then: Optional[datetime], now: datetime) -> Optional[int]:
    if not then:
        return None
    return (now.date() - then.date()).days


def _to_out(c: Contact, session: Session, now: Optional[datetime] = None) -> ContactOut:
    now = now or datetime.utcnow()
    n_msgs = len(session.exec(
        select(OutreachMessage).where(OutreachMessage.contact_id == c.id)).all())
    offer_title = None
    if c.offer_id:
        o = session.get(Offer, c.offer_id)
        if o:
            offer_title = o.role_title
    due_in = None
    if c.next_followup_at:
        due_in = (c.next_followup_at.date() - now.date()).days
    return ContactOut(
        id=c.id, full_name=c.full_name, bank=c.bank, role_title=c.role_title,
        desk=c.desk, location=c.location, contact_type=c.contact_type,
        linkedin_url=c.linkedin_url, email=c.email, email_guess=c.email_guess,
        email_guess_confidence=c.email_guess_confidence,
        is_connection=c.is_connection, school=c.school,
        grad_year=c.grad_year, shared_context=c.shared_context,
        offer_id=c.offer_id, offer_title=offer_title, status=c.status,
        notes=c.notes, last_sent_at=c.last_sent_at,
        next_followup_at=c.next_followup_at, replied_at=c.replied_at,
        followup_count=c.followup_count,
        followups_left=max(0, MAX_FOLLOWUPS - c.followup_count),
        due_in_days=due_in,
        days_since_sent=_days_between(c.last_sent_at, now),
        message_count=n_msgs, created_at=c.created_at,
    )


def _send_links(m: OutreachMessage, c: Optional[Contact]) -> SendLinks:
    """Build compose URLs that open the user's own client with the draft filled in.

    This is the whole "send" story and it is deliberately a handoff: the message
    lands in Gmail or the mail app, and the user presses send. Nothing is
    transmitted from here, and no credential is ever involved.
    """
    if not c:
        return SendLinks()
    links = SendLinks(linkedin=c.linkedin_url or None)
    if m.channel == Channel.email and c.email:
        subject = m.subject or ""
        # quote() over quote_plus(): mailto and Gmail both want %20 for spaces,
        # and a literal + in a URL-encoded body would arrive as a space.
        qs = urlencode({"subject": subject, "body": m.body}, quote_via=quote)
        links.mailto = f"mailto:{quote(c.email)}?{qs}"
        links.gmail = (
            "https://mail.google.com/mail/?view=cm&fs=1&"
            + urlencode({"to": c.email, "su": subject, "body": m.body}, quote_via=quote)
        )
    return links


def _msg_out(m: OutreachMessage, c: Optional[Contact] = None) -> MessageOut:
    n = len(m.body or "")
    return MessageOut(
        id=m.id, contact_id=m.contact_id, kind=m.kind, channel=m.channel,
        ask_type=m.ask_type, subject=m.subject, send=_send_links(m, c), body=m.body,
        char_count=n,
        # The 300-char cap is LinkedIn's connection-request note specifically; an
        # email or a DM of the same length is fine.
        over_limit=(m.channel == Channel.linkedin
                    and m.kind == MessageKind.connection_note
                    and n > CONNECTION_NOTE_MAX),
        created_at=m.created_at, sent_at=m.sent_at, edited=m.edited,
    )


def _base_cv_text() -> str:
    if not BASE_CV_JSON.exists():
        raise HTTPException(
            400,
            "Structured base CV not found (storage/cvs/base_cv.json). Drafting uses "
            "it as the only source of facts about you, so it must exist first.",
        )
    return flatten_cv_text(json.loads(BASE_CV_JSON.read_text(encoding="utf-8")))


def _offer_dict(session: Session, offer_id: Optional[int]) -> Optional[dict]:
    if not offer_id:
        return None
    o = session.get(Offer, offer_id)
    if not o:
        return None
    return {
        "bank": o.bank, "role_title": o.role_title, "location": o.location,
        "program_type": o.program_type.value, "start_date_raw": o.start_date_raw,
        "description": o.description,
    }


def _schedule_next_followup(c: Contact, now: datetime) -> None:
    """Set the next nudge date, or clear it once the cadence is exhausted."""
    if c.status in _NO_FOLLOWUP or c.followup_count >= MAX_FOLLOWUPS:
        c.next_followup_at = None
        return
    c.next_followup_at = now + timedelta(days=FOLLOWUP_DAYS[c.followup_count])


# ----------------------------------------------------------------- contacts

@router.get("/summary", response_model=SummaryOut)
def summary(session: Session = Depends(get_session)):
    now = datetime.utcnow()
    contacts = session.exec(select(Contact)).all()
    by_status: dict = {}
    by_type: dict = {}
    for c in contacts:
        by_status[c.status.value] = by_status.get(c.status.value, 0) + 1
        by_type[c.contact_type.value] = by_type.get(c.contact_type.value, 0) + 1
    due = sum(
        1 for c in contacts
        if c.next_followup_at and c.next_followup_at <= now and c.status not in _NO_FOLLOWUP
    )
    # "Sent" counts anyone the message actually went out to, including those who
    # have since replied or closed, so the reply rate has an honest denominator.
    sent_total = sum(1 for c in contacts if c.last_sent_at)
    replied_total = sum(
        1 for c in contacts
        if c.status in (OutreachStatus.replied, OutreachStatus.call_booked, OutreachStatus.referred)
    )
    drafts_unsent = len(session.exec(
        select(OutreachMessage).where(OutreachMessage.sent_at == None)).all())  # noqa: E711
    return SummaryOut(
        total=len(contacts), by_status=by_status, by_type=by_type, due_now=due,
        sent_total=sent_total, replied_total=replied_total,
        reply_rate=round(replied_total / sent_total, 3) if sent_total else 0.0,
        drafts_unsent=drafts_unsent,
    )


@router.get("/contacts", response_model=list[ContactOut])
def list_contacts(
    status: Optional[OutreachStatus] = None,
    bank: Optional[str] = None,
    contact_type: Optional[ContactType] = None,
    due: bool = False,
    session: Session = Depends(get_session),
):
    now = datetime.utcnow()
    q = select(Contact)
    if status:
        q = q.where(Contact.status == status)
    if bank:
        q = q.where(Contact.bank == bank)
    if contact_type:
        q = q.where(Contact.contact_type == contact_type)
    rows = session.exec(q).all()
    if due:
        rows = [c for c in rows
                if c.next_followup_at and c.next_followup_at <= now
                and c.status not in _NO_FOLLOWUP]
    # Follow-ups that are due float to the top; then never-contacted; then the
    # rest by most recently touched.
    def sort_key(c: Contact):
        overdue = 0
        if c.next_followup_at and c.next_followup_at <= now and c.status not in _NO_FOLLOWUP:
            overdue = -1
        untouched = 0 if c.status == OutreachStatus.to_contact else 1
        return (overdue, untouched, -(c.updated_at or c.created_at).timestamp())

    rows.sort(key=sort_key)
    return [_to_out(c, session, now) for c in rows]


@router.post("/contacts", response_model=ContactOut, status_code=201)
def create_contact(body: ContactIn, session: Session = Depends(get_session)):
    if not body.full_name.strip() or not body.bank.strip():
        raise HTTPException(400, "full_name and bank are required")
    c = Contact(**body.model_dump())
    c.full_name = c.full_name.strip()
    c.bank = c.bank.strip()
    session.add(c)
    session.commit()
    session.refresh(c)
    return _to_out(c, session)


@router.get("/contacts/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: int, session: Session = Depends(get_session)):
    c = session.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")
    return _to_out(c, session)


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: int, body: ContactUpdate, session: Session = Depends(get_session)):
    c = session.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")
    now = datetime.utcnow()
    data = body.model_dump(exclude_unset=True)
    new_status = data.pop("status", None)
    for k, v in data.items():
        setattr(c, k, v)
    if new_status and new_status != c.status:
        c.status = new_status
        # A reply ends the nudge cadence: chasing someone who answered is the
        # fastest way to undo the reply.
        if new_status in _NO_FOLLOWUP:
            c.next_followup_at = None
            if new_status == OutreachStatus.replied and not c.replied_at:
                c.replied_at = now
        elif new_status == OutreachStatus.sent and c.last_sent_at:
            _schedule_next_followup(c, c.last_sent_at)
    c.updated_at = now
    session.add(c)
    session.commit()
    session.refresh(c)
    return _to_out(c, session, now)


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: int, session: Session = Depends(get_session)):
    c = session.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")
    for m in session.exec(
            select(OutreachMessage).where(OutreachMessage.contact_id == contact_id)).all():
        session.delete(m)
    session.delete(c)
    session.commit()


# ----------------------------------------------------------------- messages

@router.get("/contacts/{contact_id}/messages", response_model=list[MessageOut])
def list_messages(contact_id: int, session: Session = Depends(get_session)):
    c = session.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")
    rows = session.exec(
        select(OutreachMessage)
        .where(OutreachMessage.contact_id == contact_id)
        .order_by(OutreachMessage.created_at)
    ).all()
    return [_msg_out(m, c) for m in rows]


@router.post("/contacts/{contact_id}/draft", response_model=MessageOut, status_code=201)
def draft_message(contact_id: int, body: DraftRequest, session: Session = Depends(get_session)):
    """Draft one message for a contact. Blocking: one or two Claude CLI calls."""
    c = session.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")

    contact_dict = {
        "full_name": c.full_name, "bank": c.bank, "role_title": c.role_title,
        "desk": c.desk, "location": c.location, "school": c.school,
        "grad_year": c.grad_year, "shared_context": c.shared_context,
        "notes": c.notes, "contact_type": c.contact_type.value,
    }
    previous = [
        {"kind": m.kind.value, "body": m.body,
         "sent_at": m.sent_at.isoformat() if m.sent_at else None,
         "created_at": m.created_at.isoformat()}
        for m in session.exec(
            select(OutreachMessage)
            .where(OutreachMessage.contact_id == contact_id)
            .order_by(OutreachMessage.created_at)).all()
    ]

    # A connection note is a LinkedIn artefact; over email it is just a first
    # email. Coerce rather than reject so the UI cannot produce a dead end.
    kind = body.kind
    if kind == MessageKind.connection_note and (
            body.channel == Channel.email or c.is_connection):
        # A connection note rides on a connection REQUEST. Over email there is
        # no such thing, and for someone already connected there is no Connect
        # button to attach it to — either way the right artefact is a message.
        kind = MessageKind.dm

    try:
        subject, text = draft_outreach(
            contact=contact_dict,
            kind=kind.value,
            base_cv_text=_base_cv_text(),
            offer=_offer_dict(session, c.offer_id),
            previous=previous,
            extra_instructions=body.extra_instructions,
            channel=body.channel.value,
            ask_type=body.ask_type.value,
            target_role=body.target_role,
        )
        if body.humanize:
            text = humanize_outreach(text, kind.value)
        # Enforce LinkedIn's cap by rewriting, never by truncating.
        if body.channel == Channel.linkedin and kind == MessageKind.connection_note                 and len(text) > CONNECTION_NOTE_MAX:
            text = shorten_to_limit(text, CONNECTION_NOTE_MAX)
    except ClaudeCliError as e:
        if is_usage_limit_error(e):
            raise HTTPException(429, f"Claude usage limit reached: {e}")
        raise HTTPException(502, f"Draft generation failed: {e}")

    if not text.strip():
        raise HTTPException(502, "Draft generation returned an empty message")

    if body.channel == Channel.email and not subject:
        # Never ship a blank subject line; a plain fallback beats an empty one.
        subject = f"{c.bank} - {contact_dict['full_name'].split()[0]}, quick question"

    m = OutreachMessage(contact_id=contact_id, kind=kind, channel=body.channel,
                        ask_type=body.ask_type, subject=subject, body=text)
    session.add(m)
    c.updated_at = datetime.utcnow()
    session.add(c)
    session.commit()
    session.refresh(m)
    return _msg_out(m, c)


@router.patch("/messages/{message_id}", response_model=MessageOut)
def edit_message(message_id: int, body: MessageEdit, session: Session = Depends(get_session)):
    m = session.get(OutreachMessage, message_id)
    if not m:
        raise HTTPException(404, "message not found")
    if body.body != m.body:
        m.body = body.body
        m.edited = True
    if body.subject is not None and body.subject != m.subject:
        m.subject = body.subject
        m.edited = True
    session.add(m)
    session.commit()
    session.refresh(m)
    return _msg_out(m, session.get(Contact, m.contact_id))


@router.post("/messages/{message_id}/sent", response_model=ContactOut)
def mark_sent(message_id: int, session: Session = Depends(get_session)):
    """Record that the user sent this message by hand, and schedule the nudge."""
    m = session.get(OutreachMessage, message_id)
    if not m:
        raise HTTPException(404, "message not found")
    c = session.get(Contact, m.contact_id)
    if not c:
        raise HTTPException(404, "contact not found")

    now = datetime.utcnow()
    if not m.sent_at:
        m.sent_at = now
    c.last_sent_at = now
    if m.kind == MessageKind.followup:
        c.followup_count += 1
    # A thank-you goes out after they engaged, so it must not reopen the cadence
    # or drag the contact back to a "sent, awaiting reply" state.
    if m.kind != MessageKind.thank_you:
        if c.status == OutreachStatus.to_contact:
            c.status = OutreachStatus.sent
        _schedule_next_followup(c, now)
    else:
        c.next_followup_at = None
    c.updated_at = now
    session.add(m)
    session.add(c)
    session.commit()
    session.refresh(c)
    return _to_out(c, session, now)


@router.delete("/messages/{message_id}", status_code=204)
def delete_message(message_id: int, session: Session = Depends(get_session)):
    m = session.get(OutreachMessage, message_id)
    if not m:
        raise HTTPException(404, "message not found")
    session.delete(m)
    session.commit()


# ------------------------------------------------------------ suggestions
# Who to write to next, derived from the user's own LinkedIn data export.
# See linkedin_import for why this is an import and not a scrape.

class SuggestionOut(BaseModel):
    id: int
    full_name: str
    linkedin_url: Optional[str]
    email: Optional[str]
    email_guess: Optional[str]
    email_guess_confidence: Optional[str]
    company_raw: str
    bank: str
    employer_tier: str
    position: str
    category: str
    seniority: str
    score: int
    reasons: list
    connected_on: Optional[str]
    dismissed: bool
    contact_id: Optional[int]


class ImportResult(BaseModel):
    total_rows: int
    matched: int
    with_shared_email: int
    with_guessed_email: int
    new: int
    already_known: int
    no_employer_match: int
    not_markets_role: int
    top: list[SuggestionOut]


class SearchLink(BaseModel):
    label: str
    scope: str
    url: str


def _sugg_out(s: ConnectionSuggestion) -> SuggestionOut:
    return SuggestionOut(
        id=s.id, full_name=s.full_name, linkedin_url=s.linkedin_url, email=s.email,
        email_guess=s.email_guess, email_guess_confidence=s.email_guess_confidence,
        company_raw=s.company_raw, bank=s.bank, employer_tier=s.employer_tier,
        position=s.position, category=s.category, seniority=s.seniority,
        score=s.score, reasons=list(s.reasons or []), connected_on=s.connected_on,
        dismissed=s.dismissed, contact_id=s.contact_id,
    )


def _dedupe_key(url: Optional[str], name: str, bank: str) -> str:
    """A LinkedIn URL is the stable identity; fall back to name+firm for rows
    where the export omitted it (it does that for some connections)."""
    return (url or "").strip().rstrip("/").lower() or f"{name.strip().lower()}|{bank.lower()}"


@router.post("/import/linkedin", response_model=ImportResult)
async def import_linkedin(file: UploadFile = File(...), session: Session = Depends(get_session)):
    """Import Connections.csv from LinkedIn's own data export and rank the people
    worth approaching. Re-importing is safe: existing rows are updated in place,
    and anyone already promoted to a Contact keeps that link."""
    raw = await file.read()
    try:
        rows = parse_connections_csv(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))

    matches, stats = analyse(rows)

    existing = {
        _dedupe_key(s.linkedin_url, s.full_name, s.bank): s
        for s in session.exec(select(ConnectionSuggestion)).all()
    }
    # Someone already in the outreach list is not a suggestion any more.
    contact_keys = {
        _dedupe_key(c.linkedin_url, c.full_name, c.bank)
        for c in session.exec(select(Contact)).all()
    }

    new_count = known_count = 0
    for m in matches:
        key = _dedupe_key(m.get("linkedin_url"), m["full_name"], m["bank"])
        if key in contact_keys:
            known_count += 1
            continue
        row = existing.get(key)
        if row:
            # Refresh the classification (their title may have changed, and the
            # vocabulary improves) without resurrecting a dismissal.
            row.position = m["position"]
            row.company_raw = m["company"]
            row.bank = m["bank"]
            row.employer_tier = m["employer_tier"]
            row.category = m["category"]
            row.seniority = m["seniority"]
            row.score = m["score"]
            row.reasons = m["reasons"]
            row.email = m.get("email") or row.email
            if not row.email:
                row.email_guess = m.get("email_guess")
                row.email_guess_confidence = m.get("email_guess_confidence")
            known_count += 1
            session.add(row)
            continue
        session.add(ConnectionSuggestion(
            full_name=m["full_name"], linkedin_url=m.get("linkedin_url"),
            email=m.get("email"), email_guess=m.get("email_guess"),
            email_guess_confidence=m.get("email_guess_confidence"),
            company_raw=m["company"], bank=m["bank"],
            employer_tier=m["employer_tier"], position=m["position"],
            category=m["category"], seniority=m["seniority"], score=m["score"],
            reasons=m["reasons"], connected_on=m.get("connected_on"),
        ))
        new_count += 1
    session.commit()

    top = session.exec(
        select(ConnectionSuggestion)
        .where(ConnectionSuggestion.dismissed == False)  # noqa: E712
        .where(ConnectionSuggestion.contact_id == None)  # noqa: E711
        .order_by(ConnectionSuggestion.score.desc())
        .limit(20)
    ).all()

    logger.info("linkedin import: %s rows, %s matched, %s new", stats["total"],
                stats["matched"], new_count)
    return ImportResult(
        total_rows=stats["total"], matched=stats["matched"], new=new_count,
        with_shared_email=stats["with_shared_email"],
        with_guessed_email=stats["with_guessed_email"],
        already_known=known_count, no_employer_match=stats["no_employer_match"],
        not_markets_role=stats["not_markets_role"],
        top=[_sugg_out(s) for s in top],
    )


@router.get("/suggestions", response_model=list[SuggestionOut])
def list_suggestions(
    category: Optional[str] = None,
    bank: Optional[str] = None,
    min_score: int = 0,
    include_dismissed: bool = False,
    limit: int = 1000,
    session: Session = Depends(get_session),
):
    q = select(ConnectionSuggestion).where(ConnectionSuggestion.score >= min_score)
    if not include_dismissed:
        q = q.where(ConnectionSuggestion.dismissed == False)  # noqa: E712
    if category:
        q = q.where(ConnectionSuggestion.category == category)
    if bank:
        q = q.where(ConnectionSuggestion.bank == bank)
    rows = session.exec(q.order_by(ConnectionSuggestion.score.desc()).limit(limit)).all()
    # Anyone already promoted to a Contact drops out: they are in the pipeline now.
    return [_sugg_out(s) for s in rows if s.contact_id is None]


@router.post("/suggestions/{suggestion_id}/add", response_model=ContactOut)
def add_suggestion(suggestion_id: int, session: Session = Depends(get_session)):
    """Promote a suggestion into a real outreach contact, carrying the classified
    desk and seniority across so the drafter starts with the right angle."""
    s = session.get(ConnectionSuggestion, suggestion_id)
    if not s:
        raise HTTPException(404, "suggestion not found")
    if s.contact_id:
        existing = session.get(Contact, s.contact_id)
        if existing:
            return _to_out(existing, session)

    ctype = ContactType(s.seniority) if s.seniority in ContactType.__members__ \
        else ContactType.junior
    c = Contact(
        full_name=s.full_name, bank=s.bank, role_title=s.position,
        desk=s.category if s.category != "markets" else None,
        contact_type=ctype, linkedin_url=s.linkedin_url, email=s.email,
        # The export contains only 1st-degree connections, so there is no
        # Connect button on their profile to attach a connection note to.
        is_connection=True,
        # A derived address lands in email_guess so channel:auto never routes to
        # it silently; promoting it into `email` is an explicit user action.
        email_guess=s.email_guess, email_guess_confidence=s.email_guess_confidence,
        # Being a first-degree connection IS the tie, and it is a true one. It is
        # recorded as the plain fact it is, with nothing implied about how well
        # they know each other.
        shared_context=("Already a first-degree LinkedIn connection"
                        + (f", connected {s.connected_on}" if s.connected_on else "")),
        notes=f"From LinkedIn import: {s.company_raw} - {s.position}",
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    s.contact_id = c.id
    session.add(s)
    session.commit()
    return _to_out(c, session)


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuggestionOut)
def dismiss_suggestion(suggestion_id: int, session: Session = Depends(get_session)):
    s = session.get(ConnectionSuggestion, suggestion_id)
    if not s:
        raise HTTPException(404, "suggestion not found")
    s.dismissed = True
    session.add(s)
    session.commit()
    session.refresh(s)
    return _sugg_out(s)


@router.get("/search-links", response_model=list[SearchLink])
def get_search_links():
    """Pre-built searches on LinkedIn's own site, scoped to existing connections.
    Opening a search page is ordinary use; this only saves typing the query."""
    return [SearchLink(**l) for l in search_links()]


# ------------------------------------------------------------ batch drafting
# Drafting N personalised messages is N Claude CLI calls at roughly 6s each
# (12s with the humaniser), so it runs on a background thread with progress
# rather than blocking a request for ten minutes. Mirrors main._auto_tailor_worker.

_batch_lock = _threading.Lock()
_batch_state: dict = {
    "running": False, "total": 0, "done": 0, "failed": 0,
    "current": None, "last_error": None, "contact_ids": [],
}


class BulkDraftRequest(BaseModel):
    suggestion_ids: list[int] = []
    contact_ids: list[int] = []
    kind: MessageKind = MessageKind.connection_note
    # "auto" picks email where the export gave an address, LinkedIn otherwise —
    # the right default for a mixed batch.
    channel: str = "auto"
    ask_type: AskType = AskType.chat
    target_role: Optional[str] = None
    humanize: bool = True


class BatchStatus(BaseModel):
    running: bool
    total: int
    done: int
    failed: int
    current: Optional[str]
    last_error: Optional[str]
    contact_ids: list[int]


class QueueItem(BaseModel):
    """One unsent draft, with everything the send step needs in one payload."""
    message: MessageOut
    contact_id: int
    contact_name: str
    bank: str
    role_title: Optional[str]
    contact_type: ContactType
    status: OutreachStatus


def _resolve_channel(pref: str, c: Contact) -> Channel:
    if pref == "email":
        return Channel.email
    if pref == "linkedin":
        return Channel.linkedin
    return Channel.email if (c.email or "").strip() else Channel.linkedin


def _batch_worker(contact_ids: list[int], req: BulkDraftRequest) -> None:
    global _batch_state
    try:
        base_cv = _base_cv_text()
    except HTTPException as e:
        _batch_state.update(running=False, last_error=str(e.detail))
        _batch_lock.release()
        return
    try:
        for cid in contact_ids:
            with Session(ENGINE) as session:
                c = session.get(Contact, cid)
                if not c:
                    _batch_state["done"] += 1
                    continue
                _batch_state["current"] = f"{c.full_name} — {c.bank}"
                channel = _resolve_channel(req.channel, c)
                kind = req.kind
                if kind == MessageKind.connection_note and (
                        channel == Channel.email or c.is_connection):
                    kind = MessageKind.dm
                try:
                    subject, text = draft_outreach(
                        contact={
                            "full_name": c.full_name, "bank": c.bank,
                            "role_title": c.role_title, "desk": c.desk,
                            "location": c.location, "school": c.school,
                            "grad_year": c.grad_year, "shared_context": c.shared_context,
                            "notes": c.notes, "contact_type": c.contact_type.value,
                        },
                        kind=kind.value, base_cv_text=base_cv,
                        offer=_offer_dict(session, c.offer_id),
                        previous=[
                            {"kind": m.kind.value, "body": m.body,
                             "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                             "created_at": m.created_at.isoformat()}
                            for m in session.exec(
                                select(OutreachMessage)
                                .where(OutreachMessage.contact_id == cid)
                                .order_by(OutreachMessage.created_at)).all()
                        ],
                        channel=channel.value, ask_type=req.ask_type.value,
                        target_role=req.target_role,
                    )
                    if req.humanize:
                        text = humanize_outreach(text, kind.value)
                    if channel == Channel.linkedin and kind == MessageKind.connection_note \
                            and len(text) > CONNECTION_NOTE_MAX:
                        text = shorten_to_limit(text, CONNECTION_NOTE_MAX)
                    if not text.strip():
                        raise ClaudeCliError("empty draft")
                    if channel == Channel.email and not subject:
                        subject = f"{c.bank} - {c.full_name.split()[0]}, quick question"
                    session.add(OutreachMessage(
                        contact_id=cid, kind=kind, channel=channel,
                        ask_type=req.ask_type, subject=subject, body=text))
                    session.commit()
                    _batch_state["done"] += 1
                except Exception as e:
                    logger.warning("batch draft failed for contact %s: %s", cid, e)
                    _batch_state["failed"] += 1
                    _batch_state["done"] += 1
                    _batch_state["last_error"] = str(e)[:300]
                    if is_usage_limit_error(e):
                        # The subscription window is spent; every remaining call
                        # would fail the same way. Stop and let the user resume.
                        _batch_state["last_error"] = (
                            "Claude usage limit reached. The rest were not drafted — "
                            "run the batch again once the window resets."
                        )
                        break
    finally:
        _batch_state["running"] = False
        _batch_state["current"] = None
        _batch_lock.release()


@router.post("/bulk-draft", response_model=BatchStatus)
def bulk_draft(req: BulkDraftRequest, session: Session = Depends(get_session)):
    """Promote any suggestions given, then draft one personalised message per
    contact in the background. Every draft is written from that person's own
    firm, desk and title: this is N individual messages, not one mail-merged."""
    if _batch_state["running"]:
        raise HTTPException(409, "A batch is already running")

    contact_ids: list[int] = []
    for sid in req.suggestion_ids:
        s = session.get(ConnectionSuggestion, sid)
        if not s:
            continue
        if s.contact_id:
            contact_ids.append(s.contact_id)
            continue
        contact_ids.append(add_suggestion(sid, session).id)
    for cid in req.contact_ids:
        if session.get(Contact, cid) and cid not in contact_ids:
            contact_ids.append(cid)

    if not contact_ids:
        raise HTTPException(400, "No valid contacts or suggestions to draft for")
    if not _batch_lock.acquire(blocking=False):
        raise HTTPException(409, "A batch is already running")

    _batch_state.update(running=True, total=len(contact_ids), done=0, failed=0,
                        current=None, last_error=None, contact_ids=contact_ids)
    _threading.Thread(target=_batch_worker, args=(contact_ids, req), daemon=True).start()
    return BatchStatus(**_batch_state)


@router.get("/bulk-draft/status", response_model=BatchStatus)
def bulk_draft_status():
    return BatchStatus(**_batch_state)


@router.get("/queue", response_model=list[QueueItem])
def send_queue(session: Session = Depends(get_session)):
    """Every drafted-but-unsent message, best contact first.

    This is the send step: the user walks the queue, and each item is two clicks
    (open the composer, mark sent). Nothing is transmitted from here.
    """
    msgs = session.exec(
        select(OutreachMessage)
        .where(OutreachMessage.sent_at == None)  # noqa: E711
        .order_by(OutreachMessage.created_at)
    ).all()
    out: list[QueueItem] = []
    for m in msgs:
        c = session.get(Contact, m.contact_id)
        if not c:
            continue
        out.append(QueueItem(
            message=_msg_out(m, c), contact_id=c.id, contact_name=c.full_name,
            bank=c.bank, role_title=c.role_title, contact_type=c.contact_type,
            status=c.status,
        ))
    return out
