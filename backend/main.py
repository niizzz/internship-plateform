"""FastAPI app: offers, refresh, tailoring, notifications, settings."""
from __future__ import annotations

import asyncio
import sys
# Playwright needs subprocess support; on Windows we must use ProactorEventLoop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, BackgroundTasks
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
from cv_utils import ensure_docx, extract_paragraphs, full_text, apply_replacements, write_cover_letter_docx
from llm import tailor_cv_paragraphs, generate_cover_letter, ClaudeCliError, is_usage_limit_error

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).parent
STORAGE_DIR = BACKEND_DIR / "storage"
CV_DIR = STORAGE_DIR / "cvs"
OFFERS_DIR = STORAGE_DIR / "offers"
for d in (STORAGE_DIR, CV_DIR, OFFERS_DIR):
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


class SettingsOut(BaseModel):
    has_api_key: bool
    anthropic_model: str
    base_cv_filename: Optional[str]
    base_cover_letter_filename: Optional[str] = None


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
        if update.status != ApplicationStatus.not_applied and app_row.applied_at is None:
            app_row.applied_at = datetime.utcnow()
    if update.notes is not None:
        app_row.notes = update.notes
    app_row.updated_at = datetime.utcnow()
    session.add(app_row)
    session.commit()
    session.refresh(o)
    return _offer_to_out(session, o)


# ---------- Refresh ---------------------------------------------------------

async def _do_refresh():
    global _last_refresh
    async with _refresh_lock:
        _last_refresh = {"status": "running", "started_at": datetime.utcnow(), "finished_at": None, "result": None}
        try:
            scrapers = build_scrapers(headless=True)
            result = await refresh_all(scrapers)
            _last_refresh = {
                "status": "done",
                "started_at": _last_refresh["started_at"],
                "finished_at": datetime.utcnow(),
                "result": result,
            }
            # Queue CV/CL tailoring for any offer that doesn't have docs yet.
            try:
                start_auto_tailor()
            except Exception as e:
                logger.warning("Auto-tailor kickoff failed: %s", e)
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


def _tailor_offer_impl(session: Session, o: Offer, settings: Settings) -> TailorResponse:
    """Generate tailored CV + cover letter for one offer via the Claude CLI. Blocking."""
    base_cv = Path(settings.base_cv_path)
    work_dir = CV_DIR / "_work"
    base_docx = ensure_docx(base_cv, work_dir)
    paragraphs = extract_paragraphs(base_docx)
    cv_text = "\n".join(paragraphs)

    base_cl_text = None
    if settings.base_cover_letter_path and Path(settings.base_cover_letter_path).exists():
        try:
            base_cl_docx = ensure_docx(Path(settings.base_cover_letter_path), work_dir)
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

    replacements = tailor_cv_paragraphs(base_cv_paragraphs=paragraphs, offer=offer_dict)

    offer_folder = OFFERS_DIR / f"{o.id}_{_slug(o.bank)}_{_slug(o.role_title)[:40]}"
    cv_out = offer_folder / "tailored_cv.docx"
    apply_replacements(base_docx, replacements, cv_out)

    cl_text = generate_cover_letter(
        base_cv_text=cv_text, offer=offer_dict, base_cover_letter_text=base_cl_text,
    )
    cl_out = offer_folder / "cover_letter.docx"
    write_cover_letter_docx(cl_text, cl_out)

    # Upsert TailoredDocument rows.
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
        else:
            session.add(TailoredDocument(
                offer_id=o.id, kind=kind, file_path=str(path), model="claude-cli",
            ))
    session.commit()

    return TailorResponse(
        cv_path=str(cv_out),
        cover_letter_path=str(cl_out),
        cover_letter_text=cl_text,
        cv_changed_paragraphs=len(replacements),
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
        return _tailor_offer_impl(session, o, settings)
    except ClaudeCliError as e:
        raise HTTPException(502, f"Claude CLI generation failed: {e}")


# ---------- Auto-tailor queue ------------------------------------------------
# After each refresh, every active offer without tailored docs gets queued.
# One offer at a time (each = 2 Claude CLI calls), run in a worker thread.

import threading as _threading

_tailor_queue_lock = _threading.Lock()
_tailor_state: dict = {
    "running": False, "total": 0, "done": 0, "failed": 0,
    "current": None, "last_error": None,
}


def _offers_needing_tailoring() -> list[int]:
    with Session(ENGINE) as session:
        offers = session.exec(select(Offer).where(Offer.is_active == True)).all()  # noqa: E712
        have_both: set[int] = set()
        kinds_by_offer: dict[int, set] = {}
        for d in session.exec(select(TailoredDocument)).all():
            if Path(d.file_path).exists():
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
    return FileResponse(
        doc.file_path,
        filename=Path(doc.file_path).name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


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
    # Also hard-delete the offer (and dependent rows) per user request.
    if n.offer_id:
        offer = session.get(Offer, n.offer_id)
        if offer:
            for app_row in session.exec(select(Application).where(Application.offer_id == offer.id)).all():
                session.delete(app_row)
            for d in session.exec(select(TailoredDocument).where(TailoredDocument.offer_id == offer.id)).all():
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
    return get_settings(session)


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

    # Launch the assist browser: visible window, persistent profile (ATS logins
    # survive between applications), auto-fills profile fields and uploads the
    # chosen documents on every page of the flow. User reviews and submits.
    import asyncio as _asyncio
    from apply_assist import run_assist

    profile = {f: getattr(settings, f) for f in _PROFILE_FIELDS}

    async def _assist():
        try:
            await run_assist(
                o.apply_url, profile,
                str(cv_path) if cv_path else None,
                str(cl_path) if cl_path else None,
            )
        except Exception as e:
            logger.warning("apply-assist failed: %s", e)

    _asyncio.create_task(_assist())
    return {
        "apply_url": o.apply_url,
        "cv_path": str(cv_path) if cv_path else None,
        "cl_path": str(cl_path) if cl_path else None,
        "cv_source": body.cv_source,
        "cl_source": body.cl_source,
        "profile": profile,
        "application_status": app_row.status.value,
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


# ---------- Helpers ---------------------------------------------------------

import re as _re


def _slug(s: str) -> str:
    s = _re.sub(r"[^a-zA-Z0-9]+", "_", s or "").strip("_").lower()
    return s or "x"
