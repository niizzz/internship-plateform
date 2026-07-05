"""CV utilities: PDF→DOCX conversion, paragraph extraction, in-place text replacement."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from docx import Document
from pdf2docx import Converter

logger = logging.getLogger(__name__)


def pdf_to_docx(pdf_path: Path, docx_path: Path) -> Path:
    """Convert a PDF to a DOCX file. Returns the output path."""
    cv = Converter(str(pdf_path))
    try:
        cv.convert(str(docx_path))
    finally:
        cv.close()
    return docx_path


def ensure_docx(input_path: Path, work_dir: Path) -> Path:
    """If input is PDF, convert to DOCX in work_dir. If DOCX, copy it. Returns DOCX path."""
    work_dir.mkdir(parents=True, exist_ok=True)
    suffix = input_path.suffix.lower()
    out = work_dir / (input_path.stem + ".docx")
    if suffix == ".pdf":
        logger.info("Converting PDF to DOCX: %s -> %s", input_path, out)
        pdf_to_docx(input_path, out)
    elif suffix == ".docx":
        shutil.copy(input_path, out)
    else:
        raise ValueError(f"Unsupported CV format: {suffix}")
    return out


def extract_paragraphs(docx_path: Path) -> list[str]:
    """Return all non-empty paragraph texts (top-level + table cells), in document order."""
    doc = Document(str(docx_path))
    paragraphs: list[str] = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if text:
            paragraphs.append(text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    text = p.text.strip()
                    if text:
                        paragraphs.append(text)
    return paragraphs


def full_text(docx_path: Path) -> str:
    return "\n".join(extract_paragraphs(docx_path))


def apply_replacements(
    docx_path: Path,
    replacements: dict[int, str],
    out_path: Path,
) -> Path:
    """Apply paragraph replacements by index. Indexes match `extract_paragraphs` order.

    Preserves the formatting of the first run in each replaced paragraph; subsequent runs
    are removed. Tables are also walked.
    """
    doc = Document(str(docx_path))
    walker_idx = 0

    def _maybe_replace(p) -> None:
        nonlocal walker_idx
        text = p.text.strip()
        if not text:
            return
        if walker_idx in replacements:
            new_text = replacements[walker_idx]
            if p.runs:
                first = p.runs[0]
                first.text = new_text
                # Remove remaining runs (they would duplicate the old text).
                for r in p.runs[1:]:
                    r.text = ""
            else:
                p.text = new_text
        walker_idx += 1

    for p in doc.paragraphs:
        _maybe_replace(p)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    _maybe_replace(p)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def write_cover_letter_docx(text: str, out_path: Path) -> Path:
    """Write a plain cover letter to a fresh DOCX."""
    doc = Document()
    for para in text.split("\n\n"):
        para = para.strip()
        if para:
            doc.add_paragraph(para)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
