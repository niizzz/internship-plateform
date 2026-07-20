from __future__ import annotations

import html as _htmllib
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from playwright.async_api import Browser, BrowserContext, async_playwright

logger = logging.getLogger(__name__)


EUROPE_COUNTRIES = {
    "United Kingdom", "UK", "England", "Scotland",
    "France", "Germany", "Spain", "Italy", "Netherlands", "Belgium",
    "Luxembourg", "Ireland", "Switzerland", "Portugal", "Sweden", "Denmark",
    "Norway", "Finland", "Poland", "Austria", "Czech Republic", "Czechia",
    "Hungary", "Greece", "Romania",
}

EUROPE_CITIES = {
    # UK
    "london", "edinburgh", "birmingham", "glasgow", "manchester",
    # France
    "paris", "la défense", "la defense", "courbevoie",
    # DACH
    "frankfurt", "berlin", "munich", "münchen", "zurich", "zürich", "geneva", "genève", "basel", "lugano", "vienna", "wien",
    # Iberia
    "madrid", "barcelona", "lisbon", "lisboa",
    # Benelux
    "amsterdam", "the hague", "rotterdam", "brussels", "bruxelles", "luxembourg", "luxembourg city",
    # Nordics
    "stockholm", "copenhagen", "oslo", "helsinki",
    # Kepler Cheuvreux offices / French spellings
    "nyon", "londres", "francfort",
    # CEE / other
    "warsaw", "prague", "dublin", "milan", "milano", "rome", "roma",
}

# Keywords that mark a posting as Sales / Trading / Structuring relevant.
SNT_KEYWORDS = [
    "sales", "trader", "trading", "structuring", "structurer",
    "markets", "market making", "global markets",
    "fixed income", "ficc", "equities", "equity derivatives", "rates", "fx",
    "commodities", "credit trading", "credit sales", "macro",
    "quantitative trading", "quantitative strategist", "qis",
    "securities", "delta one", "etf trading",
]

# Programs we accept (anything that looks like an internship / placement / spring / grad scheme).
PROGRAM_KEYWORDS = [
    "intern", "internship", "summer analyst", "summer associate",
    "industrial placement", "placement", "year in industry", "year-in-industry",
    "off-cycle", "off cycle", "spring", "insight", "discovery",
    "graduate", "analyst program", "analyst programme", "early careers",
    "apprenticeship", "apprenti", "stagiaire", "stage", "vie",
]


@dataclass
class ScrapedOffer:
    """What each scraper returns per posting. Orchestrator handles persistence."""
    bank: str
    external_id: str            # bank-specific id used for dedupe across refreshes
    role_title: str
    location: str               # raw location string from the posting
    apply_url: str              # direct application URL
    source_url: Optional[str] = None  # the listing page we found it on
    description: Optional[str] = None
    start_date_raw: Optional[str] = None
    start_date_parsed: Optional[date] = None
    duration: Optional[str] = None
    program_type: Optional[str] = None
    category: Optional[str] = None
    # Derived / extracted at orchestration time if not set.
    country: Optional[str] = None
    city: Optional[str] = None
    extras: dict = field(default_factory=dict)


class BankScraper:
    """Base class for bank scrapers. Subclasses implement `scrape()`."""

    bank_name: str = "UNKNOWN"
    careers_url: str = ""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    async def __aenter__(self):
        # Playwright is started lazily on first new_page() call so HTTP-only
        # scrapers don't pay the subprocess cost.
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _ensure_browser(self):
        if self._context is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )

    async def new_page(self):
        await self._ensure_browser()
        assert self._context is not None
        return await self._context.new_page()

    async def scrape(self) -> list[ScrapedOffer]:
        raise NotImplementedError


# --- Filtering helpers shared by all scrapers / orchestrator ----------------

# Non-front-office function terms. When one of these appears in the TITLE and
# there is NO strong S&T signal in the title, the role is dropped — even if the
# description mentions Global Markets / trading as a sibling division (bank
# descriptions list every division as boilerplate). This is the gate that keeps
# middle-/back-office and control roles out of an S&T-only feed.
_BANKING_ONLY_TITLE = re.compile(
    r"\b(investment banking|m&a|leveraged finance|corporate banking|"
    r"wealth management|private bank(?:er|ing)?|client advisor|relationship manager|"
    r"asset management|commercial real estate|loan|lending|"
    r"corporate finance|m&a advisory|operations analyst|trade lifecycle|"
    r"trade support|middle office|back office|kyc|aml|compliance officer|"
    r"anti[-\s]?financial crime|\bafc\b|financial crime|fraud|sanctions|"
    # Risk functions (not front-office S&T)
    r"market risk|credit risk|counterparty risk|operational risk|liquidity risk|"
    r"model risk|risk management|prudential|"
    # Control / finance / ops functions
    r"product control|valuation control|financial control|business control|"
    r"operations|settlement(?:s)?|reconciliation|post[\s-]?trad(?:e|ing)|"
    r"custody|fund servicing|fund administration|reference data|securities services|"
    r"regulatory|governance|onboarding|"
    # Corporate / commercial bank & adjacent (not front-office S&T)
    r"corporate coverage|corporate bank|commercial bank|"
    r"\bcoverage\b|relationship (?:&|and) transaction|transaction management|"
    r"transaction banking|cash management|trade finance|"
    r"structured finance|structured lending|working capital|"
    r"private equity|private capital|client lifecycle|\bdws\b|"
    # Support functions
    r"audit|tax|hr|human resources|marketing|communications|"
    r"technology|software engineer|developer|cybersecurity|data engineer|devops|"
    r"esg|sustainability|csr)\b"
)

# Strong S&T signals — if ANY of these appear in title or description, it's S&T,
# even if the posting also mentions banking divisions. Includes product names,
# desks, and umbrella division names banks use instead of "Trading".
_STRONG_SNT = re.compile(
    r"\b("
    # Division / umbrella names
    r"global markets|securities division|markets division|"
    r"sales\s*(?:&|and)\s*trading|s\s*&\s*t|"
    # Desks / asset classes
    r"ficc|\bfic\b|fixed income(?:\s+sales|\s+trading|\s+division)?|"
    r"equity derivatives|equities trading|equities sales|equities division|"
    r"interest rate(?:s)?\s+(?:trad|sales|product|deriv)|"
    r"\brates\s+(?:trad(?:er|ing)|sales|desk|product|structuring|strategist)|"
    r"\bfx\b|forex|foreign exchange|fx trading|fx sales|fx options|"
    r"commodit(?:y|ies)\s+(?:trad|sales|desk)|"
    r"credit(?:\s+sales|\s+trading|\s+desk|\s+flow|\s+structuring)|"
    r"emerging markets(?:\s+trading|\s+sales|\s+desk)?|"
    r"macro\s+(?:trad\w*|sales|strateg\w*|research|desk)|"
    r"securitized products|securitised products|asset[-\s]backed|"
    # Trading-style products
    r"delta one|prime services|prime brokerage|"
    r"electronic trading|algorithmic trading|algo trading|low\s*latency|"
    r"market[-\s]?making|market maker|"
    r"qis|quantitative investment strateg|systematic trading|systematic strateg|"
    r"flow\s+(?:trad(?:er|ing)|sales|product)|"
    r"sales[\s-]*trader|"
    r"cross[-\s]asset(?:\s+solutions|\s+sales|\s+structuring|\s+trading)?|"
    r"structured\s+(?:products?|solutions?|notes?|deriv)|"
    r"exotic(?:s)?(?:\s+deriv|\s+trad|\s+desk)?|"
    r"vanilla(?:s)?\s+(?:trad|desk)|"
    # Title-style phrases. NB: "structured" alone is intentionally excluded —
    # it collides with "structured lending/finance" (corporate banking). The
    # S&T sense is covered by "structuring"/"structurer" and the specific
    # "structured products/notes/solutions/derivatives" above.
    # "trading" alone is strong — except "post trading" (settlement/ops).
    r"(?<!post\s)(?<!post-)trad(?:er|ing)|structur(?:ing|er)|"
    r"quant(?:itative)?\s+(?:trader|trading|strategist|analyst|analysis|analytics|research|structuring)|"
    # French desk terms (SocGen / Natixis / CACIB / Rothschild / BNP postings)
    r"n[ée]gociateur|structureur|ing[ée]nieur\s+financier|"
    r"produits?\s+structur[ée]s?|structuration|"
    r"salle des march[ée]s|d[ée]riv[ée]s\s+actions|"
    r"vente[s]?\s+(?:flux|d[ée]riv\w*|actions|taux|change|cr[ée]dit|march)"
    r")\b"
)

# Unambiguous desk / division phrases. Only these count when the S&T signal
# comes from the DESCRIPTION (not the title) — they don't appear in
# corporate-coverage / lending / ops boilerplate, unlike bare "markets".
_VERY_STRONG_SNT = re.compile(
    r"\b("
    r"global markets|securities division|markets division|"
    r"sales\s*(?:&|and)\s*trading|\bs\s*&\s*t\b|"
    r"ficc|\bfic\b|"
    r"fixed income\s+(?:sales|trading|desk)|"
    r"equity derivatives|equities\s+(?:trading|sales)|"
    r"\brates\s+(?:trading|sales|desk|structuring)|"
    r"fx\s+(?:trading|sales|options)|foreign exchange\s+(?:trading|sales)|"
    r"credit\s+(?:trading|sales|flow)|"
    r"commodit(?:y|ies)\s+(?:trading|sales)|"
    r"macro\s+(?:trading|sales|desk)|"
    r"delta one|prime (?:brokerage|services)|"
    r"electronic trading|algorithmic trading|algo trading|market[-\s]?making|"
    r"structured\s+(?:products?|notes?|solutions?|derivatives)|"
    r"flow\s+(?:trading|sales)|sales[\s-]*trader|securities lending|"
    # French desk terms
    r"n[ée]gociateur|structureur|produits?\s+structur[ée]s?|"
    r"salle des march[ée]s|d[ée]riv[ée]s\s+actions"
    r")\b"
)

# Sales-adjacent terms that need a markets context anchor to count.
_SALES_TERMS = re.compile(r"\b(sales|distribution|client coverage|institutional sales)\b")
_MARKETS_ANCHOR = re.compile(
    r"\b(markets|securities|trading|derivatives|ficc|fixed income|equities|"
    r"rates|fx|forex|commodities|credit|macro|hedge fund|institutional client)\b"
)


def _categorize(text: str) -> str:
    """Pick a sub-category (sales/trading/structuring/markets) from text."""
    if re.search(r"\bsales[\s-]*trader\b", text):
        return "sales"
    if re.search(r"\b(structur(ing|er)|structureur|structuration|ing[ée]nieur\s+financier|produits?\s+structur[ée]s?)\b", text):
        return "structuring"
    if re.search(r"\bn[ée]gociateur\b", text):
        return "trading"
    if re.search(r"\bvente[s]?\b", text) and not re.search(r"\btrad", text):
        return "sales"
    if re.search(r"\btrad(er|ing)\b", text):
        return "trading"
    if re.search(r"\b(sales|distribution)\b", text):
        return "sales"
    return "markets"


def is_snt_role(title: str, description: str = "") -> Optional[str]:
    """Return the S&T sub-category if the role is Sales/Trading/Structuring, else None.

    Title-driven by design:
      1. If the title names a non-front-office function (corporate coverage, risk,
         ops, IB, PB, …) and carries no S&T marker, drop it — a description that
         lists S&T as a sibling division must not rescue it.
      2. If the title carries a strong S&T marker, classify from the title.
      3. Otherwise the title is generic (e.g. "Summer Analyst", "Intern – Team X").
         Only then consult the description, and only accept an *unambiguous* desk /
         division phrase (`_VERY_STRONG_SNT`) — bare "markets"/"trading" boilerplate
         does not qualify. This is what keeps coverage/lending interns out.
    """
    title_l = title.lower()

    if _BANKING_ONLY_TITLE.search(title_l) and not _STRONG_SNT.search(title_l):
        return None

    # Title-based detection (full strong set).
    if _STRONG_SNT.search(title_l):
        return _categorize(title_l)
    if _SALES_TERMS.search(title_l) and _MARKETS_ANCHOR.search(title_l):
        return "sales"

    # Description fallback — ONLY when the title is a bare program label with no
    # domain noun of its own (e.g. "Summer Analyst", "Stage 6 mois", "Trainee").
    # If the title already carries a domain word ("Economie", "Juriste", "Risk",
    # "Coverage"), trust the title and don't let desc boilerplate override it —
    # that boilerplate names every division and was the main false-positive source.
    if _title_is_generic(title_l):
        desc_l = (description or "").lower()
        if desc_l and _VERY_STRONG_SNT.search(desc_l):
            return _categorize(desc_l)
    return None


# Filler words stripped when testing whether a title is "just a program label".
_TITLE_FILLER_RE = re.compile(
    r"\b(assistant|junior|senior|programme|program|team|desk|group|division|"
    r"department|dept|office|the|and|of|for|"
    r"de|du|des|en|la|le|les|au|aux|et|un|une|h\s*/\s*f|f\s*/\s*h|m/f/d|w/m/d|f/m|"
    r"mois|month|months|an|ans|year|years|"
    r"janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|octobre|novembre|decembre|décembre|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.I,
)


def _title_is_generic(title_l: str) -> bool:
    """True if, after removing program words + fillers + punctuation/digits, the
    title has no meaningful domain noun left (so the description may decide).
    Threshold is 2: a 3-letter leftover is usually a domain acronym (AFC, KYC)
    that must NOT be description-rescued."""
    t = _PROGRAM_RE.sub(" ", title_l)
    t = _TITLE_FILLER_RE.sub(" ", t)
    t = re.sub(r"[^a-zàâäéèêëîïôöûùüç]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return len(t) <= 2


_PROGRAM_RE = re.compile(
    r"\b("
    r"intern(?:s|ship|ships)?|"
    r"summer\s+(?:analyst|associate)|"
    r"industrial\s+placement|placement|year\s+in\s+industry|year-in-industry|"
    r"off[\s-]?cycle|"
    r"spring\s+(?:week|insight|programme|program)|insight\s+(?:day|week|programme|program)|discovery\s+(?:day|week)|"
    r"graduate(?:\s+(?:programme|program|scheme|analyst))?|"
    r"analyst\s+(?:programme|program|scheme)|"
    r"apprentice(?:ship)?|apprenti|trainee|"
    r"stagiaire|stage|vie\b|alternance|alternant(?:e)?|"
    # German early-careers terms (Deutsche Bank, German postings)
    r"praktikum|praktikant(?:in)?|werkstudent(?:in)?|"
    r"early\s+careers?|emerging\s+talent|"
    r"work\s+(?:placement|experience)"
    r")\b"
)


def is_internship_or_grad(title: str, description: str = "") -> bool:
    """Whether a role is an internship / placement / graduate programme.

    Determined from the TITLE only. Banks always label early-careers roles in
    the title (candidates filter on it); scanning the description instead pulls
    in full-time VP/Analyst roles whose boilerplate merely mentions "graduate
    programmes", "work experience" or "we offer internships". The `description`
    argument is accepted for call-site compatibility but intentionally unused.
    """
    return bool(_PROGRAM_RE.search(title.lower()))


def parse_location(raw: str) -> tuple[Optional[str], Optional[str]]:
    """Return (city, country) best-effort from a free-form location string."""
    if not raw:
        return None, None
    text = raw.lower()
    city = None
    for c in EUROPE_CITIES:
        if c in text:
            city = c.title()
            break
    country = None
    for c in EUROPE_COUNTRIES:
        if c.lower() in text:
            country = c
            break
    # Heuristic country-from-city if country missing
    if country is None and city:
        city_country = {
            "London": "United Kingdom", "Edinburgh": "United Kingdom",
            "Paris": "France", "La Défense": "France", "La Defense": "France", "Courbevoie": "France",
            "Frankfurt": "Germany", "Berlin": "Germany", "Munich": "Germany", "München": "Germany",
            "Zurich": "Switzerland", "Zürich": "Switzerland", "Geneva": "Switzerland", "Basel": "Switzerland",
            "Madrid": "Spain", "Barcelona": "Spain",
            "Milan": "Italy", "Milano": "Italy", "Rome": "Italy",
            "Amsterdam": "Netherlands", "Rotterdam": "Netherlands",
            "Brussels": "Belgium", "Bruxelles": "Belgium",
            "Luxembourg": "Luxembourg",
            "Dublin": "Ireland",
            "Lisbon": "Portugal", "Lisboa": "Portugal",
            "Vienna": "Austria", "Wien": "Austria",
            "Stockholm": "Sweden", "Copenhagen": "Denmark", "Oslo": "Norway", "Helsinki": "Finland",
            "Warsaw": "Poland", "Prague": "Czech Republic",
            "Nyon": "Switzerland", "Londres": "United Kingdom", "Francfort": "Germany",
        }
        country = city_country.get(city)
    return city, country


def in_europe(location: str) -> bool:
    """Loose check: location string looks European."""
    if not location:
        return False
    city, country = parse_location(location)
    return city is not None or country is not None


# --- Description formatting --------------------------------------------------

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
_BLOCK_CLOSE_RE = re.compile(r"</(p|div|section|article|tr|table|ul|ol|blockquote)\s*>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_LI_OPEN_RE = re.compile(r"<li\b[^>]*>", re.I)
_H_OPEN_RE = re.compile(r"<h[1-6]\b[^>]*>", re.I)
_H_CLOSE_RE = re.compile(r"</h[1-6]\s*>", re.I)
_BOLD_TAG_RE = re.compile(r"</?(?:strong|b)\b[^>]*>", re.I)
_EMPTY_BOLD_RE = re.compile(r"\*\*(\s*)\*\*")
_HEADING_LINE_RE = re.compile(r"^##\s*")


def html_to_text(raw: str, limit: int = 7000) -> str:
    """Convert posting HTML to readable "markdown-lite", PRESERVING structure.

    Block-level closes and <br> become newlines, <li> becomes a "• " bullet
    line, <h1-6> become "## " heading lines and <strong>/<b> become **bold**.
    The frontend's OfferDescription component renders these markers as real
    headings/lists/bold; older rows without markers still render via its
    heuristics.
    """
    if not raw:
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", raw)
    text = _LI_OPEN_RE.sub("\n• ", text)
    text = _BR_RE.sub("\n", text)
    text = _H_OPEN_RE.sub("\n## ", text)
    text = _H_CLOSE_RE.sub("\n", text)
    text = _BOLD_TAG_RE.sub("**", text)
    text = _BLOCK_CLOSE_RE.sub("\n", text)
    text = _ANY_TAG_RE.sub(" ", text)
    text = _htmllib.unescape(text)
    # Drop empty bold pairs left by icon/spacer <strong> tags (keep the gap).
    text = _EMPTY_BOLD_RE.sub(r"\1", text)
    # Normalise whitespace but keep line structure.
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.split("\n")]
    # Re-open/close bold on every line so **spans** never straddle newlines —
    # banks bold whole multi-paragraph blocks, but the renderer works per line.
    bold_open = False
    balanced: list[str] = []
    for ln in lines:
        if ln:
            if bold_open:
                ln = "**" + ln
            bold_open = ln.count("**") % 2 == 1
            if bold_open:
                ln += "**"
            ln = _EMPTY_BOLD_RE.sub(r"\1", ln).strip()
        balanced.append(ln)
    out: list[str] = []
    blank = 0
    for ln in balanced:
        if ln and _HEADING_LINE_RE.sub("", ln).strip("*•· ") == "":
            ln = ""  # heading/bullet/bold marker with no text behind it
        if not ln:
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(ln)
    result = "\n".join(out).strip()
    return result[:limit]


# --- Start date / duration extraction ----------------------------------------

_MONTHS = {
    # English
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    # French
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12, "decembre": 12,
    # German
    "januar": 1, "februar": 2, "märz": 3, "marz": 3, "juni": 6, "juli": 7,
    "oktober": 10, "dezember": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

# "start(ing) ...", "commencing ...", "à pourvoir à partir de ...", "ab ..." etc.
# followed within a few words by "<month> <year>" (optionally with a day).
_ANCHORED_START_RE = re.compile(
    r"(?:start(?:ing|s)?(?:\s+date)?|commenc\w+|begin(?:ning|s)?|join(?:ing)?\s+(?:us|in)|onboard\w*|"
    r"d[ée]marrage|d[ée]but(?:ant)?|prise de poste|[àa] pourvoir|[àa] partir d[eu]|d[èe]s(?!\s+que)|"
    r"ab(?:\s+dem)?|beginn(?:end)?|starttermin)"
    r"\W{0,3}(?:\w+\W{1,3}){0,4}?"
    r"(?:(\d{1,2})(?:st|nd|rd|th)?\s+)?(" + _MONTH_ALT + r")\.?\s*(?:(\d{1,2})(?:st|nd|rd|th)?,?\s*)?(20\d\d)\b",
    re.I,
)
# "July 2026 start" / "start July 2026" already covered; this catches trailing "start".
_TRAILING_START_RE = re.compile(
    r"\b(" + _MONTH_ALT + r")\.?\s+(20\d\d)\s+(?:start|intake|entry|cohort)\b", re.I)
# Seasons: "Summer 2027", "2027 Summer Analyst", "été 2026",
# "Summer Analyst Program 2027" (program words between season and year).
_SEASON_RE = re.compile(
    r"\b(summer|spring|autumn|fall|winter|[ée]t[ée]|printemps)"
    r"(?:\s+(?:analyst|associate|intern(?:ship)?|week|programme|program))*\s+(20\d\d)\b|"
    r"\b(20\d\d)\s+(summer|spring|off[\s-]?cycle|winter)\b", re.I)
_SEASON_MONTH = {"summer": 6, "été": 6, "ete": 6, "spring": 4, "printemps": 4,
                 "autumn": 9, "fall": 9, "winter": 1, "off-cycle": 1, "off cycle": 1, "offcycle": 1}
# Bare "<month> 2026" — safe in TITLES only (French postings put the start month
# in the title); in descriptions it matches deadlines/posting dates.
_BARE_MONTH_RE = re.compile(r"\b(" + _MONTH_ALT + r")\.?\s+(20\d\d)\b", re.I)
_ASAP_RE = re.compile(
    r"\b(asap|as soon as possible|d[èe]s que possible|immediate start|"
    r"start(?:ing)?\s+immediately|ab sofort)\b", re.I)
_ISO_DATE_RE = re.compile(r"\b(20\d\d)[-/](\d{2})[-/](\d{2})\b")

_MONTH_DISPLAY = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"]


def _mk_date(year: int, month: int, day: int = 1) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _match_start(text: str, allow_bare: bool) -> tuple[Optional[str], Optional[date]]:
    if not text:
        return None, None
    m = _ANCHORED_START_RE.search(text)
    if m:
        day = int(m.group(1) or m.group(3) or 1)
        month = _MONTHS[m.group(2).lower()]
        year = int(m.group(4))
        disp = f"{_MONTH_DISPLAY[month - 1]} {year}"
        if m.group(1) or m.group(3):
            disp = f"{day} {disp}"
        return disp, _mk_date(year, month, min(day, 28))
    m = _TRAILING_START_RE.search(text)
    if m:
        month, year = _MONTHS[m.group(1).lower()], int(m.group(2))
        return f"{_MONTH_DISPLAY[month - 1]} {year}", _mk_date(year, month)
    m = _SEASON_RE.search(text)
    if m:
        season = (m.group(1) or m.group(4) or "").lower()
        year = int(m.group(2) or m.group(3))
        month = _SEASON_MONTH.get(season.replace("-", " ").strip(), 6)
        label = "Off-cycle" if "cycle" in season else season.capitalize()
        if label in ("Été", "Ete"):
            label = "Summer"
        if label == "Printemps":
            label = "Spring"
        return f"{label} {year}", _mk_date(year, month)
    if allow_bare:
        m = _BARE_MONTH_RE.search(text)
        if m:
            month, year = _MONTHS[m.group(1).lower()], int(m.group(2))
            return f"{_MONTH_DISPLAY[month - 1]} {year}", _mk_date(year, month)
    # "dès que possible" / ASAP is unambiguous wherever it appears.
    if _ASAP_RE.search(text):
        return "ASAP", None
    return None, None


def extract_start_date(title: str, description: str = "") -> tuple[Optional[str], Optional[date]]:
    """Best-effort start date from a posting: ("July 2026", date(2026,7,1)) or (None, None).

    The title is trusted more than the description: bare "<month> <year>" counts
    there, whereas in the description only anchored phrases ("starting in …",
    "à pourvoir à partir de …") and seasons are accepted.
    """
    disp, parsed = _match_start(title or "", allow_bare=True)
    if disp:
        return disp, parsed
    return _match_start(description or "", allow_bare=False)


def normalize_start_date(raw: str) -> tuple[Optional[str], Optional[date]]:
    """Normalize a scraper-provided raw value (ISO date or free text)."""
    if not raw:
        return None, None
    m = _ISO_DATE_RE.search(raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        parsed = _mk_date(y, mo, d)
        if parsed:
            return f"{d} {_MONTH_DISPLAY[mo - 1]} {y}", parsed
    return _match_start(raw, allow_bare=True)


_DURATION_RE = re.compile(
    r"\b(\d{1,2})\s*(?:-|–|\s)?\s*(month|months|mois|monate|week|weeks|semaines?|wochen)\b", re.I)
_DURATION_WORDS = {"month": "months", "months": "months", "mois": "months", "monate": "months",
                   "week": "weeks", "weeks": "weeks", "semaine": "weeks", "semaines": "weeks",
                   "wochen": "weeks"}


def extract_duration(title: str, description: str = "") -> Optional[str]:
    """Best-effort duration ("6 months", "10 weeks") from title, then description."""
    for text in (title, description):
        if not text:
            continue
        m = _DURATION_RE.search(text)
        if m:
            n = int(m.group(1))
            unit = _DURATION_WORDS.get(m.group(2).lower(), "months")
            if (unit == "months" and 1 <= n <= 24) or (unit == "weeks" and 1 <= n <= 52):
                return f"{n} {unit}"
    return None
