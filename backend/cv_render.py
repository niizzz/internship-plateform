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


def render_html(cv: dict, scale: float = 1.0) -> str:
    sections = "".join(_section_html(s) for s in (cv.get("sections") or []))
    # `scale` shrinks the whole document proportionally (font sizes are in pt via
    # a root font scale) as an auto-fit safety net for slightly longer tailored
    # text — see render_cv_pdf.
    zoom = f"<style>.cv{{zoom:{scale};}}</style>" if scale != 1.0 else ""
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style>{zoom}</head>"
        f'<body><div class="cv">'
        f'<div class="name">{_esc(cv.get("name"))}</div>'
        f'<div class="contact">{_contact_line(cv)}</div>'
        f"{sections}</div></body></html>"
    )


_MARGIN = {"top": "0.45in", "bottom": "0.35in", "left": "0.5in", "right": "0.5in"}


def render_cv_pdf(cv: dict, out_pdf: Path) -> Path:
    """Render structured CV data to a one-page PDF via headless Chromium (sync
    Playwright, safe to call from a worker thread). If the content overflows one
    page it is auto-shrunk in small steps until it fits — the CV must stay one
    page, but tailored text can run a little longer than the base."""
    from playwright.sync_api import sync_playwright

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page()
            scale = 1.0
            for _ in range(8):
                page.set_content(render_html(cv, scale), wait_until="load")
                # Does the content exceed one A4 text-height at this scale?
                overflow = page.evaluate(
                    "() => document.body.scrollHeight > document.body.clientHeight + 2")
                page.pdf(path=str(out_pdf), format="A4", print_background=True,
                         prefer_css_page_size=False, margin=_MARGIN)
                from pypdf import PdfReader
                if len(PdfReader(str(out_pdf)).pages) <= 1:
                    break
                scale -= 0.04  # ~4% smaller each retry
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
  .cl {{ font-family: 'Times New Roman', Times, serif; color: #000; font-size: 11pt; line-height: 1.35; }}
  .cl .name {{ text-align: center; font-weight: bold; font-size: 16pt; letter-spacing: .3px; }}
  .cl .contact {{ text-align: center; font-size: 9.5pt; margin-top: 1pt;
                  border-bottom: 1.2pt solid {NAVY}; padding-bottom: 5pt; }}
  .cl .contact a {{ color: #000; text-decoration: underline; }}
  .cl .body {{ margin-top: 12pt; }}
  .cl .body p {{ margin: 0 0 9pt 0; text-align: left; }}
"""


def render_cover_letter_pdf(text: str, header: dict, out_pdf: Path) -> Path:
    """Render a plain-text cover letter to a one-page PDF with a letterhead that
    matches the CV (name + contact + navy rule), via headless Chromium."""
    from playwright.sync_api import sync_playwright

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    contact = '<span class="sep"> • </span>'.join(_esc(c) for c in (header.get("contact") or []) if c)
    if header.get("linkedin_url"):
        contact += f' • <a href="{_esc(header["linkedin_url"])}">{_esc(header.get("linkedin_label") or "LinkedIn")}</a>'
    # Blank lines separate paragraphs; a single newline (e.g. "Sincerely," above
    # the name) becomes a line break inside the paragraph.
    paras = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    body = "".join("<p>" + "<br>".join(_esc(line) for line in p.split("\n")) + "</p>"
                   for p in paras)

    def build(scale):
        zoom = f".cl{{zoom:{scale};}}" if scale != 1.0 else ""
        return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CL_CSS}{zoom}</style></head>"
                f'<body><div class="cl"><div class="name">{_esc(header.get("name"))}</div>'
                f'<div class="contact">{contact}</div><div class="body">{body}</div></div></body></html>')

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
