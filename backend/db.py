from datetime import datetime, date
from enum import Enum
from pathlib import Path
from typing import Optional

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel, create_engine, Session

DB_DIR = Path(__file__).parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "internships.db"
ENGINE = create_engine(f"sqlite:///{DB_PATH}", echo=False, connect_args={"check_same_thread": False})


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


class Application(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    offer_id: int = Field(foreign_key="offer.id", unique=True, index=True)
    status: ApplicationStatus = Field(default=ApplicationStatus.not_applied)
    notes: Optional[str] = None
    applied_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TailoredDocument(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    offer_id: int = Field(foreign_key="offer.id", index=True)
    kind: DocumentKind
    file_path: str
    model: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


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


def init_db() -> None:
    SQLModel.metadata.create_all(ENGINE)
    with ENGINE.begin() as conn:
        _migrate_settings(conn)
        _migrate_offer(conn)
    # Ensure single Settings row exists.
    with Session(ENGINE) as s:
        if not s.get(Settings, 1):
            s.add(Settings(id=1))
            s.commit()


def get_session():
    with Session(ENGINE) as session:
        yield session
