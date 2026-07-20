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


def docx_to_pdf(docx_path: Path, pdf_path: Path) -> Path:
    """Convert DOCX to PDF via Word COM (banks' ATSes want PDFs).

    Safe to call from worker threads (per-call COM init). Requires MS Word.
    """
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        try:
            doc = word.Documents.Open(str(docx_path.resolve()), ReadOnly=True)
            # 17 = wdExportFormatPDF
            doc.ExportAsFixedFormat(OutputFileName=str(pdf_path.resolve()), ExportFormat=17)
            doc.Close(False)
        finally:
            word.Quit()
    finally:
        pythoncom.CoUninitialize()
    return pdf_path


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


def _set_text_preserving_runs(p, new_text: str) -> None:
    """Replace a paragraph's text while keeping run-level formatting intact.

    Tailoring edits are keyword swaps, so old and new text usually share a long
    common prefix and suffix. Only the differing middle span is rewritten (it
    takes the formatting of the run it starts in); every character outside the
    span keeps its original run — bold/italic/font changes survive untouched.
    """
    old = p.text
    if not p.runs:
        p.text = new_text
        return
    # Longest common prefix / suffix (non-overlapping).
    a = 0
    while a < len(old) and a < len(new_text) and old[a] == new_text[a]:
        a += 1
    b = 0
    while b < len(old) - a and b < len(new_text) - a and old[len(old) - 1 - b] == new_text[len(new_text) - 1 - b]:
        b += 1
    start, end = a, len(old) - b          # changed span in old text
    mid_new = new_text[a:len(new_text) - b]
    pos = 0
    n = len(old)
    last = len(p.runs) - 1
    for i, r in enumerate(p.runs):
        rlen = len(r.text)
        rs, re_ = pos, pos + rlen
        pos = re_
        head = r.text[:max(0, min(rlen, start - rs))]
        tail = r.text[min(rlen, max(0, end - rs)):]
        is_anchor = (rs <= start < re_) or (start == n and re_ == n and i == last)
        r.text = head + (mid_new if is_anchor else "") + tail


def apply_replacements(
    docx_path: Path,
    replacements: dict[int, str],
    out_path: Path,
) -> Path:
    """Apply paragraph replacements by index. Indexes match `extract_paragraphs` order.

    Formatting is preserved: only the changed span of each paragraph is
    rewritten (see _set_text_preserving_runs). Tables are also walked.
    """
    doc = Document(str(docx_path))
    walker_idx = 0

    def _maybe_replace(p) -> None:
        nonlocal walker_idx
        text = p.text.strip()
        if not text:
            return
        if walker_idx in replacements:
            _set_text_preserving_runs(p, replacements[walker_idx])
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
