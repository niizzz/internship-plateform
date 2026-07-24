"""FastAPI app: offers, refresh, tailoring, notifications, settings."""
from __future__ import annotations

import asyncio
import json
import sys
# Playwright needs subprocess support; on Windows we must use ProactorEventLoop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select, func

from db import (
    ENGINE, init_db, get_session,
    Offer, Application, ApplicationStatus,
    TailoredDocument, DocumentKind,
    Notification, Settings,
)
from scrapers import ALL_SCRAPERS
from scrapers.registry import build_scrapers
from scrapers.orchestrator import refresh_all
from cv_utils import ensure_docx, full_text
from cv_render import render_cv_pdf, render_cover_letter_pdf, flatten_cv_text
from llm import tailor_cv_json, generate_cover_letter, ClaudeCliError, is_usage_limit_error

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# Persist logs: the console lives in a minimized launcher window and every
# warning (silent scraper failures, tailoring errors) vanished with it.
from logging.handlers import RotatingFileHandler as _RFH
_LOG_DIR = Path(__file__).parent / "data"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_fh = _RFH(_LOG_DIR / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.getLogger().addHandler(_fh)
logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).parent
STORAGE_DIR = BACKEND_DIR / "storage"
CV_DIR = STORAGE_DIR / "cvs"
OFFERS_DIR = STORAGE_DIR / "offers"
DOCS_DIR = STORAGE_DIR / "documents"
for d in (STORAGE_DIR, CV_DIR, OFFERS_DIR, DOCS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Lock to prevent concurrent refreshes.
_refresh_lock = asyncio.Lock()
_last_refresh: dict = {"status": "idle", "started_at": None, "finished_at": None, "result": None}


app = FastAPI(title="Internship DB", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------- Schemas ---------------------------------------------------------

class OfferOut(BaseModel):
    id: int
    bank: str
    role_title: str
    category: str
    location: str
    country: str
    city: Optional[str]
    start_date_raw: Optional[str]
    duration: Optional[str]
    program_type: str
    description: Optional[str]
    apply_url: str
    source_url: Optional[str]
    posted_at: Optional[datetime] = None  # when the BANK published it
    first_seen_at: datetime
    last_seen_at: datetime
    is_active: bool
    application_status: str = ApplicationStatus.not_applied.value
    application_notes: Optional[str] = None
    has_tailored_cv: bool = False
    has_tailored_cover_letter: bool = False


def _offer_to_out(
    session: Session, o: Offer,
    app_row: Optional[Application] = None,
    docs: Optional[list[TailoredDocument]] = None,
    prefetched: bool = False,
) -> OfferOut:
    if not prefetched:
        app_row = session.exec(select(Application).where(Application.offer_id == o.id)).first()
        docs = session.exec(select(TailoredDocument).where(TailoredDocument.offer_id == o.id)).all()
    docs = docs or []
    return OfferOut(
        id=o.id,
        bank=o.bank,
        role_title=o.role_title,
        category=o.category.value,
        location=o.location,
        country=o.country,
        city=o.city,
        start_date_raw=o.start_date_raw,
        duration=o.duration,
        program_type=o.program_type.value,
        description=o.description,
        apply_url=o.apply_url,
        source_url=o.source_url,
        posted_at=o.posted_at,
        first_seen_at=o.first_seen_at,
        last_seen_at=o.last_seen_at,
        is_active=o.is_active,
        application_status=app_row.status.value if app_row else ApplicationStatus.not_applied.value,
        application_notes=app_row.notes if app_row else None,
        has_tailored_cv=any(d.kind == DocumentKind.cv for d in docs),
        has_tailored_cover_letter=any(d.kind == DocumentKind.cover_letter for d in docs),
    )


class ApplicationUpdate(BaseModel):
    status: Optional[ApplicationStatus] = None
    notes: Optional[str] = None


class ExtraDocOut(BaseModel):
    label: str
    filename: str


class SettingsOut(BaseModel):
    has_api_key: bool
    anthropic_model: str
    base_cv_filename: Optional[str]
    base_cover_letter_filename: Optional[str] = None
    extra_documents: list[ExtraDocOut] = []
    warning: Optional[str] = None


class SettingsUpdate(BaseModel):
    anthropic_api_key: Optional[str] = None
    anthropic_model: Optional[str] = None


class ProfileOut(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    nationality: Optional[str] = None
    date_of_birth: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    postcode: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    university: Optional[str] = None
    degree: Optional[str] = None
    graduation_year: Optional[str] = None
    gpa: Optional[str] = None
    work_authorization: Optional[str] = None
    languages: Optional[str] = None


class ProfileUpdate(ProfileOut):
    pass


class ApplyAssistRequest(BaseModel):
    cv_source: str = "tailored"  # "tailored" | "original"
    cl_source: str = "tailored"


class NotificationOut(BaseModel):
    id: int
    kind: str
    created_at: datetime
    offer_snapshot: dict


class RefreshStatus(BaseModel):
    status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    result: Optional[dict]
    implemented_scrapers: list[str]


# ---------- Offers ----------------------------------------------------------

@app.get("/api/offers", response_model=list[OfferOut])
def list_offers(
    bank: Optional[str] = None,
    category: Optional[str] = None,
    country: Optional[str] = None,
    status: Optional[ApplicationStatus] = None,
    search: Optional[str] = None,
    include_inactive: bool = False,
    session: Session = Depends(get_session),
):
    q = select(Offer)
    if not include_inactive:
        q = q.where(Offer.is_active == True)  # noqa: E712
    if bank:
        q = q.where(Offer.bank == bank)
    if category:
        q = q.where(Offer.category == category)
    if country:
        q = q.where(Offer.country == country)
    if search:
        like = f"%{search.lower()}%"
        q = q.where(func.lower(Offer.role_title).like(like) | func.lower(Offer.location).like(like))
    q = q.order_by(Offer.last_seen_at.desc())
    offers = session.exec(q).all()

    # Bulk-fetch applications and tailored docs (2 queries instead of 2 per offer).
    apps = {a.offer_id: a for a in session.exec(select(Application)).all()}
    docs_by_offer: dict[int, list[TailoredDocument]] = {}
    for d in session.exec(select(TailoredDocument)).all():
        docs_by_offer.setdefault(d.offer_id, []).append(d)

    rows = [
        _offer_to_out(session, o, apps.get(o.id), docs_by_offer.get(o.id), prefetched=True)
        for o in offers
    ]
    if status:
        rows = [r for r in rows if r.application_status == status.value]
    return rows


@app.get("/api/offers/{offer_id}", response_model=OfferOut)
def get_offer(offer_id: int, session: Session = Depends(get_session)):
    o = session.get(Offer, offer_id)
    if not o:
        raise HTTPException(404, "Offer not found")
    return _offer_to_out(session, o)


@app.patch("/api/offers/{offer_id}/application", response_model=OfferOut)
def update_application(offer_id: int, update: ApplicationUpdate, session: Session = Depends(get_session)):
    o = session.get(Offer, offer_id)
    if not o:
        raise HTTPException(404, "Offer not found")
    app_row = session.exec(select(Application).where(Application.offer_id == offer_id)).first()
    if not app_row:
        app_row = Application(offer_id=offer_id)
        session.add(app_row)
    if update.status is not None:
        app_row.status = update.status
        if update.status == ApplicationStatus.not_applied:
            # Reverting to not-applied must clear the timestamp, or a later
            # re-apply would inherit a stale date and skew the dashboard.
            app_row.applied_at = None
        elif app_row.applied_at is None:
            app_row.applied_at = datetime.utcnow()
    if update.notes is not None:
        app_row.notes = update.notes
    app_row.updated_at = datetime.utcnow()
    session.add(app_row)
    session.commit()
    session.refresh(o)
    return _offer_to_out(session, o)


# ---------- Refresh ---------------------------------------------------------

def _append_refresh_history(result: dict) -> None:
    """One JSON line per refresh (data/refresh_history.jsonl) so failure
    patterns ("SocGen has failed 5 refreshes running") are visible after the
    console window is long gone. Best-effort — never breaks a refresh."""
    try:
        line = json.dumps({
            "at": datetime.utcnow().isoformat(),
            "scraped": result.get("scraped_banks"),
            "failed": result.get("failed"),
            "suspect_zero": result.get("suspect_zero"),
            "inserted": result.get("inserted"),
            "updated": result.get("updated"),
            "removed": result.get("removed"),
        }, ensure_ascii=False)
        with open(BACKEND_DIR / "data" / "refresh_history.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        logger.debug("could not append refresh history", exc_info=True)


async def _do_refresh():
    global _last_refresh
    async with _refresh_lock:
        _last_refresh = {"status": "running", "started_at": datetime.utcnow(), "finished_at": None, "result": None}

        def _progress(agg: dict) -> None:
            # Live per-bank progress: banks are persisted as they finish, so the
            # UI polls this and streams new offers in instead of waiting for the
            # slowest scraper.
            _last_refresh["result"] = agg

        try:
            scrapers = build_scrapers(headless=True)
            result = await refresh_all(scrapers, progress=_progress)
            _last_refresh = {
                "status": "done",
                "started_at": _last_refresh["started_at"],
                "finished_at": datetime.utcnow(),
                "result": result,
            }
            _append_refresh_history(result)
            # No auto-tailoring after refresh (user's choice 2026-07-07):
            # documents are generated on demand when the user clicks Apply.
            # POST /api/tailor/run still exists for a manual bulk run.
        except Exception as e:
            logger.exception("Refresh failed: %s", e)
            _last_refresh = {
                "status": "error",
                "started_at": _last_refresh["started_at"],
                "finished_at": datetime.utcnow(),
                "result": {"error": str(e)},
            }


@app.post("/api/refresh", response_model=RefreshStatus)
async def refresh(background_tasks: BackgroundTasks):
    if _refresh_lock.locked():
        return RefreshStatus(
            status="running",
            started_at=_last_refresh.get("started_at"),
            finished_at=None,
            result=None,
            implemented_scrapers=[c.bank_name for c in ALL_SCRAPERS],
        )
    background_tasks.add_task(_do_refresh)
    return RefreshStatus(
        status="started",
        started_at=datetime.utcnow(),
        finished_at=None,
        result=None,
        implemented_scrapers=[c.bank_name for c in ALL_SCRAPERS],
    )


@app.get("/api/refresh/status", response_model=RefreshStatus)
def refresh_status():
    return RefreshStatus(
        status=_last_refresh.get("status", "idle"),
        started_at=_last_refresh.get("started_at"),
        finished_at=_last_refresh.get("finished_at"),
        result=_last_refresh.get("result"),
        implemented_scrapers=[c.bank_name for c in ALL_SCRAPERS],
    )


@app.get("/api/debug/playwright")
async def debug_playwright() -> dict:
    """Verify Playwright can launch a browser under uvicorn's event loop."""
    import asyncio as _a
    loop = _a.get_running_loop()
    info = {"loop_type": type(loop).__name__, "platform": sys.platform}
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            await browser.close()
        info["playwright_ok"] = True
    except Exception as e:
        info["playwright_ok"] = False
        info["error"] = str(e)
    return info


# ---------- Tailoring -------------------------------------------------------

class TailorResponse(BaseModel):
    cv_path: str
    cover_letter_path: str
    cover_letter_text: str
    cv_changed_paragraphs: int


BASE_CV_JSON = CV_DIR / "base_cv.json"

import hashlib as _hashlib


def _tailor_fingerprint(o: Offer, base_bytes: Optional[bytes] = None) -> str:
    """Content hash of everything a tailored document is derived from: the
    structured base CV and the offer's title + description. A mismatch against
    TailoredDocument.fingerprint means the stored PDF is stale (base CV was
    re-transcribed, or the bank edited the posting) and must be regenerated."""
    if base_bytes is None:
        try:
            base_bytes = BASE_CV_JSON.read_bytes()
        except OSError:
            base_bytes = b""
    h = _hashlib.sha256()
    h.update(base_bytes)
    h.update(b"\x00")
    h.update((o.role_title or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((o.description or "").encode("utf-8"))
    return h.hexdigest()[:32]


def _tailor_offer_impl(session: Session, o: Offer, settings: Settings) -> TailorResponse:
    """Generate tailored CV + cover letter (both PDF) for one offer via the
    Claude CLI. The CV is rendered from structured data through a fixed HTML
    template (cv_render), so formatting is always identical to the base CV —
    tailoring only rewrites text. Blocking."""
    if not BASE_CV_JSON.exists():
        raise ClaudeCliError(
            "Structured base CV not found (storage/cvs/base_cv.json). It defines "
            "the CV layout + content that tailoring rewrites."
        )
    base_cv = json.loads(BASE_CV_JSON.read_text(encoding="utf-8"))

    base_cl_text = None
    if settings.base_cover_letter_path and Path(settings.base_cover_letter_path).exists():
        try:
            base_cl_docx = ensure_docx(Path(settings.base_cover_letter_path), CV_DIR / "_work")
            base_cl_text = full_text(base_cl_docx)
        except Exception as e:
            logger.warning("Could not read base cover letter: %s", e)

    offer_dict = {
        "bank": o.bank,
        "role_title": o.role_title,
        "location": o.location,
        "program_type": o.program_type.value,
        "start_date_raw": o.start_date_raw,
        "description": o.description,
    }

    tailored_cv, n_changes = tailor_cv_json(base_cv, offer_dict)

    offer_folder = OFFERS_DIR / f"{o.id}_{_slug(o.bank)}_{_slug(o.role_title)[:40]}"
    cv_out = render_cv_pdf(tailored_cv, offer_folder / "tailored_cv.pdf")

    cl_body = generate_cover_letter(
        base_cv_text=flatten_cv_text(base_cv), offer=offer_dict, base_cover_letter_text=base_cl_text,
    )
    now = datetime.now()
    letter_date = f"{now.day} {now.strftime('%B')} {now.year}"
    signoff = "Sincerely,"
    greeting = "Dear Recruiting Team,"
    header = {
        "name": base_cv.get("name"), "contact": base_cv.get("contact"),
        "linkedin_url": base_cv.get("linkedin_url"), "linkedin_label": base_cv.get("linkedin_label"),
        "date": letter_date,
        "recipient": [o.bank, "Recruiting Team"],
        "greeting": greeting,
        "signoff": signoff,
        "signature": base_cv.get("name"),
    }
    cl_out = render_cover_letter_pdf(cl_body, header, offer_folder / "cover_letter.pdf")
    # Assemble a readable full-letter preview for the UI (the PDF adds the letterhead).
    cl_text = f"{letter_date}\n\n{o.bank}\nRecruiting Team\n\n{greeting}\n\n{cl_body}\n\n{signoff}\n{base_cv.get('name')}"

    # Upsert TailoredDocument rows, stamped with the input fingerprint so a
    # later base-CV or posting change invalidates them.
    fp = _tailor_fingerprint(o)
    for kind, path in ((DocumentKind.cv, cv_out), (DocumentKind.cover_letter, cl_out)):
        existing = session.exec(
            select(TailoredDocument).where(
                (TailoredDocument.offer_id == o.id) & (TailoredDocument.kind == kind)
            )
        ).first()
        if existing:
            existing.file_path = str(path)
            existing.model = "claude-cli"
            existing.created_at = datetime.utcnow()
            existing.fingerprint = fp
        else:
            session.add(TailoredDocument(
                offer_id=o.id, kind=kind, file_path=str(path), model="claude-cli",
                fingerprint=fp,
            ))
    session.commit()

    return TailorResponse(
        cv_path=str(cv_out),
        cover_letter_path=str(cl_out),
        cover_letter_text=cl_text,
        cv_changed_paragraphs=n_changes,
    )


@app.post("/api/offers/{offer_id}/tailor", response_model=TailorResponse)
def tailor_for_offer(offer_id: int, session: Session = Depends(get_session)):
    o = session.get(Offer, offer_id)
    if not o:
        raise HTTPException(404, "Offer not found")
    settings = session.get(Settings, 1)
    if not settings or not settings.base_cv_path or not Path(settings.base_cv_path).exists():
        raise HTTPException(400, "No base CV uploaded. Upload it in Settings first.")
    try:
        # Serialize with the auto-tailor queue / background apply-tailoring so only
        # one claude.exe runs at a time (this endpoint runs in FastAPI's threadpool).
        with _tailor_run_lock:
            return _tailor_offer_impl(session, o, settings)
    except ClaudeCliError as e:
        raise HTTPException(502, f"Claude CLI generation failed: {e}")


# ---------- Auto-tailor queue ------------------------------------------------
# After each refresh, every active offer without tailored docs gets queued.
# One offer at a time (each = 2 Claude CLI calls), run in a worker thread.

import threading as _threading

_tailor_queue_lock = _threading.Lock()
# Serializes the actual generation work (2 Claude CLI calls per offer) across the
# auto-tailor queue AND on-demand background tailoring from apply-assist, so only
# one claude.exe runs at a time no matter which path triggered it.
_tailor_run_lock = _threading.Lock()
_tailor_state: dict = {
    "running": False, "total": 0, "done": 0, "failed": 0,
    "current": None, "last_error": None,
}


def _offers_needing_tailoring() -> list[int]:
    with Session(ENGINE) as session:
        offers = session.exec(select(Offer).where(Offer.is_active == True)).all()  # noqa: E712
        try:
            base_bytes = BASE_CV_JSON.read_bytes()
        except OSError:
            base_bytes = b""
        fp_by_offer = {o.id: _tailor_fingerprint(o, base_bytes) for o in offers}
        have_both: set[int] = set()
        kinds_by_offer: dict[int, set] = {}
        for d in session.exec(select(TailoredDocument)).all():
            # A document only counts if its file exists AND it was generated
            # from the current base CV + posting text (fingerprint match).
            if Path(d.file_path).exists() and d.fingerprint == fp_by_offer.get(d.offer_id):
                kinds_by_offer.setdefault(d.offer_id, set()).add(d.kind)
        for oid, kinds in kinds_by_offer.items():
            if DocumentKind.cv in kinds and DocumentKind.cover_letter in kinds:
                have_both.add(oid)
        return [o.id for o in offers if o.id not in have_both]


def _auto_tailor_worker(offer_ids: list[int]) -> None:
    global _tailor_state
    try:
        for oid in offer_ids:
            with Session(ENGINE) as session:
                o = session.get(Offer, oid)
                settings = session.get(Settings, 1)
                if not o or not o.is_active:
                    _tailor_state["done"] += 1
                    continue
                _tailor_state["current"] = f"{o.bank} — {o.role_title}"
                try:
                    with _tailor_run_lock:
                        _tailor_offer_impl(session, o, settings)
                    _tailor_state["done"] += 1
                except Exception as e:
                    logger.warning("Auto-tailor failed for offer %s: %s", oid, e)
                    _tailor_state["failed"] += 1
                    _tailor_state["last_error"] = str(e)[:300]
                    _tailor_state["done"] += 1
                    if is_usage_limit_error(e):
                        # Claude subscription window exhausted — every remaining
                        # call would fail too. Stop; a later /api/tailor/run
                        # (or the next refresh) picks up whatever is missing.
                        _tailor_state["last_error"] = (
                            "Claude usage limit reached — remaining offers postponed. "
                            "Click the Docs chip to retry once the window resets."
                        )
                        break
    finally:
        _tailor_state["running"] = False
        _tailor_state["current"] = None
        _tailor_queue_lock.release()


def start_auto_tailor() -> dict:
    """Kick off background tailoring for all active offers missing docs. Returns state."""
    global _tailor_state
    with Session(ENGINE) as session:
        settings = session.get(Settings, 1)
        if not settings or not settings.base_cv_path or not Path(settings.base_cv_path).exists():
            return {**_tailor_state, "skipped": "no base CV uploaded"}
    if not _tailor_queue_lock.acquire(blocking=False):
        return dict(_tailor_state)  # already running
    ids = _offers_needing_tailoring()
    if not ids:
        _tailor_queue_lock.release()
        _tailor_state = {**_tailor_state, "running": False, "total": 0, "done": 0, "failed": 0, "current": None}
        return dict(_tailor_state)
    _tailor_state = {
        "running": True, "total": len(ids), "done": 0, "failed": 0,
        "current": None, "last_error": None,
    }
    t = _threading.Thread(target=_auto_tailor_worker, args=(ids,), daemon=True)
    t.start()
    return dict(_tailor_state)


@app.post("/api/tailor/run")
def tailor_run_all():
    return start_auto_tailor()


@app.get("/api/tailor/status")
def tailor_status():
    return dict(_tailor_state)


@app.get("/api/offers/{offer_id}/document/{kind}")
def download_document(offer_id: int, kind: DocumentKind, session: Session = Depends(get_session)):
    doc = session.exec(
        select(TailoredDocument).where(
            (TailoredDocument.offer_id == offer_id) & (TailoredDocument.kind == kind)
        )
    ).first()
    if not doc or not Path(doc.file_path).exists():
        raise HTTPException(404, "Document not found")
    media = "application/pdf" if doc.file_path.lower().endswith(".pdf") else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(doc.file_path, filename=Path(doc.file_path).name, media_type=media)


# ---------- Notifications ---------------------------------------------------

@app.get("/api/notifications", response_model=list[NotificationOut])
def list_notifications(session: Session = Depends(get_session)):
    rows = session.exec(
        select(Notification).where(Notification.dismissed == False).order_by(Notification.created_at.desc())  # noqa: E712
    ).all()
    return [
        NotificationOut(id=n.id, kind=n.kind.value, created_at=n.created_at, offer_snapshot=n.offer_snapshot or {})
        for n in rows
    ]


@app.delete("/api/notifications/{notif_id}")
def dismiss_notification(notif_id: int, session: Session = Depends(get_session)):
    n = session.get(Notification, notif_id)
    if not n:
        raise HTTPException(404, "Notification not found")
    n.dismissed = True
    # Hard-delete the offer per user request — but ONLY when there is no
    # application history attached. An offer you applied to (or interviewed
    # at) stays in the DB as an inactive archive: deleting it would silently
    # erase funnel history and tailored documents, which is unrecoverable if
    # the "removed" notification was itself a scraper glitch.
    if n.offer_id:
        offer = session.get(Offer, n.offer_id)
        if offer:
            app_row = session.exec(
                select(Application).where(Application.offer_id == offer.id)).first()
            has_history = app_row is not None and app_row.status != ApplicationStatus.not_applied
            if has_history:
                offer.is_active = False
            else:
                if app_row:
                    session.delete(app_row)
                for d in session.exec(select(TailoredDocument).where(TailoredDocument.offer_id == offer.id)).all():
                    try:  # best-effort file cleanup; the row goes regardless
                        p = Path(d.file_path)
                        if p.is_file() and OFFERS_DIR in p.parents:
                            p.unlink()
                    except OSError:
                        pass
                    session.delete(d)
                session.delete(offer)
    session.add(n)
    session.commit()
    return {"ok": True}


# ---------- Settings --------------------------------------------------------

@app.get("/api/settings", response_model=SettingsOut)
def get_settings(session: Session = Depends(get_session)):
    s = session.get(Settings, 1)
    if not s:
        s = Settings(id=1)
        session.add(s)
        session.commit()
    return SettingsOut(
        has_api_key=bool(s.anthropic_api_key),
        anthropic_model=s.anthropic_model,
        base_cv_filename=Path(s.base_cv_path).name if s.base_cv_path else None,
        base_cover_letter_filename=Path(s.base_cover_letter_path).name if s.base_cover_letter_path else None,
        extra_documents=[
            ExtraDocOut(label=d.get("label") or Path(d["path"]).stem, filename=Path(d["path"]).name)
            for d in (s.extra_documents or [])
        ],
    )


@app.put("/api/settings", response_model=SettingsOut)
def update_settings(update: SettingsUpdate, session: Session = Depends(get_session)):
    s = session.get(Settings, 1)
    if not s:
        s = Settings(id=1)
        session.add(s)
    if update.anthropic_api_key is not None:
        s.anthropic_api_key = update.anthropic_api_key.strip() or None
    if update.anthropic_model:
        s.anthropic_model = update.anthropic_model
    session.commit()
    return get_settings(session)


@app.post("/api/settings/base-cv", response_model=SettingsOut)
async def upload_base_cv(file: UploadFile = File(...), session: Session = Depends(get_session)):
    if not file.filename or not file.filename.lower().endswith((".pdf", ".docx")):
        raise HTTPException(400, "Only PDF or DOCX accepted")
    target = CV_DIR / f"base_cv{Path(file.filename).suffix.lower()}"
    target.write_bytes(await file.read())
    s = session.get(Settings, 1)
    if not s:
        s = Settings(id=1)
        session.add(s)
    s.base_cv_path = str(target)
    session.commit()
    out = get_settings(session)
    # Tailoring renders from the structured base_cv.json, which this upload
    # does NOT regenerate — without this warning the user believes new uploads
    # flow into tailored documents, and they silently don't.
    out.warning = (
        "Heads up: tailored CVs are generated from storage/cvs/base_cv.json, "
        "which is NOT derived from this upload. If the CV content changed, "
        "base_cv.json must be re-transcribed for tailoring to pick it up."
    )
    return out


@app.post("/api/settings/base-cover-letter", response_model=SettingsOut)
async def upload_base_cover_letter(file: UploadFile = File(...), session: Session = Depends(get_session)):
    if not file.filename or not file.filename.lower().endswith((".pdf", ".docx")):
        raise HTTPException(400, "Only PDF or DOCX accepted")
    target = CV_DIR / f"base_cover_letter{Path(file.filename).suffix.lower()}"
    target.write_bytes(await file.read())
    s = session.get(Settings, 1)
    if not s:
        s = Settings(id=1)
        session.add(s)
    s.base_cover_letter_path = str(target)
    session.commit()
    return get_settings(session)


@app.post("/api/settings/extra-document", response_model=SettingsOut)
async def upload_extra_document(
    file: UploadFile = File(...),
    label: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    """Add a miscellaneous document (certificate, transcript, ...) that the
    assisted-apply engine offers to 'other/additional document' file inputs."""
    if not file.filename or not file.filename.lower().endswith((".pdf", ".docx", ".png", ".jpg", ".jpeg")):
        raise HTTPException(400, "Only PDF, DOCX, PNG or JPG accepted")
    target = DOCS_DIR / Path(file.filename).name
    target.write_bytes(await file.read())
    s = session.get(Settings, 1)
    if not s:
        s = Settings(id=1)
        session.add(s)
    docs = [d for d in (s.extra_documents or []) if d.get("path") != str(target)]
    docs.append({"label": (label or "").strip() or Path(file.filename).stem, "path": str(target)})
    s.extra_documents = docs  # reassign so the JSON column is marked dirty
    session.commit()
    return get_settings(session)


@app.delete("/api/settings/extra-document/{index}", response_model=SettingsOut)
def delete_extra_document(index: int, session: Session = Depends(get_session)):
    s = session.get(Settings, 1)
    docs = list(s.extra_documents or []) if s else []
    if not s or index < 0 or index >= len(docs):
        raise HTTPException(404, "No such document")
    removed = docs.pop(index)
    s.extra_documents = docs
    session.commit()
    try:
        p = Path(removed.get("path", ""))
        if p.is_file() and p.parent == DOCS_DIR:
            p.unlink()
    except OSError:
        pass
    return get_settings(session)


# ---------- Profile ---------------------------------------------------------

_PROFILE_FIELDS = list(ProfileOut.model_fields.keys())


@app.get("/api/profile", response_model=ProfileOut)
def get_profile(session: Session = Depends(get_session)):
    s = session.get(Settings, 1)
    if not s:
        s = Settings(id=1)
        session.add(s)
        session.commit()
    return ProfileOut(**{f: getattr(s, f) for f in _PROFILE_FIELDS})


@app.put("/api/profile", response_model=ProfileOut)
def update_profile(update: ProfileUpdate, session: Session = Depends(get_session)):
    s = session.get(Settings, 1)
    if not s:
        s = Settings(id=1)
        session.add(s)
    data = update.model_dump(exclude_unset=True)
    for k, v in data.items():
        if isinstance(v, str):
            v = v.strip() or None
        setattr(s, k, v)
    session.commit()
    return get_profile(session)


# ---------- Apply / Pre-fill assist -----------------------------------------

def _resolve_doc_path(session: Session, offer_id: int, kind: DocumentKind, source: str, settings: Settings) -> Optional[Path]:
    """Return the chosen document path for an offer, or None if missing."""
    if source == "original":
        if kind == DocumentKind.cv:
            return Path(settings.base_cv_path) if settings.base_cv_path else None
        return Path(settings.base_cover_letter_path) if settings.base_cover_letter_path else None
    # tailored
    doc = session.exec(
        select(TailoredDocument).where(
            (TailoredDocument.offer_id == offer_id) & (TailoredDocument.kind == kind)
        )
    ).first()
    if not doc:
        return None
    # Stale documents (generated from an older base CV or an older version of
    # the posting) are treated as missing so callers regenerate them.
    o = session.get(Offer, offer_id)
    if o is not None and doc.fingerprint != _tailor_fingerprint(o):
        return None
    p = Path(doc.file_path)
    return p if p.exists() else None


@app.get("/api/offers/{offer_id}/document/{kind}/{source}")
def download_document_source(
    offer_id: int, kind: DocumentKind, source: str,
    session: Session = Depends(get_session),
):
    if source not in {"tailored", "original"}:
        raise HTTPException(400, "source must be 'tailored' or 'original'")
    settings = session.get(Settings, 1)
    p = _resolve_doc_path(session, offer_id, kind, source, settings) if settings else None
    if not p or not p.exists():
        raise HTTPException(404, f"{kind.value} ({source}) not available")
    media = "application/pdf" if p.suffix.lower() == ".pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(p, filename=p.name, media_type=media)


@app.post("/api/offers/{offer_id}/apply-assist")
async def apply_assist(
    offer_id: int, body: ApplyAssistRequest,
    session: Session = Depends(get_session),
):
    """Open the offer's apply URL in a headful browser tab so user can attach the chosen
    documents and submit. We pre-prepare the document paths and return them so the
    frontend can show a checklist; the actual form-filling per bank is best-effort.
    """
    o = session.get(Offer, offer_id)
    if not o:
        raise HTTPException(404, "Offer not found")
    settings = session.get(Settings, 1)
    if not settings:
        raise HTTPException(400, "Settings not initialized")

    cv_path = _resolve_doc_path(session, offer_id, DocumentKind.cv, body.cv_source, settings)
    cl_path = _resolve_doc_path(session, offer_id, DocumentKind.cover_letter, body.cl_source, settings)

    needs_tailored = (body.cv_source == "tailored" and not cv_path) or \
                     (body.cl_source == "tailored" and not cl_path)
    if needs_tailored and (not settings.base_cv_path or not Path(settings.base_cv_path).exists()):
        raise HTTPException(400, "No base CV uploaded. Upload it in Settings first.")

    # Mutable doc paths the assist loop re-reads every pass. Seed with whatever
    # already exists; if the tailored docs are missing they're generated in a
    # BACKGROUND thread and dropped in here when ready — so the browser opens and
    # starts filling profile fields immediately instead of blocking ~1-2 min on
    # generation (that block was why some offers felt slow to launch and others
    # instant, depending purely on whether their docs happened to be pre-made).
    docs_holder = {
        "cv": str(cv_path) if cv_path else None,
        "cl": str(cl_path) if cl_path else None,
    }

    # Snapshot everything the background thread + assist task need before the
    # request's DB session closes.
    apply_url = o.apply_url
    cv_source, cl_source = body.cv_source, body.cl_source
    profile = {f: getattr(settings, f) for f in _PROFILE_FIELDS}
    # Extras (place of birth, ...) + the offer's country, so the engine can
    # answer right-to-work/sponsorship questions per jurisdiction.
    profile["extras_json"] = settings.extras_json or {}
    profile["offer_country"] = o.country
    extra_docs = [d for d in (settings.extra_documents or []) if d.get("path") and Path(d["path"]).exists()]

    # Mark as applied immediately (user reviews + submits in browser).
    app_row = session.exec(select(Application).where(Application.offer_id == offer_id)).first()
    if not app_row:
        app_row = Application(offer_id=offer_id)
        session.add(app_row)
    if app_row.status == ApplicationStatus.not_applied:
        app_row.status = ApplicationStatus.applied
        app_row.applied_at = datetime.utcnow()
    app_row.updated_at = datetime.utcnow()
    session.commit()
    app_status = app_row.status.value

    # Background tailoring (only when docs are missing): its own DB session, its
    # own claude.exe (serialized by _tailor_run_lock). When done it updates
    # docs_holder, and the already-running assist loop uploads the docs next pass.
    if needs_tailored:
        def _bg_tailor() -> None:
            try:
                with _tailor_run_lock:
                    with Session(ENGINE) as s2:
                        o2 = s2.get(Offer, offer_id)
                        st2 = s2.get(Settings, 1)
                        if not o2 or not st2:
                            return
                        _tailor_offer_impl(s2, o2, st2)
                        cvp = _resolve_doc_path(s2, offer_id, DocumentKind.cv, cv_source, st2)
                        clp = _resolve_doc_path(s2, offer_id, DocumentKind.cover_letter, cl_source, st2)
                        if cvp:
                            docs_holder["cv"] = str(cvp)
                        if clp:
                            docs_holder["cl"] = str(clp)
                logger.info("apply-assist: background tailoring ready for offer %s", offer_id)
            except Exception as e:
                # Surface the failure to the assist tab: the badge otherwise
                # promises "documents will attach automatically" forever.
                docs_holder["failed"] = str(e)[:200]
                logger.warning("apply-assist: background tailoring failed for offer %s: %s", offer_id, e)
        _threading.Thread(target=_bg_tailor, daemon=True).start()

    # Launch the assist browser NOW: visible window, persistent profile (ATS
    # logins survive between applications), auto-fills profile fields and uploads
    # the documents (as soon as they exist) on every page. User reviews + submits.
    import asyncio as _asyncio
    from apply_assist import run_assist

    async def _assist():
        try:
            await run_assist(
                apply_url, profile,
                docs_holder.get("cv"), docs_holder.get("cl"),
                extra_docs=extra_docs, docs_holder=docs_holder,
            )
        except Exception as e:
            logger.warning("apply-assist failed: %s", e)

    _asyncio.create_task(_assist())
    return {
        "apply_url": apply_url,
        "cv_path": docs_holder.get("cv"),
        "cl_path": docs_holder.get("cl"),
        "cv_source": cv_source,
        "cl_source": cl_source,
        "profile": profile,
        "application_status": app_status,
        "tailoring": needs_tailored,
    }


# ---------- Misc ------------------------------------------------------------

@app.get("/api/stats")
def stats(session: Session = Depends(get_session)):
    total = session.exec(select(func.count(Offer.id)).where(Offer.is_active == True)).one()  # noqa: E712
    by_status = {}
    for st in ApplicationStatus:
        count = session.exec(
            select(func.count(Application.id)).where(Application.status == st)
        ).one()
        by_status[st.value] = count
    by_bank = dict(session.exec(
        select(Offer.bank, func.count(Offer.id)).where(Offer.is_active == True).group_by(Offer.bank)  # noqa: E712
    ).all())
    notif_count = session.exec(
        select(func.count(Notification.id)).where(Notification.dismissed == False)  # noqa: E712
    ).one()
    return {
        "total_active_offers": total,
        "by_status": by_status,
        "by_bank": by_bank,
        "unread_notifications": notif_count,
    }


# ---------- Dashboard analytics ---------------------------------------------

_SENT = {
    ApplicationStatus.applied, ApplicationStatus.online_assessment,
    ApplicationStatus.interview, ApplicationStatus.offer, ApplicationStatus.rejected,
}
_RESPONDED = {  # anything past "applied" — a reply of any kind, incl. a rejection
    ApplicationStatus.online_assessment, ApplicationStatus.interview,
    ApplicationStatus.offer, ApplicationStatus.rejected,
}
_INTERVIEWED = {ApplicationStatus.interview, ApplicationStatus.offer}


@app.get("/api/dashboard")
def dashboard(session: Session = Depends(get_session)):
    """Everything the live desk dashboard needs in one call: the application
    funnel (sent / responses / interviews / offers with period deltas), a daily
    time series for the applications-over-time chart, the offer pipeline, the
    newest offers with their on-site age, and a merged recent-activity feed."""
    now = datetime.utcnow()
    today = now.date()
    week_ago = now - timedelta(days=7)
    month_start = datetime(now.year, now.month, 1)
    last_month_end = month_start - timedelta(seconds=1)
    last_month_start = datetime(last_month_end.year, last_month_end.month, 1)

    apps = session.exec(select(Application)).all()
    offers = session.exec(select(Offer).where(Offer.is_active == True)).all()  # noqa: E712
    app_by_offer = {a.offer_id: a for a in apps}

    sent = [a for a in apps if a.status in _SENT]
    responded = [a for a in apps if a.status in _RESPONDED]
    interviewed = [a for a in apps if a.status in _INTERVIEWED]
    offers_won = [a for a in apps if a.status == ApplicationStatus.offer]
    ghosted = [a for a in apps if a.status == ApplicationStatus.applied]

    def sent_in(lo: datetime, hi: Optional[datetime] = None) -> int:
        return sum(1 for a in sent if a.applied_at and a.applied_at >= lo
                   and (hi is None or a.applied_at <= hi))

    sent_today = sum(1 for a in sent if a.applied_at and a.applied_at.date() == today)
    sent_this_month = sent_in(month_start)
    sent_last_month = sent_in(last_month_start, last_month_end)
    interviews_this_week = sum(1 for a in interviewed if a.updated_at and a.updated_at >= week_ago)

    hit_rate = round(100 * len(responded) / len(sent), 1) if sent else 0.0
    ghost_rate = round(100 * len(ghosted) / len(sent), 1) if sent else 0.0

    # Consecutive-day streak of at least one application, ending today or yesterday.
    sent_days = {a.applied_at.date() for a in sent if a.applied_at}
    streak = 0
    cursor = today if today in sent_days else today - timedelta(days=1)
    while cursor in sent_days:
        streak += 1
        cursor -= timedelta(days=1)

    # 30-day daily series: applications sent, and offers discovered (both real,
    # single-series charts — offers keeps the view alive before the funnel fills).
    apps_series, offers_series = [], []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        apps_series.append({"date": d.isoformat(),
                            "count": sum(1 for a in sent if a.applied_at and a.applied_at.date() == d)})
        offers_series.append({"date": d.isoformat(),
                             "count": sum(1 for o in offers if o.first_seen_at and o.first_seen_at.date() == d)})

    # Active-offer pipeline by application status (an offer with no Application
    # row counts as not_applied).
    by_status = {st.value: 0 for st in ApplicationStatus}
    by_category: dict = {}
    for o in offers:
        a = app_by_offer.get(o.id)
        st = a.status.value if a else ApplicationStatus.not_applied.value
        by_status[st] = by_status.get(st, 0) + 1
        by_category[o.category.value] = by_category.get(o.category.value, 0) + 1

    new_today = sum(1 for o in offers if o.first_seen_at and o.first_seen_at.date() == today)
    new_this_week = sum(1 for o in offers if o.first_seen_at and o.first_seen_at >= week_ago)

    def age_days(o: Offer) -> int:
        return max(0, (now - o.first_seen_at).days) if o.first_seen_at else 0

    recent = sorted(offers, key=lambda o: o.first_seen_at or now, reverse=True)[:10]
    recent_offers = [{
        "id": o.id, "bank": o.bank, "role_title": o.role_title, "category": o.category.value,
        "city": o.city, "country": o.country, "start_date_raw": o.start_date_raw,
        "first_seen_at": o.first_seen_at.isoformat() if o.first_seen_at else None,
        "posted_at": o.posted_at.isoformat() if o.posted_at else None,
        "age_days": age_days(o),
        "application_status": (app_by_offer[o.id].status.value
                               if o.id in app_by_offer else ApplicationStatus.not_applied.value),
    } for o in recent]

    # Merged activity feed: application status changes + fresh offer discoveries.
    activity = []
    for a in apps:
        if a.status != ApplicationStatus.not_applied and a.updated_at:
            o = next((x for x in offers if x.id == a.offer_id), None)
            if o:
                activity.append({"kind": "app", "id": o.id, "bank": o.bank,
                                 "role_title": o.role_title, "label": a.status.value,
                                 "at": a.updated_at.isoformat()})
    for o in recent:
        activity.append({"kind": "offer_new", "id": o.id, "bank": o.bank,
                         "role_title": o.role_title, "label": "new",
                         "at": o.first_seen_at.isoformat() if o.first_seen_at else now.isoformat()})
    activity.sort(key=lambda x: x["at"], reverse=True)
    activity = activity[:14]

    return {
        "generated_at": now.isoformat(),
        "funnel": {
            "apps_sent": len(sent), "responses": len(responded),
            "interviews": len(interviewed), "offers": len(offers_won),
            "sent_today": sent_today, "sent_this_week": sent_in(week_ago),
            "sent_this_month": sent_this_month, "sent_last_month": sent_last_month,
            "interviews_this_week": interviews_this_week,
            "hit_rate": hit_rate, "ghost_rate": ghost_rate, "streak_days": streak,
        },
        "pipeline": {
            "active_offers": len(offers), "new_today": new_today, "new_this_week": new_this_week,
            "by_status": by_status, "by_category": by_category,
        },
        "apps_series": apps_series,
        "offers_series": offers_series,
        "recent_offers": recent_offers,
        "activity": activity,
    }


# ---------- Helpers ---------------------------------------------------------

import re as _re


def _slug(s: str) -> str:
    s = _re.sub(r"[^a-zA-Z0-9]+", "_", s or "").strip("_").lower()
    return s or "x"
