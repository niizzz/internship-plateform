"""New-item email digest: after each refresh, one email listing the offers and
events that appeared since the last digest.

SELF-NOTIFICATION ONLY. The single recipient is `Settings.notify_email`, the
user's own address; nothing here takes a recipient from anywhere else, and
nothing imports the outreach code. This is NOT a way to send outreach (see
CLAUDE.md invariant 11 — outreach stays a by-hand handoff).

SMTP credentials (typically a Gmail app password) live in the local SQLite
settings row (backend/data, gitignored) and are never returned by the API.
"""
from __future__ import annotations

import html
import logging
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from typing import Optional

from sqlmodel import Session, select

from db import ENGINE, Offer, RecruitingEvent, Settings

logger = logging.getLogger(__name__)

APP_URL = "http://localhost:5173"
# One digest never lists more than this per section; the rest are summarised.
MAX_ROWS = 40

last_result: dict = {"at": None, "ok": None, "detail": None}


class NotifyError(Exception):
    pass


def is_configured(st: Settings) -> bool:
    return bool(st.notify_email and st.smtp_host and st.smtp_user and st.smtp_password)


def _send(st: Settings, subject: str, text: str, html_body: str) -> None:
    if not is_configured(st):
        raise NotifyError("email not configured (recipient, SMTP server, user and app password are required)")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Internship Plateform <{st.smtp_user}>"
    msg["To"] = st.notify_email
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    port = int(st.smtp_port or 465)
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(st.smtp_host, port, context=ctx, timeout=30) as s:
                s.login(st.smtp_user, st.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(st.smtp_host, port, timeout=30) as s:
                s.starttls(context=ctx)
                s.login(st.smtp_user, st.smtp_password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise NotifyError(
            "SMTP login refused — for Gmail use an App Password "
            "(Google Account > Security > 2-Step Verification > App passwords), "
            f"not your normal password. ({e.smtp_code})") from e
    except (smtplib.SMTPException, OSError) as e:
        raise NotifyError(f"{type(e).__name__}: {e}") from e


def _fmt_event_when(e: RecruitingEvent) -> str:
    if not e.starts_at:
        return "date TBC"
    s = e.starts_at.strftime("%a %d %b %Y") if e.all_day else e.starts_at.strftime("%a %d %b %Y, %H:%M")
    return f"{s} {e.timezone or ''}".strip()


def _offer_rows(offers: list[Offer]) -> tuple[str, str]:
    text, rows = [], []
    for o in offers[:MAX_ROWS]:
        where = o.city or o.country or ""
        start = f" · starts {o.start_date_raw}" if o.start_date_raw else ""
        text.append(f"- [{o.bank}] {o.role_title} ({where}{start})\n  {o.apply_url}")
        rows.append(
            f'<tr><td style="padding:6px 10px;color:#64748b;white-space:nowrap">{html.escape(o.bank)}</td>'
            f'<td style="padding:6px 10px"><a href="{html.escape(o.apply_url)}" style="color:#0e7490;font-weight:600">'
            f'{html.escape(o.role_title)}</a><div style="color:#64748b;font-size:12px">'
            f'{html.escape(where)}{html.escape(start)} · {html.escape(o.category.value if hasattr(o.category, "value") else str(o.category))}'
            f' · <a href="{APP_URL}/offers/{o.id}" style="color:#64748b">open in platform</a></div></td></tr>')
    if len(offers) > MAX_ROWS:
        more = f"…and {len(offers) - MAX_ROWS} more in the platform."
        text.append(more)
        rows.append(f'<tr><td colspan="2" style="padding:6px 10px;color:#64748b">{more}</td></tr>')
    return "\n".join(text), "".join(rows)


def _event_rows(events: list[RecruitingEvent]) -> tuple[str, str]:
    text, rows = [], []
    for e in events[:MAX_ROWS]:
        where = "Virtual" if e.is_virtual else (e.city or e.location or e.country or "")
        dl = f" · register by {e.registration_deadline:%d %b}" if e.registration_deadline else ""
        text.append(f"- [{e.bank}] {e.title} — {_fmt_event_when(e)}, {where}{dl}\n  {e.register_url}")
        rows.append(
            f'<tr><td style="padding:6px 10px;color:#64748b;white-space:nowrap">{html.escape(e.bank)}</td>'
            f'<td style="padding:6px 10px"><a href="{html.escape(e.register_url)}" style="color:#7e22ce;font-weight:600">'
            f'{html.escape(e.title)}</a><div style="color:#64748b;font-size:12px">'
            f'{html.escape(_fmt_event_when(e))} · {html.escape(where)}{html.escape(dl)}</div></td></tr>')
    if len(events) > MAX_ROWS:
        more = f"…and {len(events) - MAX_ROWS} more in the platform."
        text.append(more)
        rows.append(f'<tr><td colspan="2" style="padding:6px 10px;color:#64748b">{more}</td></tr>')
    return "\n".join(text), "".join(rows)


def _section(title: str, rows_html: str) -> str:
    return (f'<h3 style="font-family:sans-serif;margin:18px 0 6px">{title}</h3>'
            f'<table style="border-collapse:collapse;font-family:sans-serif;font-size:14px">{rows_html}</table>')


def build_digest(offers: list[Offer], events: list[RecruitingEvent]) -> tuple[str, str, str]:
    parts = []
    if offers:
        parts.append(f"{len(offers)} new offer{'s' if len(offers) != 1 else ''}")
    if events:
        parts.append(f"{len(events)} new event{'s' if len(events) != 1 else ''}")
    subject = "Internship Plateform: " + " + ".join(parts)
    o_txt, o_html = _offer_rows(offers)
    e_txt, e_html = _event_rows(events)
    text = "\n\n".join(x for x in (
        f"NEW OFFERS\n{o_txt}" if offers else "",
        f"NEW EVENTS (registration open)\n{e_txt}" if events else "",
        f"Open the platform: {APP_URL}",
    ) if x)
    body = "".join((
        _section("New offers", o_html) if offers else "",
        _section("New events — registration open", e_html) if events else "",
        f'<p style="font-family:sans-serif;font-size:12px;color:#64748b;margin-top:20px">'
        f'Sent by your local Internship Plateform after a refresh. '
        f'<a href="{APP_URL}">Open the platform</a> · turn these off in Settings.</p>',
    ))
    return subject, text, f"<html><body>{body}</body></html>"


def pending_items(s: Session, st: Settings) -> tuple[list[Offer], list[RecruitingEvent]]:
    since = st.notify_since or datetime.utcnow()
    offers = s.exec(select(Offer).where(
        Offer.is_active == True, Offer.emailed_at == None,  # noqa: E711,E712
        Offer.first_seen_at >= since,
    ).order_by(Offer.bank, Offer.role_title)).all()
    events = s.exec(select(RecruitingEvent).where(
        RecruitingEvent.is_active == True, RecruitingEvent.hidden == False,  # noqa: E712
        RecruitingEvent.manual == False, RecruitingEvent.registration_open == True,  # noqa: E712
        RecruitingEvent.emailed_at == None,  # noqa: E711
        RecruitingEvent.first_seen_at >= since,
    ).order_by(RecruitingEvent.starts_at)).all()
    # Only events an S&T candidate would want in their inbox; the rest are on
    # the Events page.
    events = [e for e in events if e.relevance in ("markets", "general")]
    return list(offers), events


def send_new_items_digest() -> dict:
    """Email everything new since the last digest. Items are marked emailed
    ONLY after a successful send, so a failed send retries next refresh."""
    global last_result
    with Session(ENGINE) as s:
        st = s.get(Settings, 1)
        if not st or not st.email_notifications:
            return {"sent": False, "reason": "disabled"}
        offers, events = pending_items(s, st)
        if not offers and not events:
            return {"sent": False, "reason": "nothing new"}
        subject, text, body = build_digest(offers, events)
        try:
            _send(st, subject, text, body)
        except NotifyError as e:
            logger.error("digest email failed: %s", e)
            last_result = {"at": datetime.utcnow().isoformat(), "ok": False, "detail": str(e)}
            return {"sent": False, "reason": str(e)}
        now = datetime.utcnow()
        for row in (*offers, *events):
            row.emailed_at = now
            s.add(row)
        s.commit()
        detail = f"{len(offers)} offers, {len(events)} events"
        logger.info("digest email sent to %s: %s", st.notify_email, detail)
        last_result = {"at": now.isoformat(), "ok": True, "detail": detail}
        return {"sent": True, "offers": len(offers), "events": len(events)}


def send_test_email() -> None:
    global last_result
    with Session(ENGINE) as s:
        st = s.get(Settings, 1)
        _send(st, "Internship Plateform: test email",
              "Email notifications are working. You'll get one email after each refresh "
              "that finds new offers or events.",
              "<html><body style='font-family:sans-serif'><p>Email notifications are working ✅</p>"
              "<p>You'll get one email after each refresh that finds new offers or events.</p></body></html>")
        last_result = {"at": datetime.utcnow().isoformat(), "ok": True, "detail": "test email sent"}


def status(st: Optional[Settings]) -> dict:
    return {"configured": bool(st and is_configured(st)), **last_result}
