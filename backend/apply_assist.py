"""Assisted-apply engine: opens the bank's application page in a visible browser,
auto-fills every recognizable field from the user's profile, uploads the tailored
CV / cover letter into file inputs — then leaves review + Submit to the user.

Design notes:
- One persistent Chromium profile (storage/browser_profile) shared by all
  assists, so ATS accounts (Workday, Avature, ...) stay logged in between
  applications. One browser window; each assist opens a new tab.
- ATS flows are multi-page. A fill pass runs every few seconds for up to
  15 minutes per tab, so each new step the user advances to gets filled too.
- Fields are matched in two tiers: first the field's OWN attributes/labels,
  then (only if nothing matched) nearby label text from ancestors that wrap
  just this one field — a sibling field's label can never bleed in. Only
  EMPTY fields are filled — never overwrite what the user typed.
- Oracle CX ("Candidate Experience") gets dedicated handling: pill-button
  questions, cx-select comboboxes, and inline Education/Experience/Language
  entry editors fed from settings.extras_json.
- The engine NEVER clicks Submit/Apply buttons and NEVER fills e-signature
  fields — signing stays a human act.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import os
import re
from pathlib import Path
from typing import Optional

from playwright.async_api import BrowserContext, ElementHandle, Frame, Page, async_playwright

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).parent

# Pacing multiplier for the interactive waits inside cx-combobox / entry-editor
# driving. 1.0 in production (real ATSes need ~1.5s for dropdowns to populate);
# the fixture test sets IDB_ASSIST_PACE low so the suite runs fast.
_PACE = max(0.05, float(os.environ.get("IDB_ASSIST_PACE", "1.0")))


def _w(ms: int) -> int:
    return max(1, int(ms * _PACE))


PROFILE_DIR = BACKEND_DIR / "storage" / "browser_profile"

FILL_INTERVAL_S = 2.5
TAB_LIFETIME_S = 15 * 60
# Hard backstop: the same document is never auto-uploaded more than this many
# times in one assist run, no matter how the ATS re-renders its inputs.
MAX_UPLOADS_PER_DOC = 3


# Surname particles that belong with the family name ("de la Cruz", "van Dijk").
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


_UK_CH = ("united kingdom", "uk", "great britain", "england", "scotland",
          "switzerland", "suisse", "schweiz")

_YES = ["yes", "oui", "ja", "y"]
_NO = ["no", "non", "nein", "n"]


# --- phone formatting ---------------------------------------------------------
# Dial code -> country names as ATS dial-code pickers spell them. The first name
# is the preferred match: several countries share a code (+44 also lists
# Guernsey/Jersey/Isle of Man, +1 lists Canada), so the digits alone are ambiguous.
_DIAL_COUNTRIES = {
    "33": ["france"], "44": ["united kingdom", "uk", "great britain"],
    "212": ["morocco", "maroc"], "49": ["germany", "deutschland"],
    "41": ["switzerland", "suisse", "schweiz"], "39": ["italy", "italia"],
    "34": ["spain", "españa"], "32": ["belgium", "belgique"],
    "31": ["netherlands"], "352": ["luxembourg"], "353": ["ireland"],
    "351": ["portugal"], "43": ["austria"], "46": ["sweden"], "45": ["denmark"],
    "47": ["norway"], "358": ["finland"], "48": ["poland"], "30": ["greece"],
    "420": ["czech republic", "czechia"], "40": ["romania"], "36": ["hungary"],
    "1": ["united states", "usa"], "971": ["united arab emirates"],
    "65": ["singapore"], "852": ["hong kong"], "213": ["algeria"], "216": ["tunisia"],
}
# Countries whose NATIONAL format adds a trunk "0" in front of the subscriber
# number (+33 6 12 34 56 78 is written 06 12 34 56 78 at home). Italy, Spain,
# Portugal, Luxembourg, the Nordics bar Sweden/Finland, Greece, Poland, US and
# Singapore have no trunk prefix: their national form is the bare number.
_TRUNK_ZERO = {"33", "44", "49", "41", "43", "32", "31", "212", "353", "46", "358",
               "40", "971", "213", "216"}


def _split_phone(intl: str) -> tuple[str, str]:
    """'+33 6 12 34 56 78' -> ('33', '612345678'). Dial codes are 1-3 digits and
    prefix-free, so match the known table longest-first — a compact '+337436…'
    must not read as dial 337. Unknown codes fall back to the digits before the
    first space. A number with no '+'/'00' prefix has no dial code: ('', digits)."""
    s = (intl or "").strip()
    digits = re.sub(r"\D", "", s)
    if s.startswith("00"):
        digits, s = digits[2:], "+" + s[2:]
    if not s.startswith("+"):
        return "", digits.lstrip("0")
    for n in (3, 2, 1):
        if digits[:n] in _DIAL_COUNTRIES:
            return digits[:n], digits[n:].lstrip("0")
    m = re.match(r"^\+\s*(\d{1,3})\D", s)
    if m:
        return m.group(1), digits[len(m.group(1)):].lstrip("0")
    return "", digits


def _national_display(intl: str) -> str:
    """The number as dialled inside its own country, for a field that sits next
    to a separate country-code picker: '+33 6 12 34 56 78' -> '0612345678'."""
    dial, nsn = _split_phone(intl)
    return ("0" + nsn) if dial in _TRUNK_ZERO else nsn


def _dial_from_text(raw: str) -> str:
    """Dial digits a country-code control currently shows, from its value or
    visible text: '+33', 'France (+33)', '33', 'France' -> '33'. '' when it shows
    a placeholder ('Select…') or nothing recognisable."""
    t = (raw or "").strip().lower()
    m = re.search(r"\+\s?(\d{1,4})\b", t)
    if m:
        return m.group(1)
    m = re.fullmatch(r"\(?(\d{1,4})\)?", t)
    if m:
        return m.group(1)
    for d, names in _DIAL_COUNTRIES.items():
        if any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", t) for n in names if len(n) > 2):
            return d
    return ""


def _phone_value(intl: str, info: dict) -> Optional[str]:
    """What to type into one phone input, given its context from _PHONE_INFO_JS.
    None = don't type yet (a separate code picker shows a DIFFERENT country and
    couldn't be changed — the national number would be read under that code).

    - intl-tel-input / react-phone-input: full international number; the widget
      parses the '+33' and sets its own flag.
    - a separate country-code control: national number with trunk 0
      ('0612345678'); the code lives in the other control.
    - a lone field: full international, which is unambiguous to any reader —
      unless the field can't hold it (digits-only pattern / type=number, or a
      maxlength shorter than it), then compact '+33612345678' or national."""
    dial, nsn = _split_phone(intl)
    if info.get("iti"):
        return intl
    if info.get("hasDial"):
        cur = _dial_from_text(info.get("dialVal", ""))
        if dial and cur and cur != dial:
            return None
        return _national_display(intl)
    compact = ("+" + dial + nsn) if dial else nsn
    max_len = int(info.get("maxLen") or 0)
    if info.get("noPlus"):
        return _national_display(intl)
    if 0 < max_len < len(intl):
        return compact if len(compact) <= max_len else _national_display(intl)
    return intl


def build_field_values(profile: dict) -> list[list]:
    """Ordered fill rules, ready to ship to the page as JS data.

    Each rule is [regex, value, alts, kind]:
    - regex   : tested (case-insensitive) against the field context
    - value   : what gets typed into text inputs
    - alts    : lowercase labels accepted for <select>/radio/pill options
                (None -> match on value itself)
    - kind    : "text"  = any field
                "date"  = reformatted per input (ISO for <input type=date>,
                          dd/mm/yyyy default, placeholder hints respected)
                "choice"= selects/radios/pills only — never typed into text
                "phone" = like text, but the dial code is stripped when the
                          field sits next to a country-code selector
    First matching rule wins for text inputs; choice widgets try every
    matching rule until one actually fits an available option, so an early
    near-miss (e.g. "école" hitting the university rule inside a French
    yes/no question) can't dead-end the fill.
    """
    first, last = _split_name(profile.get("full_name") or "")
    extras = profile.get("extras_json") or {}
    gender = (profile.get("gender") or "").strip().lower()

    title = title_alts = None
    if gender[:1] in ("m", "h"):          # male / homme / herr
        title, title_alts = "Mr", ["mr", "mr.", "m", "m.", "monsieur", "mister", "herr"]
    elif gender[:1] in ("f", "w"):        # female / femme / weiblich
        title, title_alts = "Ms", ["ms", "ms.", "mrs", "miss", "mme", "madame", "frau"]

    gender_alts = {
        "male": ["male", "man", "homme", "masculin", "männlich", "m"],
        "female": ["female", "woman", "femme", "féminin", "weiblich", "f"],
    }.get(gender)

    dial = None
    dial_d, _nsn = _split_phone(profile.get("phone") or "")
    if dial_d:
        dial = "+" + dial_d

    nationality = profile.get("nationality")
    nat_alts = None
    if (nationality or "").strip().lower() in ("moroccan", "morocco"):
        nat_alts = ["moroccan", "morocco", "marocain", "marocaine", "maroc",
                    "marokko", "marokkanisch"]

    country = profile.get("country")
    country_alts = ["france", "french republic", "frankreich", "francia"] \
        if (country or "").strip().lower() == "france" else None

    birth_country = extras.get("country_of_birth")
    birth_country_alts = ["morocco", "maroc", "marokko"] \
        if (birth_country or "").strip().lower() == "morocco" else None

    # Right-to-work answers depend on where the job is (main.py passes
    # offer_country): user can work in the EU, needs sponsorship for UK/CH.
    # "Unknown" (location parse failure) must yield NO answer at all — the
    # truthy string used to fall through to "no sponsorship needed", silently
    # pre-selecting a wrong visa answer on UK/CH applications.
    oc = (profile.get("offer_country") or "").strip().lower()
    needs_sponsor: Optional[bool] = (oc in _UK_CH) if oc and oc != "unknown" else None

    rules: list[list] = []

    def add(rx: str, val, alts: Optional[list] = None, kind: str = "text"):
        if val:
            rules.append([rx, str(val), [a for a in (alts or []) if a] or None, kind])

    add(r"first[\s_-]*name|given[\s_-]*name|forename|pr[ée]nom|vorname", first)
    add(r"last[\s_-]*name|family[\s_-]*name|surname|nom de famille|nachname", last)
    add(r"full[\s_-]*name|^name$|your name|legal name", profile.get("full_name"))
    add(r"e[\s_-]*mail|courriel", profile.get("email"))
    # Dial code before the generic phone rule ("country phone code" selects).
    # Wordings seen: "Country code", "Dial code", "Country phone code",
    # "International Calling code" (SuccessFactors/HSBC), "Phone country code".
    add(r"(?:country|dial(?:l?ing)?|international|calling)[\s_-]*(?:(?:calling|dial(?:l?ing)?|phone)[\s_-]*)?code"
        r"|phone[\s_-]*country[\s_-]*code|indicatif|vorwahl|prefisso|prefijo",
        dial, _DIAL_COUNTRIES.get(dial_d, [])[:1] + [dial, (dial or "").lstrip("+")])
    # Secondary phone slots stay EMPTY: BrassRing offers Mobile / Home / Other
    # phone, and the old rule copied the mobile into "Other phone" too (and
    # would do the same to a "Work phone"). They are optional and a duplicated
    # number is noise, not data.
    add(r"^(?![\s\S]*(?:other|work|business|office|employer|fax|emergency)[\s_-]*(?:phone|tel))"
        r"[\s\S]*(?:phone|mobile|telephone|t[ée]l[ée]phone|portable)",
        profile.get("phone"), None, "phone")
    add(r"linked[\s_-]*in", profile.get("linkedin_url"))
    add(r"github", profile.get("github_url"))
    add(r"portfolio|personal website|web ?site", profile.get("portfolio_url"))
    # Generic "Link 1"-style URL slots (Oracle "Supporting Documents and URLs").
    add(r"sitelink|\blink ?\d\b|\burl\b", profile.get("linkedin_url"))
    # Birth fields BEFORE generic city/country/date rules ("lieu de naissance",
    # "country of birth" would otherwise hit those).
    add(r"(?:place|city|town|ville|lieu)[\s_-]*(?:of|de)?[\s_-]*(?:birth|naissance)"
        r"|birth[\s_-]*place|birthplace|geburtsort", extras.get("place_of_birth"))
    add(r"(?:country|pays|land)[\s_-]*(?:of|de)?[\s_-]*(?:birth|naissance)|geburtsland",
        birth_country, birth_country_alts)
    add(r"date[\s_-]*of[\s_-]*birth|birth[\s_-]*date|\bdob\b|naissance|geburtsdatum",
        profile.get("date_of_birth"), None, "date")
    add(r"gender|sex\b|genre|geschlecht", profile.get("gender"), gender_alts)
    # "title" only ever as a choice: a select/pill row without Mr/Ms-like
    # options (e.g. a job-title widget) won't match any alt and stays untouched.
    add(r"civilit|salutation|anrede|\btitle\b", title, title_alts, "choice")
    # --- Application questions (Oracle pills, radios, selects) --------------
    if needs_sponsor is not None:
        add(r"sponsor", "Yes" if needs_sponsor else "No",
            _YES if needs_sponsor else _NO, "choice")
        add(r"right[\s_-]*to[\s_-]*work|eligible to work|authori[sz]ed to work"
            r"|legally (?:able|entitled|allowed) to work",
            "No" if needs_sponsor else "Yes",
            _NO if needs_sponsor else _YES, "choice")
    # Citizen/PR of the role's country: the user is a Moroccan citizen and none
    # of the tracked offers are in Morocco.
    add(r"citizen or permanent resident|permanent resident of", "No", _NO, "choice")
    add(r"come into the office|work (?:from|in) the office|on[\s-]?site\b|pr[ée]sentiel",
        "Yes", _YES, "choice")
    add(r"family member|relatives? (?:employed|working)|membre de (?:votre |la )?famille",
        "No", _NO, "choice")
    add(r"convention de stage", "Yes", _YES, "choice")
    add(r"dur[ée]e de 6 mois|6[\s-]?month internship", "Yes", _YES, "choice")
    add(r"how did you (?:hear|discover|find)|source of application"
        r"|comment avez-vous (?:connu|d[ée]couvert|entendu)",
        "Careers Site",
        ["careers site", "career site", "company website", "careers website",
         "corporate website", "site carri[èe]res"], "choice")
    add(r"work[\s_-]*(?:authori[sz]ation|permit)|visa|immigration status",
        profile.get("work_authorization"))
    # --- Address / identity --------------------------------------------------
    add(r"post[\s_-]*(?:al)?[\s_-]*code|zip|plz|code postal", profile.get("postcode"))
    # City BEFORE the street-address rule: Workday names every address subfield
    # "addressSection_*" (…_city, …_addressLine1), and the bare "address" branch
    # below would otherwise claim the City field and drop the street into it.
    # Lookbehinds keep "ethnicity"/"electricity" from matching as a city field.
    add(r"(?<!ethni)(?<!electri)city|town|ville|stadt|locality|commune",
        profile.get("city"))
    # (?!-level): Oracle city/state inputs carry autocomplete="address-level2/3"
    # — without the guard the street address lands in the City field.
    # The leading (?!.*…) blocks SECONDARY address lines: UBS BrassRing ships an
    # optional "Address Line 2" (profile_6_0_address2_txt_0) and the old rule
    # duplicated the street into it. The guard tests the WHOLE field context on
    # purpose — a positional lookahead would still let the bare "address"
    # branch match "address line 2".
    add(r"^(?![\s\S]*(?:address[\s_-]*(?:line[\s_-]*)?[23]\b|line[\s_-]*[23]\b"
        r"|apartment|apt\b|suite|complement|compl[ée]ment|additional[\s_-]*address))"
        r"[\s\S]*(?:address[\s_-]*line|street|address(?!-level)(?!.*email)|adresse)",
        profile.get("address"))
    add(r"nationalit|citizenship|citoyennet", profile.get("nationality"), nat_alts)
    add(r"universit|school|college|institution|[ée]cole|hochschule",
        profile.get("university"))
    add(r"degree|qualification|diploma|dipl[ôo]me|studiengang|major|field of study",
        profile.get("degree"))
    add(r"graduat(?:ion|e)[\s_-]*(?:year|date)|completion (?:year|date)"
        r"|end (?:year|date) of stud|ann[ée]e d'obtention",
        profile.get("graduation_year"))
    add(r"\bgpa\b|grade point|academic average|\bmoyenne\b", profile.get("gpa"))
    add(r"language|langue", profile.get("languages"))
    add(r"country|pays|\bland\b", country, country_alts)
    return rules


# JS run in the page on every pass. Fills empty text-like inputs/textareas,
# selects matching <select> options, checks matching radio groups, clicks
# matching pill buttons (Oracle CX). Rules arrive as [regex, value, alts, kind].
_FILL_JS = r"""
(rules) => {
  // Collect elements matching sel, piercing open shadow roots (Phenom,
  // SuccessFactors and other web-component ATSes hide fields there).
  const collect = (root, sel, out) => {
    for (const el of root.querySelectorAll(sel)) out.push(el);
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) collect(el.shadowRoot, sel, out);
    }
    return out;
  };
  // React/Vue/Angular track value via the native prototype setter — a plain
  // `el.value = x` bypasses their change detection and the framework treats
  // the field as still empty (Oracle HCM, Workday, Phenom are all React).
  const setNative = (el, val) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
                : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, val); else el.value = val;
    el.dispatchEvent(new Event("input", {bubbles: true}));
    el.dispatchEvent(new Event("change", {bubbles: true}));
  };
  const rxCache = {};
  const rx = (p) => rxCache[p] || (rxCache[p] = new RegExp(p, "i"));
  const norm = (s) => (s || "").toLowerCase().replace(/\s+/g, " ").trim();
  const escRx = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  const rootOf = (el) => (el.getRootNode ? el.getRootNode() : document);
  const labelFor = (el) => {
    if (!el.id) return "";
    try {
      const l = rootOf(el).querySelector(`label[for="${CSS.escape(el.id)}"]`);
      return l ? l.textContent || "" : "";
    } catch (e) { return ""; }
  };
  const byIds = (el, attr) => {
    const v = el.getAttribute(attr);
    if (!v) return "";
    return v.split(/\s+/).map((id) => {
      const n = rootOf(el).getElementById ? rootOf(el).getElementById(id) : null;
      return n ? n.textContent || "" : "";
    }).join(" ");
  };
  // Tier 1 context: the field's own attributes + directly-associated labels.
  const ctxSelf = (el) => norm([
    el.name || "", el.id || "", el.placeholder || "",
    el.getAttribute("aria-label") || "", el.getAttribute("autocomplete") || "",
    el.getAttribute("data-automation-id") || "", labelFor(el),
    (el.closest("label") || {}).textContent || "", byIds(el, "aria-labelledby"),
  ].join(" ")).slice(0, 250);
  // Tier 2 context: nearby label text — but ONLY from ancestors wrapping just
  // this one form field, so a sibling field's label can never bleed in.
  const ctxNear = (el) => {
    let node = el.parentElement, out = "";
    for (let i = 0; i < 5 && node; i++, node = node.parentElement) {
      if (node.querySelectorAll("input:not([type=hidden]),select,textarea").length > 1) break;
      const lab = node.querySelector("label, legend, [class*='label'], [class*='Label']");
      if (lab) { out = lab.textContent || ""; break; }
      const t = (node.textContent || "").trim();
      if (t && t.length <= 120) out = t;
    }
    return norm(out).slice(0, 250);
  };
  // Spam-trap fields (Oracle HCM ships an aria-label="honeypot" input):
  // filling one flags the application as a bot. Never touch them.
  const isHoneypot = (c) => /honey|h0n3y|do[\s_-]*not[\s_-]*fill|leave[\s_-]*(this[\s_-]*)?blank/.test(c);
  // E-signature fields: filling one is signing — a human act. Never touch.
  const isSignature = (el, c) =>
    /signatur/.test(c) || !!el.closest("[class*='esign'], [class*='signatur']");
  // Oracle CX inline entry editors (education/experience/language): those
  // fields belong to the Python entry filler with per-entry values — the
  // generic filler would smear the same profile value over every entry.
  const inEntryEditor = (el) => {
    const fb = el.closest("form-builder");
    return !!(fb && fb.querySelector(
      "input[name='educationalEstablishment'],input[name='employerName'],input[name='contentItemId']"));
  };
  // All rules whose regex matches this element's context, self-tier first.
  const matchRules = (el) => {
    const self = ctxSelf(el);
    if (isHoneypot(self) || isSignature(el, self)) return null;
    const out = [];
    for (const r of rules) if (rx(r[0]).test(self)) out.push(r);
    let ctx = self;
    if (!out.length) {
      const near = ctxNear(el);
      if (!near || isHoneypot(near) || /signatur/.test(near)) return {list: [], ctx: self};
      ctx = self + " " + near;
      for (const r of rules) if (rx(r[0]).test(near)) out.push(r);
    }
    return {list: out, ctx};
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && !el.disabled && !el.readOnly;
  };
  // Dates: ISO for date inputs; otherwise follow the placeholder hint,
  // defaulting to dd/mm/yyyy (all target banks are EU/UK).
  const fmtDate = (val, el, ctx) => {
    const m = String(val).match(/^(\d{4})[-\/.](\d{1,2})[-\/.](\d{1,2})/);
    if (!m) return val;
    const y = m[1], mo = m[2].padStart(2, "0"), d = m[3].padStart(2, "0");
    if (el.type === "date") return `${y}-${mo}-${d}`;
    const hint = norm((el.placeholder || "") + " " + ctx);
    if (/mm[\s\/.-]*dd[\s\/.-]*y{2,4}/.test(hint)) return `${mo}/${d}/${y}`;
    if (/y{4}[\s\/.-]*mm[\s\/.-]*dd/.test(hint)) return `${y}-${mo}-${d}`;
    return `${d}/${mo}/${y}`;
  };
  // Best option for a rule: exact > whole-word > cautious substring (min length
  // 5 both ways, so "Male" can never substring-match the "Female" option).
  const pickOption = (opts, r) => {
    const cands = (r[2] && r[2].length ? r[2] : [r[1]]).map(norm).filter(Boolean);
    let best = null, bestScore = 0;
    for (const o of opts) {
      const t = norm(o.text);
      if (!t) continue;
      for (const c of cands) {
        let s = 0;
        if (t === c) s = 3;
        else if (rx("(?<![\\w])" + escRx(c) + "(?![\\w])").test(t)) s = 2;
        else if ((c.length >= 5 && t.includes(c)) || (t.length >= 5 && c.includes(t))) s = 1;
        if (s > bestScore) { bestScore = s; best = o; }
      }
      if (bestScore === 3) break;
    }
    return best;
  };

  // Filling focuses fields; remember where the user's cursor was so a pass
  // never steals it (that plus scroll jumps was what made assists feel clunky).
  const active0 = document.activeElement;
  let filled = 0;

  const inputs = collect(document,
    "input[type='text'],input[type='email'],input[type='tel'],input[type='url']," +
    "input[type='number'],input[type='date'],input:not([type]),textarea", []);
  for (const el of inputs) {
    if (!visible(el) || el.value || el.dataset.idbFilled) continue;
    // cx-select comboboxes need dropdown-option clicks — Python handles them.
    if (String(el.className || "").includes("cx-select")) continue;
    // SuccessFactors picklists are text inputs too, but a value-set leaves the
    // hidden real field empty — _fill_sf_picklists types + clicks an option.
    if (String(el.className || "").includes("rcmpaginatedselect")) continue;
    if (inEntryEditor(el)) continue;
    const m = matchRules(el);
    if (!m) continue;
    // Phone fields are typed by Python (_fill_phone_fields): intl-tel-input and
    // similar widgets reformat on keystrokes, so a native value-set is mangled
    // to just the dial code. A phone <input> often ALSO matches the dial-code
    // rule (its label reads "phone number with country code") — so if the phone
    // rule matches at all, hand the whole field to the typing pass.
    if (m.list.some((x) => x[3] === "phone")) continue;
    const r = m.list.find((x) => x[3] !== "choice");
    if (!r) continue;
    let val = r[1];
    if (r[3] === "date") val = fmtDate(val, el, m.ctx);
    try { el.focus({preventScroll: true}); } catch (e) {}
    setNative(el, val);
    el.blur();
    el.dataset.idbFilled = "1";
    filled++;
  }

  for (const el of collect(document, "select", [])) {
    if (!visible(el) || el.dataset.idbFilled) continue;
    if (el.selectedIndex > 0 && el.value) continue;
    if (inEntryEditor(el)) continue;
    const m = matchRules(el);
    if (!m) continue;
    const opts = Array.from(el.options).map((o) => ({text: o.textContent || "", value: o.value}));
    for (const r of m.list) {
      const best = pickOption(opts, r);
      if (best) {
        setNative(el, best.value);
        el.dataset.idbFilled = "1";
        filled++;
        break;
      }
    }
  }

  // Radio groups (gender, yes/no...). Only untouched groups: if anything is
  // already checked, it stays. Custom-styled ATS radios often hide the real
  // input, so group visibility may come from the labels.
  const groups = new Map();
  for (const el of collect(document, "input[type='radio']", [])) {
    if (!el.name || el.dataset.idbFilled || inEntryEditor(el)) continue;
    const key = (el.form && el.form.id ? el.form.id + "::" : "") + el.name;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(el);
  }
  for (const els of groups.values()) {
    if (els.some((e) => e.checked)) continue;
    const groupVisible = els.some(visible) || els.some((e) => {
      const l = e.closest("label") || (e.id ? rootOf(e).querySelector(`label[for="${CSS.escape(e.id)}"]`) : null);
      return l && l.getBoundingClientRect().width > 1;
    });
    if (!groupVisible) continue;
    const first = els[0];
    let gctx = norm(first.name || "");
    const fs = first.closest("fieldset");
    const leg = fs ? fs.querySelector("legend") : null;
    if (leg) gctx += " " + norm(leg.textContent);
    const rg = first.closest("[role='radiogroup']");
    if (rg) gctx += " " + norm((rg.getAttribute("aria-label") || "") + " " + byIds(rg, "aria-labelledby"));
    if (gctx.replace(/\s/g, "").length < 4) {
      let node = first.parentElement;
      while (node && !els.every((e) => node.contains(e))) node = node.parentElement;
      if (node) gctx = norm(node.textContent).slice(0, 250);
    }
    if (isHoneypot(gctx) || /signatur/.test(gctx) || isSignature(first, gctx)) continue;
    const opts = els.map((e) => ({
      text: labelFor(e) || ((e.closest("label") || {}).textContent || "") || e.value || "",
      el: e,
    }));
    for (const r of rules) {
      if (!rx(r[0]).test(gctx)) continue;
      const best = pickOption(opts, r);
      if (best) {
        best.el.click();
        for (const e of els) e.dataset.idbFilled = "1";
        filled++;
        break;
      }
    }
  }

  // Pill-button groups (Oracle CX: Title, yes/no application questions,
  // "how did you discover..."). A group = sibling pill buttons; skip groups
  // with any pressed pill, match the group's question against the rules,
  // click the best-fitting pill.
  const pillGroups = new Map();
  for (const b of collect(document, "button[class*='pill']", [])) {
    if (b.dataset.idbFilled || inEntryEditor(b)) continue;
    // Group root = nearest ancestor holding >=2 pills (Oracle wraps each pill
    // in its own <li>, so the direct parent is a group of one).
    let p = b.parentElement;
    for (let i = 0; i < 3 && p; i++, p = p.parentElement) {
      if (p.querySelectorAll("button[class*='pill']").length >= 2) break;
    }
    if (!p) continue;
    if (!pillGroups.has(p)) pillGroups.set(p, []);
    pillGroups.get(p).push(b);
  }
  for (const [container, btns] of pillGroups) {
    if (btns.length < 2) continue;
    if (btns.some((b) => b.getAttribute("aria-pressed") === "true"
                         || /\bselected\b|\bactive\b/.test(b.className))) continue;
    let q = "";
    let n = container;
    for (let i = 0; i < 6 && n; i++, n = n.parentElement) {
      // Stop before an ancestor that contains other pill groups too.
      if (i > 0 && n.querySelectorAll("button[class*='pill']").length > btns.length) break;
      const lab = n.querySelector("label, legend, [class*='label'], [class*='Label']");
      if (lab && lab.textContent.trim()) { q = lab.textContent; break; }
      const t = (n.textContent || "").trim();
      if (t.length <= 250) q = t;
    }
    q = norm(q).slice(0, 250);
    if (!q || isHoneypot(q) || /signatur/.test(q) || isSignature(container, q)) continue;
    for (const r of rules) {
      if (!rx(r[0]).test(q)) continue;
      const best = pickOption(btns.map((b) => ({text: b.textContent || "", el: b})), r);
      if (best) {
        best.el.click();
        for (const b of btns) b.dataset.idbFilled = "1";
        filled++;
        break;
      }
    }
  }

  if (active0 && active0 !== document.body && active0 !== document.activeElement
      && typeof active0.focus === "function") {
    try { active0.focus({preventScroll: true}); } catch (e) {}
  }
  return filled;
}
"""

# Auto-accept cookie banners from known consent managers only — they overlay
# the form and block interaction. Strict id allowlist; never generic buttons.
_CONSENT_JS = r"""
() => {
  const sels = [
    "#onetrust-accept-btn-handler",                          // OneTrust
    "#truste-consent-button",                                // TrustArc
    "#didomi-notice-agree-button",                           // Didomi
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",// Cookiebot
    "#axeptio_btn_acceptAll",                                // Axeptio
    "button[data-testid='uc-accept-all-button']",            // Usercentrics
    "#tarteaucitronPersonalize2",                            // tarteaucitron
  ];
  for (const sel of sels) {
    const el = document.querySelector(sel);
    if (el && el.offsetParent !== null) { el.click(); return sel; }
  }
  const accept = ["tout accepter", "accept all", "accept all cookies",
                  "alle akzeptieren", "accepter tout", "accepter et continuer",
                  "accepter et fermer", "accept cookies"];
  for (const b of document.querySelectorAll("button, a[role='button'], input[type='button'], input[type='submit']")) {
    const t = ((b.textContent || b.value || "").trim().toLowerCase());
    if (accept.includes(t) && b.offsetParent !== null) { b.click(); return "text:" + t; }
  }
  return null;
}
"""

# Filenames of already-attached documents show up as chips/list entries in the
# form. Returns the subset of `names` visible anywhere in the frame (including
# open shadow roots) — those documents must not be uploaded again here.
_VISIBLE_NAMES_JS = r"""
(names) => {
  let hay = document.body ? document.body.innerText || "" : "";
  const walk = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) {
        hay += " " + (el.shadowRoot.textContent || "");
        walk(el.shadowRoot);
      }
    }
  };
  try { walk(document); } catch (e) {}
  return names.filter((n) => hay.includes(n));
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
            self._context = None
            await self._ensure()
            assert self._context is not None
            return await self._context.new_page()


# ---------------------------------------------------------------------------
# File-input routing: which document goes into which upload field.
# ---------------------------------------------------------------------------
_CL_WORDS = ("cover", "motivation", "lettre", "anschreiben")
_CV_RX = re.compile(r"resume|\bcvs?\b|curriculum|lebenslauf", re.I)
_MISC_RX = re.compile(
    r"transcript|certificat|certificate|diploma|dipl[ôo]me|relev[ée]|zeugnis|"
    r"other\s+document|additional\s+(?:document|file|attachment)|supporting\s+document|"
    r"miscellaneous|autres?\s+(?:documents?|fichiers?)|compl[ée]mentaire|justificatif|"
    r"upload\s+attachment", re.I)
# CV-parser import widgets (Oracle "Import your profile", Apply-with-LinkedIn):
# feeding them triggers the ATS resume parser, which fabricates junk education/
# experience entries. The engine fills those sections itself instead.
_IMPORT_RX = re.compile(r"import your (?:profile|information)|profile.?import|awli|"
                        r"apply with linkedin", re.I)

_MARKER_JS = """el => {
    const parts = [el.name||'', el.id||'',
                   el.getAttribute('aria-label')||'',
                   el.getAttribute('data-automation-id')||'',
                   String(el.className||'')];
    if (el.id) {
      try {
        const l = document.querySelector('label[for="'+CSS.escape(el.id)+'"]');
        if (l) parts.push(l.textContent||'');
      } catch (e) {}
    }
    const wl = el.closest('label');
    if (wl) parts.push(wl.textContent||'');
    // Two context markers: "tight" = the CLOSEST short-text ancestor (the
    // field's own dropzone/label), "wide" = the closest medium-text ancestor
    // (a section heading like Oracle's "Miscellaneous Documents"). Taking the
    // closest — not the farthest — short ancestor matters: on Oracle the
    // resume and cover-letter dropzones share an 88-char parent, and the old
    // farthest-wins logic gave BOTH inputs the combined text, so "cover"
    // matched first and the CV never uploaded.
    let tight = '', wide = '';
    let node = el.parentElement;
    for (let i = 0; i < 8 && node; i++, node = node.parentElement) {
      const t = (node.textContent||'').trim().replace(/\\s+/g,' ');
      if (t.length > 300) break;
      if (!tight && t.length && t.length <= 90) tight = t;
      else if (!wide && t.length > 90) wide = t;
      if (tight && wide) break;
    }
    const norm = s => s.toLowerCase().replace(/\\s+/g,' ');
    return {attrs: norm(parts.join(' ')).slice(0,300),
            tight: norm(parts.join(' ')+' '+tight).slice(0,300),
            wide: norm(wide).slice(0,300)};
}"""


def _doc_key(frame_key: str, path: str) -> str:
    return f"doc::{frame_key}::{Path(path).name}"


def _session_upload_count(uploads_done: set[str], path: str) -> int:
    suffix = "::" + Path(path).name
    return sum(1 for k in uploads_done if k.startswith("doc::") and k.endswith(suffix))


# ---------------------------------------------------------------------------
# Oracle CX support: cx-select comboboxes + inline entry editors.
# ---------------------------------------------------------------------------

_MONTHS = list(calendar.month_name)  # [""] + January..December

# Comboboxes owned by the entry-editor filler, not the generic combobox pass.
_EDITOR_CX_NAMES = {"educationalEstablishment", "countryCode", "contentItemId"}

_CX_OPTIONS_JS = """(eid) => {
    const lb = document.getElementById(eid + '-listbox');
    if (!lb) return [];
    const out = [];
    for (const o of lb.querySelectorAll("[role='gridcell'],[role='option'],li")) {
      const r = o.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) out.push((o.textContent||'').trim());
    }
    return out;
}"""

_CX_CLICK_JS = """([eid, want]) => {
    const lb = document.getElementById(eid + '-listbox');
    if (!lb) return false;
    for (const o of lb.querySelectorAll("[role='gridcell'],[role='option'],li")) {
      const r = o.getBoundingClientRect();
      if (r.width > 0 && (o.textContent||'').trim() === want) { o.click(); return true; }
    }
    return false;
}"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _pick_text(options: list[str], cands: list[str]) -> Optional[str]:
    """Best option text for candidate values: exact > whole-word > substring."""
    cands = [_norm(c) for c in cands if c]
    best, best_score = None, 0
    for opt in options:
        t = _norm(opt)
        if not t:
            continue
        for c in cands:
            s = 0
            if t == c:
                s = 3
            elif re.search(r"(?<![\w])" + re.escape(c) + r"(?![\w])", t):
                s = 2
            elif (len(c) >= 5 and c in t) or (len(t) >= 5 and t in c):
                s = 1
            if s > best_score:
                best, best_score = opt, s
        if best_score == 3:
            break
    return best


async def _cx_pick(frame: Frame, el: ElementHandle, text: Optional[str],
                   cands: list[str]) -> bool:
    """Fill an Oracle cx-select combobox, then click the best-matching option.

    text=None  -> selection-only field (month, gender): open and pick from cands.
    text given -> searchable field. The list may be a controlled vocabulary
      whose canonical entry differs from the profile value ("EMLYON Business
      School" vs Oracle's "EM Lyon, Ecole de Management de Lyon"), so each
      candidate is tried as a SEARCH QUERY in turn until the listbox returns
      options that actually match. Leaves the field cleared on failure."""
    eid = await el.get_attribute("id")
    if not eid:
        return False
    try:
        await el.scroll_into_view_if_needed()
    except Exception:
        pass

    async def _reset():
        try:
            await frame.click(f"#{eid}-reset-button", timeout=1500)
        except Exception:
            try:
                await el.press("Escape")
            except Exception:
                pass

    try:
        await el.click()
        await frame.wait_for_timeout(_w(300))

        if not text:
            options = await frame.evaluate(_CX_OPTIONS_JS, eid)
            if not options and (await el.get_attribute("aria-expanded")) != "true":
                try:
                    await frame.click(f"#{eid}-toggle-button", timeout=2000)
                    await frame.wait_for_timeout(_w(1200))
                    options = await frame.evaluate(_CX_OPTIONS_JS, eid)
                except Exception:
                    pass
            best = _pick_text(options, cands or [])
            if best is None:
                await _reset()
                return False
            ok = await frame.evaluate(_CX_CLICK_JS, [eid, best])
            await frame.wait_for_timeout(_w(500))
            return bool(ok)

        # Searchable: try each distinct candidate as a typed query.
        queries: list[str] = []
        for q in [text] + list(cands or []):
            if q and q not in queries:
                queries.append(q)
        for qtext in queries[:5]:
            await el.fill("")
            await el.type(qtext[:40], delay=35)
            await frame.wait_for_timeout(_w(1500))
            options = await frame.evaluate(_CX_OPTIONS_JS, eid)
            if not options:
                continue
            best = _pick_text(options, cands or [text])
            if best is not None:
                ok = await frame.evaluate(_CX_CLICK_JS, [eid, best])
                await frame.wait_for_timeout(_w(500))
                return bool(ok)
        await _reset()
        return False
    except Exception as e:
        logger.debug("cx_pick failed for %s: %s", eid, e)
        try:
            await el.press("Escape")
        except Exception:
            pass
        return False


# Locates the country-code control that belongs to a phone input, if any.
# Climbs at most 4 ancestors and stops once the group holds 2+ other plain
# inputs — past that point we've left the phone row and a "Country" select
# there is the ADDRESS country, not a dial code. A control counts as a dial
# picker by its name/id/label/aria/automation-id/autocomplete
# ("tel-country-code"), by a value that is just "+NN", or — for an unlabelled
# <select> — by options that are mostly "+NN" entries.
_FIND_DIAL_JS = r"""
const _dialRx = /(?:country|dial(?:l?ing)?|international|calling|phone)[\s_-]*(?:phone[\s_-]*)?code|dialcode|phonecode|countrycode|country-code|indicatif|vorwahl|prefijo|prefisso|tel-country-code/i;
const _lbl = (c) => {
  let t = '';
  if (c.id) { const l = document.querySelector('label[for="'+CSS.escape(c.id)+'"]'); if (l) t = l.textContent || ''; }
  return t + ' ' + (c.getAttribute('aria-label') || '');
};
const _isDial = (c) => {
  const s = [c.name, c.id, typeof c.className === 'string' ? c.className : '',
             c.getAttribute('data-automation-id'), c.getAttribute('autocomplete'),
             c.getAttribute('aria-labelledby') ? (document.getElementById(c.getAttribute('aria-labelledby'))||{}).textContent : '',
             _lbl(c)].join(' ');
  if (_dialRx.test(s)) return true;
  if (c.tagName === 'SELECT') {
    const o = Array.from(c.options).slice(0, 60);
    const k = o.filter((x) => /\+\s?\d{1,4}\b/.test(x.textContent || '')).length;
    return k >= 3 && k >= o.length * 0.5;
  }
  const v = String(c.tagName === 'INPUT' ? c.value : c.textContent || '').trim();
  return /^\+\d{1,4}$/.test(v) || /\(\+\d{1,4}\)\s*$/.test(v);
};
const findDial = (el) => {
  let n = el.parentElement;
  for (let i = 0; i < 4 && n; i++, n = n.parentElement) {
    const ctrls = Array.from(n.querySelectorAll(
      "select,input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]),button,[role=combobox]"))
      .filter((c) => c !== el && !c.contains(el) && !el.contains(c));
    // Check BEFORE accepting a hit: a level holding 2+ other plain inputs is
    // already past the phone row, and its pickers belong to other fields.
    // (A phone extension box is the one other input a real phone row may hold.)
    if (ctrls.filter((c) => (c.tagName === 'INPUT' || c.tagName === 'TEXTAREA') && !_isDial(c)).length >= 2) break;
    const hit = ctrls.find(_isDial);
    if (hit) return hit;
  }
  // Row-per-field layouts (SuccessFactors tables: "International Calling code"
  // and "Preferred Contact Number" are separate <tr>s) keep the picker outside
  // every ancestor of the number. Fall back to document-order neighbours: the
  // control just before (or after) the number, only if it is NAMED as a dial
  // code (an options-only guess is too loose this far out), and never across
  // another text field.
  const vis = (c) => { const r = c.getBoundingClientRect(); return r.width > 1 && r.height > 1; };
  const all = Array.from(document.querySelectorAll(
    "select,input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]),[role=combobox]"))
    .filter((c) => c === el || vis(c));
  const k = all.indexOf(el);
  const named = (c) => _dialRx.test([c.name, c.id, c.getAttribute('data-automation-id'),
                                     c.getAttribute('autocomplete'), _lbl(c)].join(' '));
  for (const step of [-1, -2, 1]) {
    const c = all[k + step];
    if (!c) continue;
    if (named(c)) return c;
    if (c.tagName === 'INPUT' && c.getAttribute('role') !== 'combobox') break;
  }
  return null;
};
const dialShown = (d) => {
  if (!d) return '';
  if (d.tagName === 'SELECT') {
    const o = d.options[d.selectedIndex];
    return ((o ? o.textContent : '') + ' ' + (d.value || '')).trim();
  }
  if (d.tagName === 'INPUT') return String(d.value || '');
  return String(d.textContent || '').trim().slice(0, 60);
};
"""

# Per-phone-input context + widget detection for the typing pass.
_PHONE_INFO_JS = r"""
el => {
""" + _FIND_DIAL_JS + r"""
  const r = el.getBoundingClientRect();
  let label = '';
  if (el.id) { const l = document.querySelector('label[for="'+CSS.escape(el.id)+'"]'); if (l) label = l.textContent || ''; }
  const wl = el.closest('label'); if (wl) label += ' ' + (wl.textContent || '');
  const ctx = [el.name||'', el.id||'', el.placeholder||'', el.getAttribute('aria-label')||'',
               el.getAttribute('autocomplete')||'', label].join(' ').toLowerCase().replace(/\s+/g,' ');
  // intl-tel-input (and clones) manage a country flag internally.
  const iti = !!el.closest('.iti, .intl-tel-input, [class*="intl-tel"], [class*="PhoneInput"], .react-tel-input');
  // a separate dial-code control means this field wants the national number.
  const dial = iti ? null : findDial(el);
  // Can this input hold a '+'? type=number / inputmode=numeric / a pattern that
  // rejects '+33…' but accepts '07…' all mean digits only.
  let noPlus = el.type === 'number' || el.getAttribute('inputmode') === 'numeric';
  const pat = el.getAttribute('pattern');
  if (pat && !noPlus) {
    try { const p = new RegExp('^(?:' + pat + ')$');
          noPlus = !p.test('+33612345678') && !p.test('+33 6 12 34 56 78') && p.test('0612345678'); } catch (e) {}
  }
  // intl-tel-input pre-fills the field with just the dial code ("+33 "); treat
  // a value that is only a country/dial code as still empty so we fill it.
  const v = (el.value||'').trim();
  const emptyish = !v || /^\+?\d{0,4}[\s().+-]*$/.test(v);
  return {ctx: ctx.slice(0,250), nameId: ((el.name||'')+' '+(el.id||'')).toLowerCase(),
          visible: r.width>1 && r.height>1 && !el.disabled && !el.readOnly,
          empty: emptyish, filledFlag: el.dataset.idbFilled==='1',
          iti, hasDial: !!dial, dialVal: dialShown(dial),
          // Only plain <select>/<input> pickers are set here; Workday buttons and
          // Oracle cx comboboxes are driven by their own passes.
          dialSettable: !!dial && (dial.tagName === 'SELECT' ||
                        (dial.tagName === 'INPUT' && !String(dial.className||'').includes('cx-select')
                         && dial.getAttribute('role') !== 'combobox')),
          maxLen: el.maxLength > 0 ? el.maxLength : 0, noPlus,
          sig: !!el.closest("[class*='esign'],[class*='signatur']")};
}
"""

# Point the phone input's own country-code picker at the profile's code.
# <select>: the option showing "+33" (not "+330"/"+233"), preferring one that
# also names the country (+44 is shared by UK/Jersey/Guernsey/Isle of Man).
# Text input: "+33". Fill-only-empty is relaxed for this one control on purpose:
# its "value" is usually a page default (+1, +44), not something the user chose,
# and leaving it would put the national number under the wrong country.
_SET_DIAL_JS = r"""
(el, a) => {
""" + _FIND_DIAL_JS + r"""
  const d = findDial(el);
  if (!d) return '';
  const setNative = (x, val) => {
    const proto = x.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(x, val);
    x.dispatchEvent(new Event('input', {bubbles: true}));
    x.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const codeRx = new RegExp('(?:^|[^\\d])\\+?\\s?' + a.dial + '(?!\\d)');
  if (d.tagName === 'SELECT') {
    let best = null, bestS = 0;
    for (const o of Array.from(d.options)) {
      const t = ((o.textContent || '') + ' ').toLowerCase(), v = String(o.value || '').toLowerCase();
      const plus = new RegExp('\\+\\s?' + a.dial + '(?!\\d)').test(t + ' ' + v);
      const bare = v === a.dial || v === '+' + a.dial || t.trim() === a.dial;
      const named = a.names.some((n) => t.includes(n));
      const s = (plus || bare ? 2 : 0) + (named ? 1 : 0) + (named && a.names[0] && t.includes(a.names[0]) ? 1 : 0);
      if ((plus || bare) && s > bestS) { bestS = s; best = o; }
    }
    if (best) setNative(d, best.value);
  } else if (d.tagName === 'INPUT') {
    if (!codeRx.test(String(d.value || ''))) setNative(d, '+' + a.dial);
  }
  return dialShown(d);
}
"""


# What country an intl-tel-input widget currently shows: separate dial-code
# text, the selector's title ("France: +33" / "France (+33)") and its flag class
# ("iti__fr"). '' when no country is VISIBLE to the user — a flagless widget
# would leave a national number with no visible country, so we never convert it.
# Greenhouse hides the widget's own button (0x0) and mirrors the country in a
# separate React "Country" box ("🇫🇷 +33") in the same phone row; that counts.
_ITI_DIAL_JS = r"""el => {
  const it = el.closest('.iti'); if (!it) return '';
  const b = it.querySelector('.iti__selected-country, .iti__selected-flag');
  if (!b) return '';
  const vis = (x) => { const r = x.getBoundingClientRect(); return r.width > 1 && r.height > 1; };
  let shown = vis(b);
  for (let n = it.parentElement, i = 0; !shown && n && i < 4; n = n.parentElement, i++) {
    shown = Array.from(n.querySelectorAll('.iti__flag, span, div')).some((x) =>
      !b.contains(x) && !it.contains(x) && vis(x) &&
      (x.classList.contains('iti__flag') || /^\+\d{1,4}$/.test((x.textContent || '').trim())));
  }
  if (!shown) return '';
  const dc = it.querySelector('.iti__selected-dial-code');
  const flag = b.querySelector('.iti__flag');
  const iso = flag ? (Array.from(flag.classList).find((c) => /^iti__[a-z]{2}$/.test(c)) || '').slice(5) : '';
  return [(dc && dc.textContent) || '', b.getAttribute('title') || '', iso ? 'iso:' + iso : ''].join(' ');
}"""
_ISO_DIAL = {"fr": "33", "gb": "44", "ma": "212", "de": "49", "ch": "41", "it": "39",
             "es": "34", "be": "32", "nl": "31", "lu": "352", "ie": "353", "pt": "351",
             "us": "1", "at": "43"}


def _iti_shown_dial(raw: str) -> str:
    m = re.search(r"iso:([a-z]{2})", raw or "")
    return _dial_from_text(re.sub(r"iso:[a-z]{2}", "", raw or "")) or \
        (_ISO_DIAL.get(m.group(1), "") if m else "")


async def _iti_to_national(frame: Frame, el: ElementHandle, intl: str, dial_d: str) -> None:
    """intl-tel-input shows its own country selector (Greenhouse, Teamtailor).
    The international number was typed first so the widget picks the country
    itself; once its selector shows OUR code, retype the box as the national
    number — the user's rule for any form with a separate country box. If the
    widget then drops the country, put the international number back."""
    try:
        # The flag switches asynchronously after the keystrokes (Teamtailor
        # takes ~0.5-1s), so poll briefly instead of reading once.
        for _ in range(6):
            if _iti_shown_dial(await el.evaluate(_ITI_DIAL_JS)) == dial_d:
                break
            await frame.wait_for_timeout(_w(300))
        else:
            return
        await el.fill("")
        await el.type(_national_display(intl), delay=40)
        await frame.wait_for_timeout(_w(400))
        if _iti_shown_dial(await el.evaluate(_ITI_DIAL_JS)) != dial_d:
            await el.fill("")
            await el.type(intl, delay=40)
            await frame.wait_for_timeout(_w(300))
    except Exception as e:
        logger.debug("iti national retype failed: %s", e)


async def _fill_phone_fields(frame: Frame, state: dict) -> int:
    """Type phone numbers into tel/phone inputs. Typing (not value-set) is
    required for intl-tel-input-style widgets, which reformat on keystrokes and
    would otherwise keep only the dial code. The format per field is decided by
    _phone_value: national ('0612345678') beside a separate country-code picker
    (set to the profile's code first), international elsewhere.

    intl-tel-input finishes initializing a beat after page load and resets the
    field to just its dial code — so a first-pass type can be wiped. We verify
    the value stuck (>=6 digits) and, if not, retry on later passes rather than
    marking the field done."""
    phone_rules = [r for r in state.get("py_rules", []) if r[3] == "phone"]
    if not phone_rules:
        return 0
    rx_c, intl, _alts, _kind = phone_rules[0]
    done: set = state.setdefault("phone_done", set())
    attempts: dict = state.setdefault("phone_attempts", {})
    filled = 0
    try:
        els = await frame.query_selector_all(
            "input[type='tel'], input[type='text']:not(.cx-select-input):not(.rcmpaginatedselectinput), "
            "input:not([type])")
    except Exception:
        return 0
    for el in els:
        try:
            info = await el.evaluate(_PHONE_INFO_JS)
        except Exception:
            continue
        if not info["visible"] or not info["empty"] or info["filledFlag"] or info["sig"]:
            continue
        if not rx_c.search(info["ctx"]) or re.search(r"honey", info["ctx"]):
            continue
        # A dial-code selector is named countryCode/dialCode/phoneCode — a real
        # phone-number field is named phone/mobile/tel even if its help text
        # mentions "country code". Guard on the name/id, not the help text.
        if re.search(r"(?:country|dial|phone)[\s_-]*code|dialcode|indicatif",
                     info.get("nameId", "")):
            continue
        key = (re.split(r"[?#]", frame.url or "")[0][:80]) + "::" + info["ctx"][:60]
        if key in done:
            continue
        if attempts.get(key, 0) >= 4:
            done.add(key)  # give up after several tries; leave for the user
            continue
        attempts[key] = attempts.get(key, 0) + 1
        dial_d, _nsn = _split_phone(intl)
        # A separate code picker showing another country (page default +1/+44):
        # point it at ours first when it's a plain select/input; Workday and
        # Oracle pickers are set by their own passes, so just retry next pass.
        if (info.get("hasDial") and dial_d and info.get("dialSettable")
                and _dial_from_text(info.get("dialVal", "")) != dial_d):
            try:
                info["dialVal"] = await el.evaluate(
                    _SET_DIAL_JS, {"dial": dial_d, "names": _DIAL_COUNTRIES.get(dial_d, [])})
            except Exception:
                pass
        value = _phone_value(intl, info)
        if value is None:
            if attempts[key] >= 4:
                logger.info("phone: country-code picker stuck on %r (profile +%s) — "
                            "number left for the user", info.get("dialVal"), dial_d)
            continue
        try:
            await el.scroll_into_view_if_needed(timeout=2000)
            await el.click(timeout=2000)
            await el.fill("")
            await el.type(value, delay=40)
            await frame.wait_for_timeout(_w(400))
            if info.get("iti") and value == intl and dial_d:
                await _iti_to_national(frame, el, intl, dial_d)
            got = await el.input_value()
            if len(re.sub(r"\D", "", got or "")) >= 6:
                await el.evaluate("el => el.dataset.idbFilled = '1'")
                done.add(key)
                filled += 1
            # else: widget reset it — retry on a later pass
        except Exception:
            continue
    return filled


async def _fill_cx_comboboxes(frame: Frame, py_rules: list, state: dict,
                              limit: int = 2) -> int:
    """Generic pass over Oracle cx-select comboboxes outside entry editors
    (postal code, gender, dial code...). At most `limit` per pass to keep the
    fill loop responsive."""
    done: set = state.setdefault("cx_done", set())
    filled = 0
    try:
        els = await frame.query_selector_all("input.cx-select-input")
    except Exception:
        return 0
    for el in els:
        if filled >= limit:
            break
        try:
            info = await el.evaluate("""el => {
                const fb = el.closest('form-builder');
                const inEditor = !!(fb && fb.querySelector(
                  "input[name='educationalEstablishment'],input[name='employerName'],input[name='contentItemId']"));
                let label = '';
                if (el.id) {
                  const l = document.querySelector('label[for="'+CSS.escape(el.id)+'"]');
                  if (l) label = l.textContent || '';
                }
                const r = el.getBoundingClientRect();
                return {id: el.id||'', name: el.name||'', label, value: el.value||'',
                        visible: r.width>1 && r.height>1, inEditor,
                        sig: !!el.closest("[class*='esign'],[class*='signatur']")};
            }""")
            key = info["id"] or info["name"]
            if (not info["visible"] or info["value"] or info["inEditor"]
                    or info["sig"] or not key or key in done
                    or info["name"] in _EDITOR_CX_NAMES):
                continue
            ctx = _norm(f"{info['name']} {info['id']} {info['label']}")
            if re.search(r"honey|signatur", ctx):
                done.add(key)
                continue
            matched = False
            for rx_c, val, alts, kind in py_rules:
                if not rx_c.search(ctx) or kind == "date":
                    continue
                matched = True
                type_text = None if kind == "choice" else val
                if await _cx_pick(frame, el, type_text, [val] + (alts or [])):
                    filled += 1
                break
            done.add(key)  # one attempt per field per assist
            if not matched:
                continue
        except Exception:
            continue
    return filled


# --- Workday button/listbox dropdowns ---------------------------------------
# Workday (Citi, MS, Santander, BBVA, Euronext, RBC, CMC…) renders most single
# selects as a <button aria-haspopup="listbox"> that opens a portal listbox of
# <div data-automation-id="promptOption">. These aren't <select>/radio/pill, so
# the generic JS never touches them — this handler drives them like the Oracle
# combobox pass: match the button's context, open, pick the option, click it.
_WD_DROPDOWN_SEL = (
    "button[aria-haspopup='listbox'],"
    "button[data-automation-id$='Dropdown'],"
    "button[data-automation-id='countryDropdown'],"
    "button[data-automation-id*='ountryPhoneCode']"
)
_WD_OPTION_SEL = (
    "[data-automation-id='promptOption'],"
    "div[data-automation-id='activeListContainer'] [role='option'],"
    "ul[role='listbox'] [role='option']"
)
_WD_PLACEHOLDERS = {
    "", "select one", "select", "select...", "select an option", "select a value",
    "choose", "choose one", "search", "make a selection", "-",
}
_WD_INFO_JS = r"""el => {
    let label = '';
    let node = el.parentElement;
    for (let i = 0; i < 5 && node; i++, node = node.parentElement) {
        if (node.querySelectorAll("button[aria-haspopup],input:not([type=hidden]),select").length > 1) break;
        const l = node.querySelector("label,legend,[id$='label'],[class*='label']");
        if (l && (l.textContent||'').trim()) { label = l.textContent; break; }
    }
    const r = el.getBoundingClientRect();
    return {aid: el.getAttribute('data-automation-id')||'', al: el.getAttribute('aria-label')||'',
            id: el.id||'', label, text: (el.textContent||'').trim(),
            visible: r.width > 1 && r.height > 1 && !el.disabled,
            sig: !!el.closest("[class*='esign'],[class*='signatur']")};
}"""


async def _fill_workday_dropdowns(frame: Frame, py_rules: list, state: dict,
                                  limit: int = 2) -> int:
    """Fill Workday button/listbox dropdowns (country, gender, phone code,
    source, yes/no…) that match a rule. At most `limit` per pass."""
    done: set = state.setdefault("wd_done", set())
    filled = 0
    try:
        btns = await frame.query_selector_all(_WD_DROPDOWN_SEL)
    except Exception:
        return 0
    for btn in btns:
        if filled >= limit:
            break
        try:
            info = await btn.evaluate(_WD_INFO_JS)
            key = info["aid"] or info["id"] or info["al"]
            if not key or key in done or not info["visible"] or info["sig"]:
                continue
            text_l = info["text"].lower().strip()
            if text_l and text_l not in _WD_PLACEHOLDERS:
                done.add(key)          # already has a value — leave it
                continue
            ctx = _norm(f"{info['aid']} {info['al']} {info['id']} {info['label']}")
            if re.search(r"honey|signatur", ctx):
                done.add(key)
                continue
            cands = None
            for rx_c, val, alts, kind in py_rules:
                if kind == "date" or not rx_c.search(ctx):
                    continue
                cands = [c for c in ([val] + (alts or [])) if c]
                break
            done.add(key)              # one attempt per field per assist
            if not cands:
                continue
            if await _wd_pick(frame, btn, cands):
                filled += 1
        except Exception:
            continue
    return filled


async def _wd_pick(frame: Frame, btn: ElementHandle, cands: list[str]) -> bool:
    """Open a Workday dropdown and click the option best matching `cands`.
    Long lists (country) expose a search box — type the value to filter first.
    Always closes the popup on failure so it can't block later fields."""
    try:
        await btn.scroll_into_view_if_needed()
    except Exception:
        pass
    try:
        await btn.click()
        await frame.wait_for_timeout(_w(300))
        # Filter box for long lists (country ~200 options are virtualized).
        try:
            search = await frame.query_selector(
                "input[data-automation-id='searchBox'],"
                "div[data-automation-id='activeListContainer'] input[type='text']")
            if search:
                await search.fill(cands[0][:40])
                await frame.wait_for_timeout(_w(500))
        except Exception:
            pass
        opts = await frame.query_selector_all(_WD_OPTION_SEL)
        pairs = []
        for o in opts:
            try:
                t = (await o.inner_text()).strip()
                if t:
                    pairs.append((o, t))
            except Exception:
                continue
        best = _pick_text([t for _, t in pairs], cands)
        if best is not None:
            for o, t in pairs:
                if t == best:
                    await o.click()
                    await frame.wait_for_timeout(_w(300))
                    return True
    except Exception as e:
        logger.debug("wd_pick failed: %s", e)
    try:
        await frame.keyboard.press("Escape")
    except Exception:
        pass
    return False


# --- SuccessFactors (RCM) picklists ----------------------------------------
# HSBC, Mizuho and every SAP SuccessFactors career site render each dropdown as
# <input class="rcmpaginatedselectinput" role="combobox" placeholder="No
# Selection"> + a hidden real field; typing filters a <ul aria-owns> of
# <li role="option">. Options can be terse (the calling-code list is just
# "+1", "+33", "+233"…), so the value is typed and the exact option clicked.
_SF_PICK_SEL = "input.rcmpaginatedselectinput"
_SF_INFO_JS = r"""el => {
  let label = el.getAttribute('aria-label') || '';
  const tr = el.closest('tr'); const th = tr && tr.querySelector('th,label');
  if (th) label += ' ' + th.textContent;
  const r = el.getBoundingClientRect();
  return {key: el.id || label.slice(0, 60), label: label.replace(/\s+/g, ' ').trim().slice(0, 250),
          value: (el.value || '').trim(), placeholder: el.getAttribute('placeholder') || '',
          visible: r.width > 1 && r.height > 1 && !el.disabled,
          sig: !!el.closest("[class*='esign'],[class*='signatur']")};
}"""
_SF_OPTS_JS = r"""el => {
  const l = document.getElementById(el.getAttribute('aria-owns') || '');
  const scope = l || document;
  return Array.from(scope.querySelectorAll("li[role=option]"))
    .filter(o => o.getBoundingClientRect().height > 0).map(o => (o.textContent || '').trim());
}"""


async def _fill_sf_picklists(frame: Frame, py_rules: list, state: dict,
                             limit: int = 3) -> int:
    """Fill empty SuccessFactors picklists that match a rule (calling code,
    country, gender, yes/no…). One attempt per field per assist."""
    done: set = state.setdefault("sf_done", set())
    filled = 0
    try:
        els = await frame.query_selector_all(_SF_PICK_SEL)
    except Exception:
        return 0
    for el in els:
        if filled >= limit:
            break
        try:
            info = await el.evaluate(_SF_INFO_JS)
            key = info["key"]
            if not key or key in done or not info["visible"] or info["sig"]:
                continue
            if info["value"] and info["value"].lower() != "no selection":
                done.add(key)          # already chosen — leave it
                continue
            ctx = _norm(info["label"])
            if re.search(r"honey|signatur", ctx):
                done.add(key)
                continue
            cands = None
            for rx_c, val, alts, kind in py_rules:
                if kind in ("date", "phone") or not rx_c.search(ctx):
                    continue
                cands = [c for c in ([val] + (alts or [])) if c]
                break
            done.add(key)
            if not cands:
                continue
            if await _sf_pick(frame, el, cands):
                filled += 1
        except Exception:
            continue
    return filled


async def _sf_pick(frame: Frame, el: ElementHandle, cands: list[str]) -> bool:
    """Type each candidate into the picklist and click the best option."""
    try:
        await el.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    for c in cands:
        try:
            await el.click(timeout=2000)
            await el.fill("")
            await el.type(c[:40], delay=40)
            await frame.wait_for_timeout(_w(1200))
            opts = await el.evaluate(_SF_OPTS_JS)
            best = _pick_text([o for o in opts if o.lower() != "no selection"], [c])
            if best is None:
                continue
            opt = frame.locator("li[role=option]", has_text=best)
            n = await opt.count()
            for i in range(n):
                o = opt.nth(i)
                if (await o.inner_text()).strip() == best and await o.is_visible():
                    await o.click(timeout=2000)
                    await frame.wait_for_timeout(_w(400))
                    return True
        except Exception as e:
            logger.debug("sf_pick failed on %r: %s", c, e)
    # Nothing matched: clear what we typed so the field reads "No Selection"
    # again instead of holding stray filter text.
    try:
        await el.fill("")
        await frame.keyboard.press("Escape")
        await el.evaluate("e => e.blur()")   # SF keeps the list open while focused
    except Exception:
        pass
    return False


async def _set_native(el: ElementHandle, value: str) -> None:
    await el.evaluate("""(el, v) => {
        const proto = el instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const d = Object.getOwnPropertyDescriptor(proto, 'value');
        if (d && d.set) d.set.call(el, v); else el.value = v;
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
    }""", value)


def _month_year(ym: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    m = re.match(r"^(\d{4})-(\d{1,2})", ym or "")
    if not m:
        return None, None
    return _MONTHS[int(m.group(2))], m.group(1)


async def _editor_set_text(editor: ElementHandle, name: str, value: Optional[str]):
    if not value:
        return
    el = await editor.query_selector(f"input[name='{name}'], textarea[name='{name}']")
    if el:
        await _set_native(el, value)


async def _editor_set_cx(frame: Frame, editor: ElementHandle, selector: str,
                         text: Optional[str], cands: list[str]) -> bool:
    el = await editor.query_selector(selector)
    if not el:
        return False
    return await _cx_pick(frame, el, text, cands)


async def _editor_set_date(frame: Frame, editor: ElementHandle, field: str,
                           ym: Optional[str]):
    month, year = _month_year(ym)
    if not year:
        return
    if month:
        await _editor_set_cx(frame, editor, f"input[id^='month-{field}']",
                             None, [month, month[:3]])
    await _editor_set_cx(frame, editor, f"input[id^='year-{field}']", year, [year])


# Field container carrying a label that matches `label_rx`, restricted to one
# widget kind. Returns the container's classification: whether it holds a
# cx-select input (Python-driven), a native <select>, or a pill group.
_CLASSIFY_JS = r"""
([labelRx, valueCands]) => {
  const rx = new RegExp(labelRx, "i");
  const norm = (s) => (s || "").toLowerCase().replace(/\s+/g, " ").trim();
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pick = (texts) => {
    const cands = valueCands.map(norm).filter(Boolean);
    let best = -1, bestScore = 0;
    texts.forEach((t0, i) => {
      const t = norm(t0);
      if (!t) return;
      for (const c of cands) {
        let s = 0;
        if (t === c) s = 3;
        else if (new RegExp("(?<![\\w])" + esc(c) + "(?![\\w])", "i").test(t)) s = 2;
        else if ((c.length >= 5 && t.includes(c)) || (t.length >= 5 && c.includes(t))) s = 1;
        if (s > bestScore) { bestScore = s; best = i; }
      }
    });
    return best;
  };
  const editor = window.__idbEditor;
  if (!editor) return {kind: "none"};
  // Locate the field container whose label matches.
  const labels = editor.querySelectorAll("label, legend, [class*='label']");
  for (const lab of labels) {
    if (!rx.test(lab.textContent || "")) continue;
    // climb to a container that also holds the input/pills
    let cont = lab.parentElement;
    for (let i = 0; i < 4 && cont; i++, cont = cont.parentElement) {
      const cx = cont.querySelector("input.cx-select-input");
      const sel = cont.querySelector("select");
      const pills = cont.querySelectorAll("button[class*='pill']");
      if (cx) return {kind: "cx", id: cx.id || ""};
      if (sel) {
        const opts = Array.from(sel.options).map((o) => o.textContent || "");
        const idx = pick(opts);
        if (idx >= 0) {
          const d = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
          d.set.call(sel, sel.options[idx].value);
          sel.dispatchEvent(new Event("input", {bubbles: true}));
          sel.dispatchEvent(new Event("change", {bubbles: true}));
          return {kind: "select", ok: true};
        }
        return {kind: "select", ok: false};
      }
      if (pills.length >= 2) {
        const arr = Array.from(pills);
        const idx = pick(arr.map((b) => b.textContent || ""));
        if (idx >= 0) { arr[idx].click(); return {kind: "pill", ok: true}; }
        return {kind: "pill", ok: false};
      }
    }
  }
  return {kind: "none"};
}
"""


async def _editor_set_classification(frame: Frame, editor: ElementHandle,
                                     label_rx: str, cands: list[str]) -> bool:
    """Fill a classification field (e.g. Oracle's required 'Degree' dropdown)
    inside an entry editor, whatever widget it uses — cx-select, native select,
    or pill group — matched by its label text."""
    cands = [c for c in cands if c]
    if not cands:
        return False
    try:
        await editor.evaluate("el => { window.__idbEditor = el; }")
        res = await frame.evaluate(_CLASSIFY_JS, [label_rx, cands])
    except Exception as e:
        logger.debug("classification probe failed: %s", e)
        return False
    kind = res.get("kind")
    if kind in ("select", "pill"):
        return bool(res.get("ok"))
    if kind == "cx" and res.get("id"):
        el = await editor.query_selector(f"#{res['id']}")
        if el:
            return await _cx_pick(frame, el, cands[0], cands)
    return False


_LANG_PILLS_JS = """(editor, cfg) => {
    const groups = new Map();
    for (const b of editor.querySelectorAll("button[class*='pill']")) {
      let p = b.parentElement;
      for (let i = 0; i < 3 && p; i++, p = p.parentElement) {
        if (p.querySelectorAll("button[class*='pill']").length >= 2) break;
      }
      if (!p) continue;
      if (!groups.has(p)) groups.set(p, []);
      groups.get(p).push(b);
    }
    let clicked = 0;
    for (const [p, btns] of groups) {
      if (btns.some(b => b.getAttribute('aria-pressed') === 'true')) continue;
      let q = '';
      let n = p;
      for (let i = 0; i < 4 && n && n !== editor.parentElement; i++, n = n.parentElement) {
        if (i > 0 && n.querySelectorAll("button[class*='pill']").length > btns.length) break;
        const lab = n.querySelector("label, legend, [class*='label']");
        if (lab && lab.textContent.trim()) { q = lab.textContent.toLowerCase(); break; }
      }
      let want = null;
      if (/read|writ|speak|lecture|écrit|oral/.test(q)) want = cfg.level;
      else if (/native|maternelle/.test(q)) want = cfg.native;
      if (!want) continue;
      for (const b of btns) {
        if ((b.textContent||'').trim().toLowerCase() === want.toLowerCase()) {
          b.click(); clicked++; break;
        }
      }
    }
    return clicked;
}"""

# Proficiency mapping onto Oracle's High/Moderate/Low pill scale.
_LANG_LEVELS = {"native": "High", "fluent": "High", "proficient": "High",
                "advanced": "High", "bilingual": "High",
                "intermediate": "Moderate", "conversational": "Moderate",
                "basic": "Low", "beginner": "Low"}

_ORACLE_SECTIONS = [
    # (Add-button text, extras key, editor probe field)
    ("EDUCATION", "education", "educationalEstablishment"),
    ("EXPERIENCE", "experience", "employerName"),
    ("LANGUAGE", "languages", "contentItemId"),
]

_COUNTRY_ALIASES = {
    "france": ["france"], "morocco": ["morocco", "maroc"], "italy": ["italy", "italie"],
    "united kingdom": ["united kingdom", "uk"], "switzerland": ["switzerland", "suisse"],
    "germany": ["germany", "allemagne"], "spain": ["spain", "espagne"],
}


async def _find_button(frame: Frame, text: str, exclude_id_prefix: str = "",
                       only_id_prefix: str = "") -> Optional[ElementHandle]:
    """Visible button whose text matches (case-insensitive), with id filters."""
    want = _norm(text)
    for b in await frame.query_selector_all("button"):
        try:
            t = _norm(await b.inner_text())
            if t != want:
                continue
            bid = (await b.get_attribute("id")) or ""
            if exclude_id_prefix and bid.startswith(exclude_id_prefix):
                continue
            if only_id_prefix and not bid.startswith(only_id_prefix):
                continue
            box = await b.bounding_box()
            if box and box["width"] > 1:
                return b
        except Exception:
            continue
    return None


async def _fill_oracle_entries(frame: Frame, extras: dict, state: dict) -> int:
    """Fill Oracle CX Education/Experience/Language sections from structured
    profile entries. Opens the inline editor, fills it, saves — one entry per
    pass so the fill loop stays responsive. Skips sections that already have
    entries (never edits or deletes what's there)."""
    st = state.setdefault("oracle_entries", {})
    for add_label, key, probe in _ORACLE_SECTIONS:
        entries = extras.get(key) or []
        sec = st.setdefault(key, {"added": 0, "stop": False})
        if not entries or sec["stop"] or sec["added"] >= len(entries):
            continue
        add_btn = await _find_button(frame, f"ADD {add_label}",
                                     only_id_prefix="profileItemsAddButton")
        if add_btn is None:
            continue  # section not on this page/step
        if sec["added"] == 0:
            # Existing entries (e.g. from a resume import)? Leave the section
            # alone rather than piling duplicates on top.
            n_existing = await add_btn.evaluate("""btn => {
                const block = btn.closest("[class*='apply-flow-block']") || btn.parentElement;
                if (!block) return 0;
                // Count entry tiles only — not their BEM children (tile__summary
                // etc.) and not the Add tile itself.
                return [...block.querySelectorAll("[class*='profile-item-tile']")]
                  .filter(el => !/tile__|new-tile/.test(String(el.className))).length;
            }""")
            if n_existing:
                logger.info("oracle %s: %d existing entries — leaving as is", key, n_existing)
                sec["stop"] = True
                continue
        entry = entries[sec["added"]]
        try:
            await add_btn.scroll_into_view_if_needed()
            await add_btn.click()
            await frame.wait_for_selector(f"input[name='{probe}']", state="visible",
                                          timeout=8000)
            await frame.wait_for_timeout(_w(800))
            probe_el = await frame.query_selector(f"input[name='{probe}']")
            editor_h = await probe_el.evaluate_handle(
                "el => el.closest('form-builder') || el.closest('form') || el.parentElement")
            editor = editor_h.as_element()
            if editor is None:
                raise RuntimeError("no editor container")

            country = entry.get("country") or ""
            c_cands = _COUNTRY_ALIASES.get(country.lower(), [country]) if country else []

            if key == "education":
                # Degree classification (required, controlled list) BEFORE the
                # free-text Major — a rejected required field aborts the save.
                level = entry.get("degree_level")
                if level:
                    await _editor_set_classification(
                        frame, editor, r"degree|dipl[ôo]me|niveau", [level])
                await _editor_set_text(editor, "major",
                                       entry.get("field") or entry.get("degree"))
                # School is a controlled vocabulary; match with specific aliases
                # and leave blank rather than pick the wrong institution.
                school = entry.get("school") or ""
                s_cands = [school] + (entry.get("school_aliases") or [])
                await _editor_set_cx(frame, editor, "input[name='educationalEstablishment']",
                                     school, s_cands)
                await _editor_set_cx(frame, editor, "input[name='countryCode']",
                                     country, c_cands)
                await _editor_set_text(editor, "city", entry.get("city"))
                await _editor_set_date(frame, editor, "dateAcquired", entry.get("end"))
            elif key == "experience":
                await _editor_set_text(editor, "employerName", entry.get("employer"))
                await _editor_set_text(editor, "jobTitle", entry.get("title"))
                await _editor_set_date(frame, editor, "startDate", entry.get("start"))
                await _editor_set_date(frame, editor, "endDate", entry.get("end"))
                await _editor_set_cx(frame, editor, "input[name='countryCode']",
                                     country, c_cands)
                await _editor_set_text(editor, "employerCity", entry.get("city"))
                await _editor_set_text(editor, "responsibilities", entry.get("description"))
            else:  # languages
                lang = entry.get("language") or ""
                await _editor_set_cx(frame, editor, "input[name='contentItemId']",
                                     lang, [lang])
                level = _LANG_LEVELS.get(_norm(entry.get("level") or ""), "High")
                native = "Yes" if _norm(entry.get("level") or "") == "native" else "No"
                await editor.evaluate(_LANG_PILLS_JS, {"level": level, "native": native})

            await frame.wait_for_timeout(_w(500))
            save_btn = await _find_button(frame, f"ADD {add_label}",
                                          exclude_id_prefix="profileItemsAddButton")
            if save_btn is None:
                raise RuntimeError("no save button")
            await save_btn.click()
            try:
                await frame.wait_for_selector(f"input[name='{probe}']", state="hidden",
                                              timeout=8000)
                sec["added"] += 1
                logger.info("oracle %s: saved entry %d/%d", key, sec["added"], len(entries))
                return 1
            except Exception:
                # Editor still open — validation refused it. Cancel so the rest
                # of the form stays usable; user finishes this entry by hand.
                err = ""
                try:
                    err = await editor.evaluate(
                        "el => (el.querySelector(\"[class*='error']\") || {}).textContent || ''")
                except Exception:
                    pass
                logger.warning("oracle %s: entry %d rejected (%s) — cancelling",
                               key, sec["added"] + 1, err.strip()[:120])
                cancel = await _find_button(frame, "CANCEL")
                if cancel:
                    await cancel.click()
                    await frame.wait_for_timeout(_w(800))
                sec["stop"] = True
                return 0
        except Exception as e:
            logger.warning("oracle %s: entry fill failed: %s", key, e)
            cancel = await _find_button(frame, "CANCEL")
            if cancel:
                try:
                    await cancel.click()
                except Exception:
                    pass
            sec["stop"] = True
            return 0
    return 0


# ---------------------------------------------------------------------------
# The fill pass.
# ---------------------------------------------------------------------------

async def fill_pass(
    page: Page,
    js_rules: list,
    cv_path: Optional[str],
    cl_path: Optional[str],
    extra_docs: Optional[list[dict]],
    state: dict,
    profile: Optional[dict] = None,
) -> int:
    """One fill pass over every frame of the page: dismiss known cookie
    banners, fill matching empty fields, click matching pills, drive Oracle
    comboboxes/entry editors, route documents into file inputs.
    Returns the number of fields filled/uploaded this pass."""
    filled_total = 0
    uploads_done: set = state.setdefault("uploads_done", set())
    if "py_rules" not in state:
        state["py_rules"] = [(re.compile(rx, re.I), val, alts, kind)
                             for rx, val, alts, kind in js_rules]
    extras = (profile or {}).get("extras_json") or {}
    doc_names = sorted({Path(p).name for p in (cv_path, cl_path) if p} |
                       {Path(d["path"]).name for d in (extra_docs or []) if d.get("path")})
    for frame in page.frames:
        try:
            await frame.evaluate(_CONSENT_JS)
        except Exception:
            pass
        try:
            filled = await frame.evaluate(_FILL_JS, js_rules)
            filled_total += filled or 0
        except Exception:
            continue  # cross-origin frame mid-navigation etc.

        # Phone fields: typed (not value-set) so intl-tel-input widgets format.
        try:
            filled_total += await _fill_phone_fields(frame, state)
        except Exception as e:
            logger.debug("phone pass error: %s", e)

        # Oracle CX extras: comboboxes + entry editors.
        try:
            if await frame.query_selector("input.cx-select-input, button[class*='cx-select-pill']"):
                filled_total += await _fill_cx_comboboxes(frame, state["py_rules"], state)
                if extras:
                    filled_total += await _fill_oracle_entries(frame, extras, state)
        except Exception as e:
            logger.debug("oracle pass error: %s", e)

        # Workday button/listbox dropdowns (country, gender, source, phone code…).
        try:
            if await frame.query_selector(_WD_DROPDOWN_SEL):
                filled_total += await _fill_workday_dropdowns(frame, state["py_rules"], state)
        except Exception as e:
            logger.debug("workday pass error: %s", e)

        # SuccessFactors picklists (HSBC, Mizuho: calling code, yes/no…).
        try:
            if await frame.query_selector(_SF_PICK_SEL):
                filled_total += await _fill_sf_picklists(frame, state["py_rules"], state)
        except Exception as e:
            logger.debug("successfactors pass error: %s", e)

        # File uploads: CV into resume-ish inputs, CL into cover-letter-ish,
        # extra documents into "other/additional document" ones.
        try:
            finputs = await frame.query_selector_all("input[type='file']")
        except Exception:
            continue
        if not finputs:
            continue
        # Upload bookkeeping is keyed on the FRAME's URL without query/hash:
        # SPA ATSes (Oracle HCM, Workday) mutate query/hash between steps of
        # the same form, and a full-page-URL key would reset the dedupe cap —
        # that's how the same CV ended up attached several times.
        frame_key = re.split(r"[?#]", frame.url or page.url or "")[0][:150]
        # A document whose filename is already visible in the frame (the
        # uploaded-file chip) is attached — never upload it again here, even
        # if the ATS re-rendered a fresh empty input.
        try:
            visible_names = set(await frame.evaluate(_VISIBLE_NAMES_JS, doc_names))
        except Exception:
            visible_names = set()
        misc_paths = [d["path"] for d in (extra_docs or [])
                      if d.get("path") and Path(d["path"]).exists()]

        # Forms whose "cover letter" is a TEXT box (Teamtailor) have no CL file
        # slot; the tailored letter then goes into "Additional files" rather
        # than never reaching the application. Filled in once `routable` is known.
        cl_to_misc: list = []

        def _route(ctx: str) -> Optional[list]:
            if any(w in ctx for w in _CL_WORDS):
                return [cl_path] if cl_path else []
            if _CV_RX.search(ctx):
                return [cv_path] if cv_path else []
            if _MISC_RX.search(ctx):
                return list(misc_paths) + cl_to_misc
            return None

        routable = []
        for finput in finputs:
            try:
                mk = await finput.evaluate(_MARKER_JS)
                if _IMPORT_RX.search(mk.get("attrs") or "") or \
                        _IMPORT_RX.search(mk.get("tight") or ""):
                    continue  # resume-parser import widget — never feed it
                routable.append((finput, mk))
            except Exception:
                continue
        if cl_path and not any(any(w in (mk.get("tight") or "") + " " + (mk.get("wide") or "")
                                   for w in _CL_WORDS) for _f, mk in routable):
            cl_to_misc.append(cl_path)
        for finput, mk in routable:
            try:
                tight = mk.get("tight") or ""
                wide = mk.get("wide") or ""
                key = frame_key + "::" + (tight + "|" + wide)[:150]
                if key in uploads_done:
                    continue
                routed = _route(tight)
                if routed is None:
                    routed = _route(wide)
                if routed is None:
                    # Unlabeled upload field: assume the CV slot, but only when
                    # it's the frame's ONLY routable file input — spraying the
                    # CV into every anonymous input is how applications ended
                    # up with piles of duplicate attachments.
                    routed = [cv_path] if (cv_path and len(routable) == 1) else []
                paths = [p for p in routed if p and Path(p).exists()
                         and Path(p).name not in visible_names
                         and _doc_key(frame_key, p) not in uploads_done
                         and _session_upload_count(uploads_done, p) < MAX_UPLOADS_PER_DOC]
                if not paths:
                    uploads_done.add(key)
                    continue
                multiple = await finput.evaluate("el => el.multiple")
                if not multiple:
                    paths = paths[:1]
                already = await finput.evaluate("el => el.files && el.files.length > 0")
                if already:
                    uploads_done.add(key)
                    continue
                await finput.set_input_files(paths if len(paths) > 1 else paths[0])
                uploads_done.add(key)
                for p in paths:
                    uploads_done.add(_doc_key(frame_key, p))
                filled_total += 1
            except Exception:
                continue
    return filled_total


async def run_assist(
    apply_url: str,
    profile: dict,
    cv_path: Optional[str],
    cl_path: Optional[str],
    extra_docs: Optional[list[dict]] = None,
    docs_holder: Optional[dict] = None,
) -> None:
    """Open apply_url in the assist browser and keep auto-filling until the tab
    closes or the lifetime expires. Runs as a fire-and-forget asyncio task.

    `docs_holder`, when given, is a mutable {"cv": path|None, "cl": path|None}
    read fresh on every pass. This lets the browser open and start filling
    profile fields IMMEDIATELY while the tailored CV / cover letter are still
    being generated in the background; the moment they land in `docs_holder`,
    the next fill pass uploads them. (When None, the fixed cv_path/cl_path are
    used — the fast path for offers whose docs already exist.)"""
    js_rules = build_field_values(profile)

    browser = await AssistBrowser.get()
    page = await browser.new_page()
    try:
        await page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        logger.warning("assist: initial navigation failed: %s", e)

    total_filled = 0
    state: dict = {}
    deadline = asyncio.get_event_loop().time() + TAB_LIFETIME_S

    # Follow every tab this session spawns. Some career sites are only a landing
    # page: the REAL ATS form opens in a NEW TAB (Goldman's higher.gs.com "Apply"
    # opens Oracle CandidateExperience with target=_blank). Watching just our own
    # tab meant the engine kept filling the idle landing page while the user sat
    # in front of an untouched form. `state` is shared across the session's tabs
    # on purpose — upload dedupe is keyed on frame URL, so a document still
    # uploads at most once even though several tabs are being swept.
    session_pages: list[Page] = [page]

    def _follow(pg: Page) -> None:
        def _on_popup(new_pg: Page) -> None:
            if new_pg not in session_pages:
                session_pages.append(new_pg)
                _follow(new_pg)          # popups of popups (ATS gateways chain)
                logger.info("assist: following new tab %s", new_pg.url[:120])
        pg.on("popup", _on_popup)

    _follow(page)

    while asyncio.get_event_loop().time() < deadline:
        live = [p for p in session_pages if not p.is_closed()]
        if not live:
            break
        cv_p = docs_holder.get("cv") if docs_holder is not None else cv_path
        cl_p = docs_holder.get("cl") if docs_holder is not None else cl_path
        pending = docs_holder is not None and (docs_holder.get("cv") is None
                                               or docs_holder.get("cl") is None)
        gen_failed = docs_holder is not None and docs_holder.get("failed")
        if gen_failed:
            doc_note = " — DOCUMENT GENERATION FAILED, attach CV/cover letter manually"
        elif pending:
            doc_note = " — CV/cover letter still generating, they'll attach automatically"
        else:
            doc_note = ""
        for pg in live:
            try:
                total_filled += await fill_pass(pg, js_rules, cv_p, cl_p,
                                                extra_docs, state, profile)
                badge = (f"Internship DB: auto-filled {total_filled} field(s)"
                         + doc_note
                         + " — review everything, then submit yourself")
                try:
                    await pg.evaluate(_BADGE_JS, badge)
                except Exception:
                    pass
            except Exception as e:
                if pg.is_closed():
                    continue
                logger.debug("assist pass error: %s", e)
        await asyncio.sleep(FILL_INTERVAL_S)

    logger.info("assist finished for %s: %d fields filled across %d tab(s)",
                apply_url, total_filled, len(session_pages))
