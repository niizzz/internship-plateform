"""Assisted-apply engine: opens the bank's application page in a visible browser,
auto-fills every recognizable field from the user's profile, uploads the tailored
CV / cover letter into file inputs — then leaves review + Submit to the user.

Design notes:
- One persistent Chromium profile (storage/browser_profile) shared by all
  assists, so ATS accounts (Workday, Avature, ...) stay logged in between
  applications. One browser window; each assist opens a new tab.
- ATS flows are multi-page. A fill pass runs every few seconds for up to
  15 minutes per tab, so each new step the user advances to gets filled too.
- Fields are matched by a context string (label, name, id, placeholder,
  aria-label, autocomplete) against synonym patterns. Only EMPTY fields are
  filled — never overwrite what the user typed.
- The engine NEVER clicks Submit/Apply buttons.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

from playwright.async_api import BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).parent
PROFILE_DIR = BACKEND_DIR / "storage" / "browser_profile"

FILL_INTERVAL_S = 2.5
TAB_LIFETIME_S = 15 * 60


# Surname particles that belong with the family name ("El Hankari", "van Dijk").
_NAME_PARTICLES = {"el", "al", "van", "von", "de", "del", "della", "di", "da",
                   "dos", "du", "la", "le", "bin", "ben", "ter", "ten", "st"}


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    split_at = len(parts) - 1
    while split_at > 1 and parts[split_at - 1].lower().strip(".") in _NAME_PARTICLES:
        split_at -= 1
    return " ".join(parts[:split_at]), " ".join(parts[split_at:])


def build_field_values(profile: dict) -> list[tuple[re.Pattern, str]]:
    """Ordered (pattern, value) rules. First match wins; specific before generic."""
    first, last = _split_name(profile.get("full_name") or "")
    rules: list[tuple[str, Optional[str]]] = [
        # (regex over the field's context string, value)
        (r"first[\s_-]*name|given[\s_-]*name|forename|pr[ée]nom|vorname", first),
        (r"last[\s_-]*name|family[\s_-]*name|surname|nom de famille|nachname", last),
        (r"full[\s_-]*name|^name$|your name|legal name", profile.get("full_name")),
        (r"e[\s_-]*mail", profile.get("email")),
        (r"phone|mobile|telephone|t[ée]l[ée]phone|cell", profile.get("phone")),
        (r"linked[\s_-]*in", profile.get("linkedin_url")),
        (r"github", profile.get("github_url")),
        (r"portfolio|personal website|web ?site", profile.get("portfolio_url")),
        (r"post[\s_-]*(?:al)?[\s_-]*code|zip|plz", profile.get("postcode")),
        (r"address[\s_-]*line|street|address(?!.*email)|adresse", profile.get("address")),
        (r"city|town|ville|stadt|locality", profile.get("city")),
        (r"nationalit", profile.get("nationality")),
        (r"date[\s_-]*of[\s_-]*birth|birth[\s_-]*date|\bdob\b|naissance", profile.get("date_of_birth")),
        (r"gender|sex\b|genre", profile.get("gender")),
        (r"universit|school|college|institution|[ée]cole|hochschule", profile.get("university")),
        (r"degree|qualification|diploma|dipl[ôo]me|studiengang|major|field of study", profile.get("degree")),
        (r"graduat(?:ion|e)[\s_-]*(?:year|date)|completion (?:year|date)|end (?:year|date) of stud",
         profile.get("graduation_year")),
        (r"gpa|grade|average|mention|note", profile.get("gpa")),
        (r"work[\s_-]*(?:authori[sz]ation|permit)|right to work|visa|sponsor",
         profile.get("work_authorization")),
        (r"language", profile.get("languages")),
        (r"country|pays|land", profile.get("country")),
    ]
    return [(re.compile(rx, re.I), val) for rx, val in rules if val]


# JS run in the page on every pass. Fills empty text-like inputs/textareas whose
# context matches a rule; selects matching <select> options; returns count filled.
_FILL_JS = r"""
(rules) => {
  const ctxOf = (el) => {
    let parts = [el.name || "", el.id || "", el.placeholder || "",
                 el.getAttribute("aria-label") || "", el.getAttribute("autocomplete") || "",
                 el.getAttribute("data-automation-id") || ""];
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) parts.push(lab.textContent || "");
    }
    const wrapLab = el.closest("label");
    if (wrapLab) parts.push(wrapLab.textContent || "");
    // nearest preceding label-ish text within the same field container
    const cont = el.closest("div,li,td,fieldset");
    if (cont) {
      const lab2 = cont.querySelector("label, .label, [class*='label']");
      if (lab2) parts.push(lab2.textContent || "");
    }
    return parts.join(" ").toLowerCase().replace(/\s+/g, " ").slice(0, 300);
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && !el.disabled && !el.readOnly;
  };
  let filled = 0;
  const inputs = document.querySelectorAll(
    "input[type='text'],input[type='email'],input[type='tel'],input[type='url']," +
    "input[type='number'],input[type='date'],input:not([type]),textarea");
  for (const el of inputs) {
    if (!visible(el) || el.value) continue;
    if (el.dataset.idbFilled) continue;
    const ctx = ctxOf(el);
    for (const [rx, val] of rules) {
      if (new RegExp(rx, "i").test(ctx)) {
        el.focus();
        el.value = val;
        el.dispatchEvent(new Event("input", {bubbles: true}));
        el.dispatchEvent(new Event("change", {bubbles: true}));
        el.blur();
        el.dataset.idbFilled = "1";
        filled++;
        break;
      }
    }
  }
  for (const el of document.querySelectorAll("select")) {
    if (!visible(el) || el.dataset.idbFilled) continue;
    if (el.selectedIndex > 0 && el.value) continue;
    const ctx = ctxOf(el);
    for (const [rx, val] of rules) {
      if (!new RegExp(rx, "i").test(ctx)) continue;
      const want = val.toLowerCase();
      let best = null;
      for (const opt of el.options) {
        const t = (opt.textContent || "").trim().toLowerCase();
        if (!t) continue;
        if (t === want) { best = opt; break; }
        if (!best && (t.includes(want) || want.includes(t)) && t.length > 1) best = opt;
      }
      if (best) {
        el.value = best.value;
        el.dispatchEvent(new Event("change", {bubbles: true}));
        el.dataset.idbFilled = "1";
        filled++;
      }
      break;
    }
  }
  return filled;
}
"""

_BADGE_JS = r"""
(text) => {
  let b = document.getElementById("idb-assist-badge");
  if (!b) {
    b = document.createElement("div");
    b.id = "idb-assist-badge";
    b.style.cssText = "position:fixed;bottom:14px;right:14px;z-index:2147483647;" +
      "background:#111827;color:#f9fafb;padding:8px 14px;border-radius:8px;" +
      "font:13px system-ui;box-shadow:0 4px 12px rgba(0,0,0,.35);opacity:.92;pointer-events:none";
    document.body.appendChild(b);
  }
  b.textContent = text;
}
"""


class AssistBrowser:
    """Singleton headful browser with a persistent profile; one tab per assist."""

    _instance: Optional["AssistBrowser"] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self._pw = None
        self._context: Optional[BrowserContext] = None

    @classmethod
    async def get(cls) -> "AssistBrowser":
        async with cls._lock:
            if cls._instance is None:
                cls._instance = AssistBrowser()
            await cls._instance._ensure()
            return cls._instance

    async def _ensure(self):
        if self._context is not None:
            try:
                # Liveness probe: closed browsers raise.
                _ = self._context.pages
                if self._context.pages is not None:
                    return
            except Exception:
                self._context = None
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        if self._pw is None:
            self._pw = await async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 900},
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context.on("close", lambda _: setattr(self, "_context", None))

    async def new_page(self) -> Page:
        assert self._context is not None
        try:
            return await self._context.new_page()
        except Exception:
            # Browser window was closed since the liveness probe — relaunch once.
            self._context = None
            await self._ensure()
            assert self._context is not None
            return await self._context.new_page()


async def run_assist(
    apply_url: str,
    profile: dict,
    cv_path: Optional[str],
    cl_path: Optional[str],
) -> None:
    """Open apply_url in the assist browser and keep auto-filling until the tab
    closes or the lifetime expires. Runs as a fire-and-forget asyncio task."""
    rules = build_field_values(profile)
    js_rules = [[p.pattern, v] for p, v in rules]

    browser = await AssistBrowser.get()
    page = await browser.new_page()
    try:
        await page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        logger.warning("assist: initial navigation failed: %s", e)

    total_filled = 0
    uploads_done: set[str] = set()
    deadline = asyncio.get_event_loop().time() + TAB_LIFETIME_S

    while asyncio.get_event_loop().time() < deadline:
        try:
            if page.is_closed():
                break
            filled = await page.evaluate(_FILL_JS, js_rules)
            total_filled += filled or 0

            # File uploads: CV into resume-ish inputs, CL into cover-letter-ish.
            for finput in await page.query_selector_all("input[type='file']"):
                try:
                    marker = await finput.evaluate(
                        "el => (el.name||'')+' '+(el.id||'')+' '+(el.closest('div,li,fieldset')?.textContent||'').slice(0,200)")
                    marker_l = (marker or "").lower()
                    key = marker_l[:120]
                    if key in uploads_done:
                        continue
                    is_cl = any(w in marker_l for w in ("cover", "motivation", "lettre"))
                    path = cl_path if (is_cl and cl_path) else cv_path
                    if not path or not Path(path).exists():
                        continue
                    already = await finput.evaluate("el => el.files && el.files.length > 0")
                    if already:
                        uploads_done.add(key)
                        continue
                    await finput.set_input_files(path)
                    uploads_done.add(key)
                    total_filled += 1
                except Exception:
                    continue

            badge = (f"Internship DB: auto-filled {total_filled} field(s) — review everything, "
                     f"then submit yourself")
            try:
                await page.evaluate(_BADGE_JS, badge)
            except Exception:
                pass
        except Exception as e:
            # Page navigating / closed between checks — retry next tick.
            if page.is_closed():
                break
            logger.debug("assist pass error: %s", e)
        await asyncio.sleep(FILL_INTERVAL_S)

    logger.info("assist finished for %s: %d fields filled", apply_url, total_filled)
