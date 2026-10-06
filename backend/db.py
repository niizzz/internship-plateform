from datetime import datetime, date
from enum import Enum
from pathlib import Path
from typing import Optional

from sqlalchemy import Column, JSON, event
from sqlmodel import Field, SQLModel, create_engine, Session

DB_DIR = Path(__file__).parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "internships.db"


def configure_engine(engine) -> None:
    """SQLite durability for a multi-threaded writer mix (refresh persists,
    tailor worker, apply-assist background threads, API threadpool): WAL lets
    readers proceed during a write, and a generous busy_timeout turns "database
    is locked" hard failures into short waits."""
    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


ENGINE = create_engine(
    f"sqlite:///{DB_PATH}", echo=False,
    connect_args={"check_same_thread": False, "timeout": 30},
)
configure_engine(ENGINE)


class Category(str, Enum):
    sales = "sales"
    trading = "trading"
    structuring = "structuring"
    markets = "markets"  # generic S&T umbrella when the posting doesn't separate


class ProgramType(str, Enum):
    summer_internship = "summer_internship"
    off_cycle = "off_cycle"
    industrial_placement = "industrial_placement"
    graduate = "graduate"
    spring_week = "spring_week"
    other = "other"


class ApplicationStatus(str, Enum):
    not_applied = "not_applied"
    applied = "applied"
    online_assessment = "online_assessment"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"


class DocumentKind(str, Enum):
    cv = "cv"
    cover_letter = "cover_letter"


class NotificationKind(str, Enum):
    offer_removed = "offer_removed"


class Offer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bank: str = Field(index=True)
    external_id: str = Field(index=True)  # bank-specific job id for dedupe
    role_title: str
    category: Category = Field(default=Category.markets)
    location: str
    country: str = Field(index=True)
    city: Optional[str] = None
    start_date_raw: Optional[str] = None
    start_date_parsed: Optional[date] = None
    posted_at: Optional[datetime] = None  # when the bank published the posting
    duration: Optional[str] = None
    program_type: ProgramType = Field(default=ProgramType.other)
    description: Optional[str] = None
    apply_url: str
    source_url: Optional[str] = None
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = Field(default=True, index=True)
    # Set once the offer went out in a new-items email digest (notifier.py).
    emailed_at: Optional[datetime] = None


class EventRegStatus(str, Enum):
    not_registered = "not_registered"
    registered = "registered"
    attended = "attended"
    skipped = "skipped"


class RecruitingEvent(SQLModel, table=True):
    """A recruiting / networking event a bank publishes (insight evenings,
    desk info sessions, trading challenges, campus presentations). Scraped by
    scrapers/events.py, or added by hand (`manual`). Registration itself is a
    link out to the bank's own form: the platform never registers for you."""
    id: Optional[int] = Field(default=None, primary_key=True)
    bank: str = Field(index=True)
    external_id: str = Field(index=True)
    title: str
    event_type: str = "event"          # insight | info_session | networking | workshop | competition | event
    relevance: str = Field(default="general", index=True)  # markets | general | other
    division: Optional[str] = None
    is_virtual: bool = False
    location: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    school: Optional[str] = None       # set when the event is for one university's students
    # Wall-clock time AT THE EVENT (naive), labelled by `timezone` ("BST",
    # "Europe/London", ...). Sources disagree on zones and several give none,
    # so converting to UTC would invent precision; display is "as published".
    starts_at: Optional[datetime] = Field(default=None, index=True)
    ends_at: Optional[datetime] = None
    all_day: bool = False              # source gives a date but no time
    timezone: Optional[str] = None
    registration_deadline: Optional[datetime] = None
    # False when the source says sign-up has closed (JPM drops the signup link).
    registration_open: bool = True
    register_url: str                  # sign-up form, else the bank's events page
    source_url: Optional[str] = None
    description: Optional[str] = None
    manual: bool = False
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = Field(default=True, index=True)
    hidden: bool = False               # user dismissed it
    reg_status: EventRegStatus = Field(default=EventRegStatus.not_registered)
    notes: Optional[str] = None
    emailed_at: Optional[datetime] = None


class Application(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    offer_id: int = Field(foreign_key="offer.id", unique=True, index=True)
    status: ApplicationStatus = Field(default=ApplicationStatus.not_applied)
    notes: Optional[str] = None
    applied_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ApplicationEvent(SQLModel, table=True):
    """One row per application status transition — the pipeline timeline.

    Application stores only the CURRENT status, so "when did this become an
    interview?" and "how long has it sat in OA?" were unanswerable; updated_at
    is overwritten by every notes edit, so it cannot stand in. Events are
    append-only and never rewritten.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    offer_id: int = Field(foreign_key="offer.id", index=True)
    from_status: Optional[ApplicationStatus] = None
    to_status: ApplicationStatus
    at: datetime = Field(default_factory=datetime.utcnow, index=True)


class TailoredDocument(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    offer_id: int = Field(foreign_key="offer.id", index=True)
    kind: DocumentKind
    file_path: str
    model: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Content fingerprint of the inputs this document was generated from
    # (base_cv.json + offer title/description). A mismatch means the document
    # is stale and must be regenerated — see main._tailor_fingerprint.
    fingerprint: Optional[str] = None


class ScraperState(SQLModel, table=True):
    """Per-bank scrape bookkeeping. `zero_streak` counts consecutive successful
    scrapes that returned zero kept offers while the bank still had active rows
    — used to distinguish "the bank really pulled everything" from "the scraper
    silently broke" before deactivating listings."""
    bank: str = Field(primary_key=True)
    zero_streak: int = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: NotificationKind
    offer_id: Optional[int] = Field(default=None, foreign_key="offer.id")
    offer_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    dismissed: bool = Field(default=False, index=True)


class Settings(SQLModel, table=True):
    id: Optional[int] = Field(default=1, primary_key=True)
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-opus-4-7"
    base_cv_path: Optional[str] = None
    base_cv_text: Optional[str] = None  # cached extracted text
    base_cover_letter_path: Optional[str] = None  # user's "original" cover letter template
    # --- Profile (pre-fill assist) ----------------------------------------
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    nationality: Optional[str] = None
    date_of_birth: Optional[str] = None  # stored as ISO string for flexibility
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
    work_authorization: Optional[str] = None  # e.g. "UK Citizen", "Tier 4 visa"
    languages: Optional[str] = None  # free-form: "French (native), English (C2)"
    extras_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # Miscellaneous documents (certificates, transcripts, ...) offered to
    # "other document" file inputs during assisted apply: [{"label", "path"}]
    extra_documents: list = Field(default_factory=list, sa_column=Column(JSON))
    # --- Automation: daily refresh + new-item email digest ----------------
    auto_refresh: bool = True
    auto_refresh_hours: int = 24
    email_notifications: bool = False
    notify_email: Optional[str] = None   # the ONLY recipient notifier.py ever sends to
    smtp_host: Optional[str] = "smtp.gmail.com"
    smtp_port: Optional[int] = 465
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None  # app password; never returned by the API
    # Items first seen before this are never emailed (no backlog blast when
    # notifications are switched on).
    notify_since: Optional[datetime] = None


class ContactType(str, Enum):
    """Who the person is relative to the candidate — drives how a draft opens.
    Ordered by observed reply rate: alumni and current interns answer, seniors
    mostly do not."""
    alumni = "alumni"        # same school — the warmest opener available
    intern = "intern"        # doing/just did an internship at the target firm
    junior = "junior"        # analyst / associate on the desk
    recruiter = "recruiter"  # campus recruiting / HR
    senior = "senior"        # VP / MD / desk head


class OutreachStatus(str, Enum):
    to_contact = "to_contact"
    sent = "sent"
    replied = "replied"
    call_booked = "call_booked"
    referred = "referred"      # they put your name forward — the win condition
    closed = "closed"          # dead: declined, or gone silent past the cadence


class Channel(str, Enum):
    """Where the message goes. Changes the format, not just the transport: an
    email gets a subject line and a little more room, a LinkedIn note gets 300
    characters and no greeting."""
    linkedin = "linkedin"
    email = "email"


class AskType(str, Enum):
    """What the message actually asks for. Orthogonal to format, and the single
    biggest driver of whether it lands."""
    chat = "chat"          # informational: their desk, their path, their old role
    referral = "referral"  # put my CV forward for a specific role


class MessageKind(str, Enum):
    connection_note = "connection_note"  # attached to a connect request, 300 chars
    dm = "dm"                            # message once connected, or InMail
    followup = "followup"
    thank_you = "thank_you"


# LinkedIn truncates a connection-request note at 300 characters. Drafts that
# overrun are regenerated shorter rather than silently cut mid-sentence.
CONNECTION_NOTE_MAX = 300


class Contact(SQLModel, table=True):
    """One person to reach out to. Deliberately NOT scraped: LinkedIn's User
    Agreement forbids automated access, so rows are entered by hand (or from a
    profile the user is already looking at). The automation here is drafting,
    tracking and follow-up timing — never sending."""
    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str
    bank: str = Field(index=True)
    role_title: Optional[str] = None
    desk: Optional[str] = None           # "Structured Equity Derivatives", "FICC"
    location: Optional[str] = None
    contact_type: ContactType = Field(default=ContactType.junior, index=True)
    linkedin_url: Optional[str] = None
    # Confirmed address: shared by the person (LinkedIn export) or typed by the
    # user. This is the ONLY field the send links and channel:auto ever use.
    email: Optional[str] = None
    # Derived from the employer's mail format. A GUESS: never sent to on its
    # own, the user promotes it into `email` explicitly if they trust it.
    email_guess: Optional[str] = None
    email_guess_confidence: Optional[str] = None   # known | likely
    # True when they are already a 1st-degree connection (everyone promoted
    # from the LinkedIn import). You cannot send a connection REQUEST to
    # someone you are already connected to, so this drives the default message
    # kind and hides the connection-note option for them.
    is_connection: bool = Field(default=False)
    school: Optional[str] = None         # set when the tie is alumni
    grad_year: Optional[str] = None
    # Free text: the honest, specific reason to write to THIS person. The single
    # biggest driver of reply rate, and the one thing a model cannot invent.
    shared_context: Optional[str] = None
    offer_id: Optional[int] = Field(default=None, foreign_key="offer.id", index=True)
    status: OutreachStatus = Field(default=OutreachStatus.to_contact, index=True)
    notes: Optional[str] = None
    last_sent_at: Optional[datetime] = None
    next_followup_at: Optional[datetime] = Field(default=None, index=True)
    replied_at: Optional[datetime] = None
    followup_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OutreachMessage(SQLModel, table=True):
    """A drafted message. Rows are created by the drafter and edited by the user;
    sent_at is set only when the user confirms they actually sent it by hand."""
    id: Optional[int] = Field(default=None, primary_key=True)
    contact_id: int = Field(foreign_key="contact.id", index=True)
    kind: MessageKind = Field(default=MessageKind.connection_note)
    channel: Channel = Field(default=Channel.linkedin)
    ask_type: AskType = Field(default=AskType.chat)
    subject: Optional[str] = None   # email only; LinkedIn messages have none
    body: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: Optional[datetime] = None
    edited: bool = Field(default=False)  # user changed the draft before sending


class ConnectionSuggestion(SQLModel, table=True):
    """A person from the user's own LinkedIn data export who looks worth writing
    to. Populated by linkedin_import from Connections.csv (LinkedIn's official
    "get a copy of your data"), never by scraping.

    Only rows that matched a known finance employer AND a front-office markets
    role are stored: the rest of the network is irrelevant to this hunt, and
    keeping third-party contact details that serve no purpose is not worth the
    privacy cost. Re-importing re-derives everything, so nothing is lost.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str
    linkedin_url: Optional[str] = Field(default=None, index=True)
    email: Optional[str] = None                    # shared by them, in the export
    # Derived from the employer's mail format. A GUESS: never sent to on its
    # own, the user promotes it into `email` explicitly if they trust it.
    email_guess: Optional[str] = None
    email_guess_confidence: Optional[str] = None   # known | likely
    company_raw: str                 # employer exactly as the export spells it
    bank: str = Field(index=True)    # canonical name it resolved to
    employer_tier: str               # target_bank | other_ib | market_maker
    position: str
    category: str                    # sales | trading | structuring | markets
    seniority: str                   # maps onto ContactType
    score: int = Field(default=0, index=True)
    reasons: list = Field(default_factory=list, sa_column=Column(JSON))
    connected_on: Optional[str] = None
    dismissed: bool = Field(default=False, index=True)
    # Set once turned into a Contact, so a person is never suggested twice.
    contact_id: Optional[int] = Field(default=None, foreign_key="contact.id")
    imported_at: datetime = Field(default_factory=datetime.utcnow)


# Columns added after the initial schema. SQLModel.metadata.create_all() will only create
# missing tables, not migrate existing ones — so on existing DBs we ALTER manually.
_SETTINGS_NEW_COLUMNS = [
    ("base_cover_letter_path", "TEXT"),
    ("full_name", "TEXT"),
    ("email", "TEXT"),
    ("phone", "TEXT"),
    ("gender", "TEXT"),
    ("nationality", "TEXT"),
    ("date_of_birth", "TEXT"),
    ("address", "TEXT"),
    ("city", "TEXT"),
    ("country", "TEXT"),
    ("postcode", "TEXT"),
    ("linkedin_url", "TEXT"),
    ("github_url", "TEXT"),
    ("portfolio_url", "TEXT"),
    ("university", "TEXT"),
    ("degree", "TEXT"),
    ("graduation_year", "TEXT"),
    ("gpa", "TEXT"),
    ("work_authorization", "TEXT"),
    ("languages", "TEXT"),
    ("extras_json", "JSON"),
    ("extra_documents", "JSON"),
    ("auto_refresh", "BOOLEAN DEFAULT 1"),
    ("auto_refresh_hours", "INTEGER DEFAULT 24"),
    ("email_notifications", "BOOLEAN DEFAULT 0"),
    ("notify_email", "TEXT"),
    ("smtp_host", "TEXT DEFAULT 'smtp.gmail.com'"),
    ("smtp_port", "INTEGER DEFAULT 465"),
    ("smtp_user", "TEXT"),
    ("smtp_password", "TEXT"),
    ("notify_since", "DATETIME"),
]


def _migrate_settings(conn) -> None:
    cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(settings)").fetchall()}
    for name, sqltype in _SETTINGS_NEW_COLUMNS:
        if name not in cols:
            conn.exec_driver_sql(f'ALTER TABLE settings ADD COLUMN "{name}" {sqltype}')


def _migrate_offer(conn) -> None:
    cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(offer)").fetchall()}
    if "posted_at" not in cols:
        conn.exec_driver_sql('ALTER TABLE offer ADD COLUMN "posted_at" DATETIME')
    if "emailed_at" not in cols:
        conn.exec_driver_sql('ALTER TABLE offer ADD COLUMN "emailed_at" DATETIME')


def _migrate_tailoreddocument(conn) -> None:
    cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(tailoreddocument)").fetchall()}
    if "fingerprint" not in cols:
        conn.exec_driver_sql('ALTER TABLE tailoreddocument ADD COLUMN "fingerprint" TEXT')


def _migrate_email_guesses(conn) -> None:
    """Guessed work addresses, added after both tables shipped."""
    for table in ("contact", "connectionsuggestion"):
        cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
        for name in ("email_guess", "email_guess_confidence"):
            if name not in cols:
                conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN "{name}" TEXT')
    cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(contact)").fetchall()}
    if "is_connection" not in cols:
        conn.exec_driver_sql(
            'ALTER TABLE contact ADD COLUMN "is_connection" BOOLEAN DEFAULT 0')
        # Rows already promoted from the import are, by definition, connections.
        conn.exec_driver_sql(
            "UPDATE contact SET is_connection=1 "
            "WHERE shared_context LIKE '%first-degree LinkedIn connection%'")


def _migrate_outreachmessage(conn) -> None:
    cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(outreachmessage)").fetchall()}
    # Defaults match the pre-channel behaviour: everything drafted before this
    # was a LinkedIn message asking for a chat.
    for name, sqltype, default in (
        ("channel", "TEXT", "'linkedin'"),
        ("ask_type", "TEXT", "'chat'"),
        ("subject", "TEXT", "NULL"),
    ):
        if name not in cols:
            conn.exec_driver_sql(
                f'ALTER TABLE outreachmessage ADD COLUMN "{name}" {sqltype} DEFAULT {default}')


def _data_fixes(conn) -> None:
    # A not_applied row must not carry an applied_at: stale timestamps (left by
    # the July status reset) would resurface as the application date if the
    # offer is applied to later, corrupting the dashboard series.
    conn.exec_driver_sql(
        "UPDATE application SET applied_at=NULL WHERE status='not_applied' AND applied_at IS NOT NULL")
    # One row per posting per bank — persist() also guards this in-batch, but a
    # unique index makes the invariant durable.
    try:
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_offer_bank_external ON offer(bank, external_id)")
    except Exception:
        # Pre-existing duplicate rows would make this fail; keep starting up.
        import logging
        logging.getLogger(__name__).warning(
            "could not create unique offer index (duplicate rows present?)")
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_event_bank_external "
        "ON recruitingevent(bank, external_id)")


def _backfill_application_events(conn) -> None:
    """Seed one event for applications that predate the events table.

    Their true transition history is unrecoverable, so record a single entry
    into the current status, dated from applied_at (falling back to
    updated_at). Only runs for applications with no events at all, so it can
    never duplicate or overwrite real history.
    """
    conn.exec_driver_sql("""
        INSERT INTO applicationevent (offer_id, from_status, to_status, at)
        SELECT a.offer_id, NULL, a.status, COALESCE(a.applied_at, a.updated_at)
        FROM application a
        WHERE a.status != 'not_applied'
          AND NOT EXISTS (SELECT 1 FROM applicationevent e WHERE e.offer_id = a.offer_id)
    """)


def init_db() -> None:
    SQLModel.metadata.create_all(ENGINE)
    with ENGINE.begin() as conn:
        _migrate_settings(conn)
        _migrate_offer(conn)
        _migrate_tailoreddocument(conn)
        _migrate_outreachmessage(conn)
        _migrate_email_guesses(conn)
        _data_fixes(conn)
        _backfill_application_events(conn)
    # Ensure single Settings row exists.
    with Session(ENGINE) as s:
        if not s.get(Settings, 1):
            s.add(Settings(id=1))
            s.commit()


def get_session():
    with Session(ENGINE) as session:
        yield session
