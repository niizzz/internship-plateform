"""Render a structured CV (base_cv.json schema) to a pixel-clean PDF.

The CV is described as data (name, contact, sections) and drawn through a fixed
HTML/CSS template rendered by headless Chromium (Playwright). This guarantees
identical formatting on every generation — the right-aligned dates never wrap,
the navy section bars are always drawn — because tailoring only swaps text, it
never touches layout. Replaces the old PDF -> pdf2docx -> edit -> Word/PDF
round-trip, which broke tab stops and dropped the heading borders.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

# Navy used for section headings + separator rules (matches the base CV).
NAVY = "#1F3864"

CSS = f"""
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  .cv {{
    font-family: 'Times New Roman', Times, serif;
    color: #000; font-size: 9.6pt; line-height: 1.12;
  }}
  .name {{
    text-align: center; font-weight: bold; font-size: 16pt;
    letter-spacing: .3px; margin: 0;
  }}
  .contact {{ text-align: center; font-size: 9.3pt; margin-top: 1pt; }}
  .contact a {{ color: #000; text-decoration: underline; }}
  .sep {{ display: inline-block; padding: 0 3pt; }}

  .section {{ margin-top: 5pt; }}
  .heading {{
    color: {NAVY}; font-weight: bold; font-size: 10.5pt; text-transform: uppercase;
    letter-spacing: .3px; border-bottom: 1.2pt solid {NAVY};
    padding-bottom: .5pt; margin-bottom: 2.5pt;
  }}

  .entry {{ margin-bottom: 3.5pt; }}
  .entry:last-child {{ margin-bottom: 0; }}
  .entry-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 10pt; }}
  .entry-head .left {{ font-weight: bold; font-size: 9.6pt; }}
  .entry-head .right {{ font-style: italic; font-size: 9.3pt; white-space: nowrap; flex-shrink: 0; }}
  .subtitle {{ font-style: italic; font-size: 9.3pt; margin-top: .3pt; }}
  .sub {{ font-size: 9.3pt; margin-top: .3pt; }}

  ul {{ margin: 1pt 0 0 0; padding-left: 14pt; }}
  li {{ font-size: 9.3pt; margin-bottom: .6pt; }}

  .profile-text {{ font-size: 9.3pt; margin: 0; }}
  .kv {{ font-size: 9.3pt; margin-bottom: 1pt; }}
  .kv:last-child {{ margin-bottom: 0; }}
  .kv-label {{ font-weight: bold; }}
"""


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _contact_line(cv: dict) -> str:
    parts = [_esc(c) for c in (cv.get("contact") or []) if c]
    if cv.get("linkedin_url"):
        parts.append(f'<a href="{_esc(cv["linkedin_url"])}">{_esc(cv.get("linkedin_label") or "LinkedIn")}</a>')
    return '<span class="sep">•</span>'.join(parts)


def _entry_html(e: dict) -> str:
    out = ['<div class="entry">']
    left, right = _esc(e.get("left")), _esc(e.get("right"))
    out.append(f'<div class="entry-head"><span class="left">{left}</span>'
               f'<span class="right">{right}</span></div>')
    if e.get("subtitle"):
        out.append(f'<div class="subtitle">{_esc(e["subtitle"])}</div>')
    if e.get("sub"):
        out.append(f'<div class="sub">{_esc(e["sub"])}</div>')
    bullets = [b for b in (e.get("bullets") or []) if b]
    if bullets:
        out.append("<ul>" + "".join(f"<li>{_esc(b)}</li>" for b in bullets) + "</ul>")
    out.append("</div>")
    return "".join(out)


def _section_html(s: dict) -> str:
    typ = s.get("type")
    body = ""
    if typ == "text":
        body = f'<p class="profile-text">{_esc(s.get("text"))}</p>'
    elif typ == "entries":
        body = "".join(_entry_html(e) for e in (s.get("entries") or []))
    elif typ == "keyvals":
        body = "".join(
            f'<div class="kv"><span class="kv-label">{_esc(i.get("label"))}:</span> {_esc(i.get("value"))}</div>'
            for i in (s.get("items") or []))
    elif typ == "bullets":
        bl = [b for b in (s.get("bullets") or []) if b]
        body = "<ul>" + "".join(f"<li>{_esc(b)}</li>" for b in bl) + "</ul>"
    return (f'<div class="section"><div class="heading">{_esc(s.get("heading"))}</div>{body}</div>')


_FONT_SIZE_RE = re.compile(r"font-size:\s*([0-9.]+)pt")


def _scaled_css(fs: float) -> str:
    """Scale ONLY font sizes by `fs`; leave page margins / bullet indent / borders
    at their fixed pt values. Shrinking the font while the page width stays fixed
    is what actually reduces line wrapping (CSS `zoom` scales width too, so it
    leaves the text-to-width ratio — and wrapping — unchanged)."""
    if fs == 1.0:
        return CSS
    return _FONT_SIZE_RE.sub(
        lambda m: f"font-size: {round(float(m.group(1)) * fs, 2)}pt", CSS)


def render_html(cv: dict, fs: float = 1.0) -> str:
    sections = "".join(_section_html(s) for s in (cv.get("sections") or []))
    # `fs` shrinks only the fonts (page width fixed) as an auto-fit safety net so
    # every experience bullet stays on one line and the CV stays one page — see
    # render_cv_pdf.
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><style>{_scaled_css(fs)}</style></head>"
        f'<body><div class="cv">'
        f'<div class="name">{_esc(cv.get("name"))}</div>'
        f'<div class="contact">{_contact_line(cv)}</div>'
        f"{sections}</div></body></html>"
    )


_MARGIN = {"top": "0.45in", "bottom": "0.35in", "left": "0.5in", "right": "0.5in"}


def _expected_li(cv: dict) -> int:
    """Number of <li> bullets the CV will render (experience + interests)."""
    n = 0
    for s in (cv.get("sections") or []):
        if s.get("type") == "entries":
            for e in (s.get("entries") or []):
                n += len([b for b in (e.get("bullets") or []) if b])
        elif s.get("type") == "bullets":
            n += len([b for b in (s.get("bullets") or []) if b])
    return n


def _bullet_wrap_count(pdf_path: Path, expected_li: int) -> int:
    """How many bullets wrapped onto a 2nd line in the *rendered PDF*.

    Measured on the real PDF (fitz) because on-screen widths don't match A4
    print. Bullet lines sit in a narrow left-indent band (a few pt right of the
    section text's left edge); the centered name/contact and right-aligned dates
    fall outside it. Extra lines in that band beyond the known bullet count are
    wraps. Returns 0 if fitz is unavailable (never blocks rendering)."""
    try:
        import fitz
    except Exception:
        return 0
    doc = fitz.open(str(pdf_path))
    band = 0
    try:
        for page in doc:
            pw = page.rect.width
            xs = []
            for blk in page.get_text("dict")["blocks"]:
                for ln in blk.get("lines", []):
                    if "".join(sp["text"] for sp in ln["spans"]).strip():
                        xs.append(ln["bbox"][0])
            left = [x for x in xs if x < pw * 0.5]
            if not left:
                continue
            base_x = min(left)  # section text left edge (headings, subtitles)
            band += sum(1 for x in left if base_x + 6 < x < base_x + 40)
    finally:
        doc.close()
    return max(0, band - expected_li)


def render_cv_pdf(cv: dict, out_pdf: Path) -> Path:
    """Render structured CV data to a one-page PDF via headless Chromium (sync
    Playwright, safe to call from a worker thread).

    Auto-fit shrinks the FONT (page width fixed) in small steps until the CV both
    (a) fits one page and (b) has no experience/interest bullet wrapping onto a
    second line. Font-scaling — not CSS zoom — is what reduces wrapping, because
    zoom scales the page width along with the text and so leaves the wrap point
    unchanged."""
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    expected_li = _expected_li(cv)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page()
            fs = 1.0
            for _ in range(10):
                page.set_content(render_html(cv, fs), wait_until="load")
                page.pdf(path=str(out_pdf), format="A4", print_background=True,
                         prefer_css_page_size=False, margin=_MARGIN)
                pages = len(PdfReader(str(out_pdf)).pages)
                # Only bother measuring wraps once it fits one page; a 2-page
                # layout is shrunk regardless.
                wraps = _bullet_wrap_count(out_pdf, expected_li) if pages <= 1 else 1
                if pages <= 1 and wraps == 0:
                    break
                fs -= 0.03
                if fs < 0.80:  # readability floor (~7.5pt body); bounded
                    break
        finally:
            browser.close()
    return out_pdf


def flatten_cv_text(cv: dict) -> str:
    """Plain-text view of a structured CV (for prompts that want the whole CV)."""
    lines = [str(cv.get("name") or ""), " • ".join(cv.get("contact") or [])]
    for s in cv.get("sections") or []:
        lines.append("")
        lines.append(str(s.get("heading") or ""))
        typ = s.get("type")
        if typ == "text":
            lines.append(str(s.get("text") or ""))
        elif typ == "entries":
            for e in s.get("entries") or []:
                lines.append(f"{e.get('left','')}  {e.get('right','')}".strip())
                if e.get("subtitle"):
                    lines.append(str(e["subtitle"]))
                if e.get("sub"):
                    lines.append(str(e["sub"]))
                for b in e.get("bullets") or []:
                    lines.append(f"• {b}")
        elif typ == "keyvals":
            for i in s.get("items") or []:
                lines.append(f"{i.get('label','')}: {i.get('value','')}")
        elif typ == "bullets":
            for b in s.get("bullets") or []:
                lines.append(f"• {b}")
    return "\n".join(lines)


CL_CSS = f"""
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  .cl {{ font-family: 'Times New Roman', Times, serif; color: #000; font-size: 11pt; line-height: 1.38; }}
  .cl .name {{ text-align: center; font-weight: bold; font-size: 16pt; letter-spacing: .3px; }}
  .cl .contact {{ text-align: center; font-size: 9.5pt; margin-top: 1pt;
                  border-bottom: 1.2pt solid {NAVY}; padding-bottom: 5pt; }}
  .cl .contact a {{ color: #000; text-decoration: underline; }}
  .cl .date {{ margin-top: 15pt; font-size: 10.5pt; }}
  .cl .recipient {{ margin-top: 11pt; font-size: 10.5pt; line-height: 1.25; }}
  .cl .greeting {{ margin-top: 13pt; }}
  .cl .body {{ margin-top: 9pt; }}
  .cl .body p {{ margin: 0 0 9pt 0; text-align: justify; }}
  .cl .signoff {{ margin-top: 11pt; line-height: 1.3; }}
  .cl .signoff .name {{ text-align: left; font-weight: normal; font-size: 11pt; letter-spacing: 0; }}
"""


def render_cover_letter_pdf(text: str, header: dict, out_pdf: Path) -> Path:
    """Render a cover-letter body to a one-page formal letter PDF: a letterhead
    that matches the CV (name + contact + navy rule), then the date, recipient
    block, a "Dear ..." greeting, the body paragraphs and a sign-off. Only the
    body comes from the model; the scaffold is deterministic. Headless Chromium.

    `header` keys: name, contact, linkedin_url/label (letterhead) plus optional
    date, recipient (list of lines), greeting, signoff (default "Sincerely,")."""
    from playwright.sync_api import sync_playwright

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    contact = '<span class="sep"> • </span>'.join(_esc(c) for c in (header.get("contact") or []) if c)
    if header.get("linkedin_url"):
        contact += f' • <a href="{_esc(header["linkedin_url"])}">{_esc(header.get("linkedin_label") or "LinkedIn")}</a>'

    date_html = f'<div class="date">{_esc(header["date"])}</div>' if header.get("date") else ""
    recip_lines = [_esc(r) for r in (header.get("recipient") or []) if r]
    recip_html = f'<div class="recipient">{"<br>".join(recip_lines)}</div>' if recip_lines else ""
    greeting_html = f'<div class="greeting">{_esc(header["greeting"])}</div>' if header.get("greeting") else ""

    paras = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    body = "".join("<p>" + "<br>".join(_esc(line) for line in p.split("\n")) + "</p>"
                   for p in paras)

    signoff = header.get("signoff") or "Sincerely,"
    signature = header.get("signature") or header.get("name") or ""
    signoff_html = (f'<div class="signoff">{_esc(signoff)}'
                    f'<br><span class="name">{_esc(signature)}</span></div>')

    def build(scale):
        zoom = f".cl{{zoom:{scale};}}" if scale != 1.0 else ""
        return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CL_CSS}{zoom}</style></head>"
                f'<body><div class="cl"><div class="name">{_esc(header.get("name"))}</div>'
                f'<div class="contact">{contact}</div>'
                f'{date_html}{recip_html}{greeting_html}'
                f'<div class="body">{body}</div>{signoff_html}</div></body></html>')

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page()
            scale = 1.0
            from pypdf import PdfReader
            for _ in range(8):
                page.set_content(build(scale), wait_until="load")
                page.pdf(path=str(out_pdf), format="A4", print_background=True, margin=_MARGIN)
                if len(PdfReader(str(out_pdf)).pages) <= 1:
                    break
                scale -= 0.05
        finally:
            browser.close()
    return out_pdf


if __name__ == "__main__":
    import json
    import sys
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "storage" / "cvs" / "base_cv.json"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "storage" / "cvs" / "_render_test.pdf"
    render_cv_pdf(json.loads(src.read_text(encoding="utf-8")), out)
    print("rendered", out)
