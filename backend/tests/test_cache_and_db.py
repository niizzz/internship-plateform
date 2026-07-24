"""Tailored-doc fingerprint invalidation + SQLite durability settings.

Run:  .venv/Scripts/python.exe -m tests.test_cache_and_db   (from backend/)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import SQLModel, Session, create_engine  # noqa: E402

import db as dbmod  # noqa: E402
from db import Offer, TailoredDocument, DocumentKind, Settings  # noqa: E402
import main as mainmod  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, got=None) -> None:
    global PASS, FAIL
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + ("" if cond else f"   got={got!r}"))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp())
    eng = create_engine(f"sqlite:///{tmpdir / 't.db'}")
    SQLModel.metadata.create_all(eng)
    mainmod.ENGINE = eng
    dbmod.ENGINE = eng

    # Fake base CV + tailored files.
    base_json = tmpdir / "base_cv.json"
    base_json.write_text('{"name": "A", "sections": []}', encoding="utf-8")
    mainmod.BASE_CV_JSON = base_json
    cv_pdf = tmpdir / "cv.pdf"
    cl_pdf = tmpdir / "cl.pdf"
    cv_pdf.write_bytes(b"%PDF-cv")
    cl_pdf.write_bytes(b"%PDF-cl")

    with Session(eng) as s:
        o = Offer(bank="X", external_id="1", role_title="S&T Intern",
                  location="London", country="United Kingdom",
                  description="FICC desk internship", apply_url="https://x/1")
        s.add(o)
        s.add(Settings(id=1))
        s.commit()
        s.refresh(o)
        oid = o.id
        fp = mainmod._tailor_fingerprint(o)
        s.add(TailoredDocument(offer_id=oid, kind=DocumentKind.cv,
                               file_path=str(cv_pdf), fingerprint=fp))
        s.add(TailoredDocument(offer_id=oid, kind=DocumentKind.cover_letter,
                               file_path=str(cl_pdf), fingerprint=fp))
        s.commit()

    check("fresh fingerprint: offer not queued for tailoring",
          oid not in mainmod._offers_needing_tailoring())

    with Session(eng) as s:
        settings = s.get(Settings, 1)
        p = mainmod._resolve_doc_path(s, oid, DocumentKind.cv, "tailored", settings)
        check("fresh fingerprint: tailored CV resolves", p == cv_pdf, p)

    # Change the structured base CV -> every tailored doc is stale.
    base_json.write_text('{"name": "A", "sections": [{"type":"text","heading":"PROFILE","text":"new"}]}',
                         encoding="utf-8")
    check("changed base CV: offer queued for re-tailoring",
          oid in mainmod._offers_needing_tailoring())
    with Session(eng) as s:
        settings = s.get(Settings, 1)
        p = mainmod._resolve_doc_path(s, oid, DocumentKind.cv, "tailored", settings)
        check("changed base CV: stale tailored CV no longer resolves", p is None, p)

    # --- SQLite durability ---------------------------------------------------
    eng2 = create_engine(f"sqlite:///{tmpdir / 'wal.db'}")
    dbmod.configure_engine(eng2)
    SQLModel.metadata.create_all(eng2)
    with eng2.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").fetchone()[0]
        busy = conn.exec_driver_sql("PRAGMA busy_timeout").fetchone()[0]
    check("journal_mode is WAL", mode.lower() == "wal", mode)
    check("busy_timeout >= 15s", int(busy) >= 15000, busy)

    print(f"\nTOTAL: {PASS}/{PASS + FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
