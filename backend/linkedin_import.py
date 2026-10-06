"""Find the people worth messaging inside the user's own LinkedIn connections.

HOW THE DATA GETS HERE, AND WHY IT IS NOT SCRAPED
-------------------------------------------------
LinkedIn's User Agreement forbids automated access, and enumerating a connection
list is exactly the behaviour its anti-bot systems are tuned to catch. So this
module reads the export LinkedIn itself provides:

    LinkedIn -> Settings & Privacy -> Data privacy -> Get a copy of your data
    -> "Connections" -> download -> Connections.csv

That file is the user's own data, handed over by LinkedIn on request. It carries
Company and Position for every connection, which is everything needed to rank
who is worth writing to. It is also strictly better than scraping would be: the
whole network at once, no pagination, no rate limit, no ban risk, and it
includes the email addresses connections chose to share.

WHAT THIS MODULE DOES
---------------------
Parses that CSV, matches each employer against the banks the platform already
tracks, classifies each job title with the SAME tested S&T vocabulary the offer
filter uses (scrapers/base.py), and scores the result so the strongest people to
approach float to the top. Nothing is contacted; ranking only.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from typing import Iterable, Optional

from email_patterns import guess_email
from scrapers.base import (
    _HARD_NON_FO, _MARKETS_ANCHOR, _MARKETS_DIVISION, _SALES_TERMS,
    _STRONG_SNT, _categorize,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------- employers

# The 28 banks the platform scrapes. A connection here is worth the most: their
# firm has live postings in the DB, so outreach can point at a real role.
TARGET_BANKS = {
    "Goldman Sachs": ["goldman sachs", "goldman"],
    "JPMorgan": ["jpmorgan", "jp morgan", "j.p. morgan", "jpmc", "chase"],
    "Morgan Stanley": ["morgan stanley"],
    "Citi": ["citi", "citigroup", "citibank"],
    "Bank of America": ["bank of america", "bofa", "merrill lynch", "baml"],
    "UBS": ["ubs", "credit suisse"],  # CS is inside UBS since 2023
    "HSBC": ["hsbc"],
    "BNP Paribas": ["bnp paribas", "bnpp", "bnp"],
    "Deutsche Bank": ["deutsche bank", "deutsche"],
    "Barclays": ["barclays"],
    "Société Générale": ["societe generale", "société générale", "socgen", "sg cib"],
    "Santander": ["santander"],
    "Crédit Agricole CIB": ["credit agricole", "crédit agricole", "ca-cib", "cacib"],
    "Natixis": ["natixis"],
    "Lazard": ["lazard"],
    "Rothschild & Co": ["rothschild", "redburn"],
    "Commerzbank": ["commerzbank"],
    "Kepler Cheuvreux": ["kepler cheuvreux", "kepler"],
    "Nomura": ["nomura"],
    "BBVA": ["bbva"],
    "Euronext": ["euronext"],
    "RBC": ["rbc", "royal bank of canada", "rbc capital markets"],
    "CMC Markets": ["cmc markets"],
    "Amundi": ["amundi"],
    "Maven Securities": ["maven securities", "maven"],
    "UniCredit": ["unicredit"],
    "Mizuho": ["mizuho"],
    "Lazard Frères Gestion": ["lazard freres gestion", "lazard frères gestion"],
}

# Investment banks and brokers the platform does not scrape but where an S&T
# contact is still worth having.
OTHER_IB = {
    "Jefferies": ["jefferies"], "Macquarie": ["macquarie"],
    "Standard Chartered": ["standard chartered", "stanchart"],
    "ING": ["ing bank", "ing group", "ing wholesale"],
    "ABN AMRO": ["abn amro"], "Rabobank": ["rabobank"],
    "Nordea": ["nordea"], "SEB": ["skandinaviska", "seb group"],
    "Danske Bank": ["danske"], "Handelsbanken": ["handelsbanken"],
    "Mediobanca": ["mediobanca"], "Intesa Sanpaolo": ["intesa sanpaolo", "intesa"],
    "Julius Baer": ["julius baer", "julius bär"], "Pictet": ["pictet"],
    "Lombard Odier": ["lombard odier"], "Vontobel": ["vontobel"],
    "Berenberg": ["berenberg"], "ODDO BHF": ["oddo"],
    "Cantor Fitzgerald": ["cantor fitzgerald"], "StoneX": ["stonex"],
    "Marex": ["marex"], "TP ICAP": ["tp icap", "tullett prebon"],
    "BGC Partners": ["bgc partners"], "Tradition": ["compagnie financiere tradition", "tradition"],
    "Scotiabank": ["scotiabank"], "BMO": ["bmo capital"],
    "TD Securities": ["td securities"], "SMBC": ["smbc", "sumitomo mitsui"],
    "MUFG": ["mufg", "mitsubishi ufj"], "Wells Fargo": ["wells fargo"],
}

# Market makers and systematic funds. Adjacent rather than core: they trade but
# mostly do not run a client sales desk, so they rank below a bank.
MARKET_MAKERS = {
    "Optiver": ["optiver"], "IMC": ["imc trading", "imc financial"],
    "Flow Traders": ["flow traders"], "Jane Street": ["jane street"],
    "SIG": ["susquehanna"], "DRW": ["drw"], "Jump Trading": ["jump trading"],
    "Citadel Securities": ["citadel securities"], "Citadel": ["citadel"],
    "XTX Markets": ["xtx"], "Qube RT": ["qube research"],
    "Squarepoint": ["squarepoint"], "Millennium": ["millennium management"],
    "Balyasny": ["balyasny"], "Point72": ["point72"],
    "Marshall Wace": ["marshall wace"], "Man Group": ["man group"],
    "Brevan Howard": ["brevan howard"], "Capula": ["capula"],
    "Tower Research": ["tower research"], "Five Rings": ["five rings"],
}

TIER_TARGET, TIER_IB, TIER_MM = "target_bank", "other_ib", "market_maker"

# Longest alias first so "credit suisse" is not shadowed by a shorter "credit",
# and "citadel securities" resolves before "citadel".
_EMPLOYER_INDEX: list[tuple[str, str, str]] = sorted(
    [(alias, name, tier)
     for table, tier in ((TARGET_BANKS, TIER_TARGET), (OTHER_IB, TIER_IB), (MARKET_MAKERS, TIER_MM))
     for name, aliases in table.items()
     for alias in aliases],
    key=lambda t: -len(t[0]),
)


# Aliases that are also ordinary words or common surnames. A word-boundary match
# on these alone is not enough: "Deutsche Telekom" and "Deutsche Post" are large
# employers and neither is Deutsche Bank, and "Citadel Bakery" is a bakery.
_AMBIGUOUS = {
    "chase", "citadel", "maven", "kepler", "deutsche", "goldman", "marex",
    "intesa", "man group", "tradition", "nordea", "danske",
}

# Words that may sit alongside an ambiguous alias without changing who it is:
# legal suffixes, geography, and finance nouns. Anything else ("telekom",
# "bakery", "plumbing") means it is a different company.
_NEUTRAL_TAIL = {
    "ag", "sa", "s.a.", "nv", "n.v.", "plc", "ltd", "limited", "llc", "llp", "inc",
    # European legal forms: without these, "Nordea Bank Abp" reads as a different
    # company than Nordea.
    "ab", "abp", "oyj", "oy", "asa", "as", "aps", "a/s", "spa", "s.p.a.", "srl",
    "gmbh", "kgaa", "se", "bv", "b.v.", "sas", "sarl", "sca", "scs", "kk",
    "pte", "pty", "ltda", "cv", "ec",
    "co", "corp", "group", "holding", "holdings", "international", "intl", "and",
    "&", "the", "de", "du", "des", "et",
    "bank", "banking", "bankers", "banque", "securities", "capital", "markets",
    "market", "asset", "assets", "management", "investment", "investments",
    "investing", "financial", "finance", "partners", "advisors", "advisers",
    "trading", "traders", "brokerage", "broker", "cib", "ib", "wealth",
    "europe", "emea", "uk", "usa", "us", "france", "germany", "deutschland",
    "london", "paris", "frankfurt", "milan", "madrid", "zurich", "geneva",
    "amsterdam", "dublin", "luxembourg", "singapore", "new", "york", "branch",
}

_TOKEN_RE = re.compile(r"[a-z0-9&.'/]+")


def _tail_is_neutral(company_l: str, alias: str) -> bool:
    """True when what surrounds an ambiguous alias does not name a different firm."""
    rest = re.sub(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", " ", company_l)
    return all(t in _NEUTRAL_TAIL for t in _TOKEN_RE.findall(rest))


def match_employer(company: str) -> tuple[Optional[str], Optional[str]]:
    """Map a free-text employer to (canonical name, tier), or (None, None).

    Matching is on word boundaries, so "INGenico" is not ING and "SEBastian Ltd"
    is not SEB. Aliases that are also ordinary words additionally require the
    rest of the name to be neutral (see _AMBIGUOUS).
    """
    c = (company or "").lower().strip()
    if not c:
        return None, None
    for alias, name, tier in _EMPLOYER_INDEX:
        if not re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", c):
            continue
        if alias in _AMBIGUOUS and not _tail_is_neutral(c, alias):
            continue
        return name, tier
    return None, None


# --------------------------------------------------------------- seniority

# Ordered: the first pattern that matches wins, so intern beats analyst
# ("Summer Analyst Intern") and head beats associate ("Associate Head").
_SENIORITY = [
    ("recruiter", re.compile(
        r"\b(recruit\w*|talent acquisition|campus|early careers?|hr business partner|"
        r"human resources|graduate programme manager)\b")),
    ("intern", re.compile(
        r"\b(intern|internship|stagiaire|stage|becario|pr[áa]cticas|est[áa]gi\w*|"
        r"praktikant|summer analyst|off[-\s]?cycle|placement student|working student|"
        r"apprenti\w*|graduate trainee|spring (?:week|insight))\b")),
    ("senior", re.compile(
        r"\b(managing director|\bmd\b|executive director|\bed\b|vice president|\bvp\b|"
        r"director|head of|global head|co[-\s]head|partner|chief|managing partner)\b")),
    ("junior", re.compile(
        r"\b(analyst|associate|\bvp\b|trader|salesperson|sales manager|structurer|"
        r"strategist|graduate|specialist|manager)\b")),
]


def detect_seniority(position: str) -> str:
    """Best-guess ContactType from a job title. Defaults to junior, which is both
    the most common connection and the safest drafting angle."""
    p = (position or "").lower()
    for label, rx in _SENIORITY:
        if rx.search(p):
            return label
    return "junior"


# --------------------------------------------------------------- role match

def classify_role(position: str) -> tuple[Optional[str], list[str]]:
    """Return (category, reasons) for a job title, or (None, reasons) if the
    title is not a front-office markets role.

    Reuses the offer filter's vocabulary so a title is judged by exactly the same
    standard as a posting: the 79-case test suite behind it is the reason this is
    worth reusing rather than reinventing.
    """
    p = (position or "").lower().strip()
    if not p:
        return None, ["no job title in the export"]
    reasons: list[str] = []

    # A control/ops/tech function that merely supports a markets division is not
    # someone who can talk about the desk or refer into it.
    non_fo = _HARD_NON_FO.search(p)
    if non_fo:
        return None, [f"non-front-office function ({non_fo.group(0)})"]

    hit = _STRONG_SNT.search(p)
    if hit:
        reasons.append(f"markets term in title ({hit.group(0)})")
    elif _MARKETS_DIVISION.search(p):
        reasons.append("sits in a Markets division")
    elif _SALES_TERMS.search(p) and _MARKETS_ANCHOR.search(p):
        reasons.append("sales role with a markets anchor")
    else:
        return None, ["title shows no markets/S&T signal"]

    return _categorize(p), reasons


# --------------------------------------------------------------- scoring

# The candidate is targeting SALES on a structured equity derivatives desk, so a
# salesperson outranks a trader even though both sit on the floor.
_CATEGORY_POINTS = {"sales": 45, "structuring": 38, "trading": 30, "markets": 26}
_TIER_POINTS = {TIER_TARGET: 30, TIER_IB: 22, TIER_MM: 16}
# Reply rate, not importance: juniors and interns answer, MDs mostly do not.
_SENIORITY_POINTS = {"junior": 20, "intern": 18, "recruiter": 12, "senior": 8}


def score_row(category: str, tier: str, seniority: str, position: str) -> tuple[int, list[str]]:
    """0-100 ranking. Purely a sort order for the user's attention, never a gate."""
    pts = _CATEGORY_POINTS.get(category, 20) + _TIER_POINTS.get(tier, 10) \
        + _SENIORITY_POINTS.get(seniority, 10)
    why: list[str] = []
    if category == "sales":
        why.append("sales seat, the desk you are targeting")
    if tier == TIER_TARGET:
        why.append("firm you already track offers for")
    if seniority in ("junior", "intern"):
        why.append("junior enough to actually reply")

    p = (position or "").lower()
    # A direct hit on the candidate's own product area is the strongest signal
    # available: the conversation starts with shared ground.
    if re.search(r"\b(structured (products?|solutions?|notes?)|equity derivatives|"
                 r"cross[-\s]asset|delta one|exotic)", p):
        pts += 5
        why.append("same product area as your experience")
    return min(pts, 100), why


# --------------------------------------------------------------- csv parsing

_EXPECTED = ("first name", "last name", "url", "company", "position")


def parse_connections_csv(raw: bytes | str) -> list[dict]:
    """Parse LinkedIn's Connections.csv into row dicts.

    The export opens with a "Notes:" preamble of a few lines before the real
    header, and the preamble's length has changed between LinkedIn versions, so
    the header is located by content rather than by skipping a fixed count.
    """
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
    lines = text.splitlines()

    start = None
    for i, line in enumerate(lines[:25]):
        low = line.lower()
        if low.startswith("first name") and "company" in low.replace('"', ''):
            start = i
            break
    if start is None:
        raise ValueError(
            "This does not look like LinkedIn's Connections.csv: no header row with "
            "'First Name' and 'Company' in the first 25 lines. Export it from "
            "LinkedIn > Settings & Privacy > Data privacy > Get a copy of your data "
            "> Connections."
        )

    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    norm = {(k or "").strip().lower(): (k or "") for k in (reader.fieldnames or [])}
    missing = [c for c in _EXPECTED if c not in norm]
    if missing:
        raise ValueError(f"Connections.csv is missing expected column(s): {', '.join(missing)}")

    out: list[dict] = []
    for row in reader:
        get = lambda key: (row.get(norm.get(key, ""), "") or "").strip()  # noqa: E731
        first, last = get("first name"), get("last name")
        name = " ".join(p for p in (first, last) if p)
        if not name:
            continue
        out.append({
            "full_name": name,
            "linkedin_url": get("url") or None,
            "email": get("email address") or None,
            "company": get("company"),
            "position": get("position"),
            "connected_on": get("connected on") or None,
        })
    return out


def analyse(rows: Iterable[dict]) -> tuple[list[dict], dict]:
    """Classify parsed rows. Returns (matches, stats).

    Only rows at a recognised finance employer AND in a front-office markets role
    are returned. Everything else is counted, never stored: the user's network is
    mostly people irrelevant to this hunt, and keeping third-party contact details
    that serve no purpose is not worth the privacy cost.
    """
    matches: list[dict] = []
    stats = {"total": 0, "no_employer_match": 0, "not_markets_role": 0,
             "matched": 0, "with_shared_email": 0, "with_guessed_email": 0}

    for r in rows:
        stats["total"] += 1
        bank, tier = match_employer(r.get("company", ""))
        if not bank:
            stats["no_employer_match"] += 1
            continue
        position = r.get("position", "")
        category, reasons = classify_role(position)
        seniority = detect_seniority(position)
        if not category:
            # A campus recruiter has no markets term in their title and would be
            # dropped by the role filter, but they are exactly who answers a
            # question about whether a programme is open. Keep them, ranked
            # below the desk itself.
            if seniority == "recruiter" and tier in (TIER_TARGET, TIER_IB):
                category = "markets"
                reasons = ["campus recruiting at a firm you track"]
            else:
                stats["not_markets_role"] += 1
                continue
        score, why = score_row(category, tier, seniority, position)
        # Only derive an address when they did not share one: a real address
        # always beats a guess, and overwriting it would be a downgrade.
        guess = None if r.get("email") else guess_email(r["full_name"], bank)
        matches.append({
            **r,
            "bank": bank, "employer_tier": tier, "category": category,
            "seniority": seniority, "score": score, "reasons": reasons + why,
            "email_guess": guess["address"] if guess else None,
            "email_guess_confidence": guess["confidence"] if guess else None,
        })
        stats["matched"] += 1
        if r.get("email"):
            stats["with_shared_email"] += 1
        elif guess:
            stats["with_guessed_email"] += 1

    matches.sort(key=lambda m: (-m["score"], m["full_name"]))
    return matches, stats


# --------------------------------------------------------------- search links

# Deep links into LinkedIn's OWN search, filtered to 1st-degree connections.
# Opening a search page is ordinary use of the site; this just saves building
# the query by hand for every bank.
_SEARCH_TERMS = [
    "structured products sales",
    "equity derivatives sales",
    "cross-asset solutions",
    "flow sales",
    "sales trader",
    "global markets sales",
]


def search_links(banks: Optional[list[str]] = None) -> list[dict]:
    """Pre-built LinkedIn people-searches, restricted to existing connections."""
    from urllib.parse import quote

    def url(keywords: str, first_degree: bool = True) -> str:
        u = f"https://www.linkedin.com/search/results/people/?keywords={quote(keywords)}"
        # network=["F"] is LinkedIn's 1st-degree filter.
        return u + '&network=%5B%22F%22%5D' if first_degree else u

    out = [{"label": t, "scope": "Your connections", "url": url(t)} for t in _SEARCH_TERMS]
    for b in (banks or list(TARGET_BANKS))[:12]:
        out.append({
            "label": f"{b} — sales",
            "scope": "Your connections",
            "url": url(f'"{b}" sales markets'),
        })
    return out
