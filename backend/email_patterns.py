"""Derive a LIKELY work email from a name plus the employer's known format.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This is derivation from public convention, not lookup. Corporate mail formats
are widely published and near-universally firstname.lastname@domain at large
banks, so a name plus a domain produces a plausible address. Nothing is fetched,
nothing is scraped, and no directory is queried.

It is also NOT verification. There is deliberately no SMTP probe here: issuing
RCPT TO against a bank's mail server to see whether an address bounces is
directory-harvest behaviour, gets the source IP blocklisted, and returns nothing
useful anyway because these domains run catch-all configurations that accept
everything. So every address this produces is a GUESS and is labelled one.

PRACTICAL WARNING worth passing to the user: a cold email from an unknown
external sender to a bank address is heavily filtered, and a guessed address
either bounces or lands in a quarantine nobody reads. An address the person
actually shared (the LinkedIn export's own column) is worth far more than ten
derived ones, and a LinkedIn message to a first-degree connection beats both.
Guesses are therefore never used automatically: they must be chosen per person.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Mail domains, which are often NOT the web domain (Goldman's site is
# goldmansachs.com but its mail is gs.com). Confidence is recorded honestly:
# "known" where the mail domain is well established, "likely" where the web
# domain is being reused as a reasonable default.
#   bank -> (mail domain, confidence)
MAIL_DOMAINS: dict[str, tuple[str, str]] = {
    "Goldman Sachs": ("gs.com", "known"),
    "JPMorgan": ("jpmorgan.com", "known"),
    "Morgan Stanley": ("morganstanley.com", "known"),
    "Citi": ("citi.com", "known"),
    "Bank of America": ("bofa.com", "likely"),
    "UBS": ("ubs.com", "known"),
    "HSBC": ("hsbc.com", "likely"),
    "BNP Paribas": ("bnpparibas.com", "known"),
    "Deutsche Bank": ("db.com", "known"),
    "Barclays": ("barclays.com", "known"),
    "Société Générale": ("sgcib.com", "likely"),
    "Santander": ("santander.com", "likely"),
    "Crédit Agricole CIB": ("ca-cib.com", "known"),
    "Natixis": ("natixis.com", "known"),
    "Lazard": ("lazard.com", "known"),
    "Lazard Frères Gestion": ("lazardfreresgestion.fr", "likely"),
    "Rothschild & Co": ("rothschildandco.com", "known"),
    "Commerzbank": ("commerzbank.com", "known"),
    "Kepler Cheuvreux": ("keplercheuvreux.com", "likely"),
    "Nomura": ("nomura.com", "known"),
    "BBVA": ("bbva.com", "known"),
    "Euronext": ("euronext.com", "known"),
    "RBC": ("rbccm.com", "likely"),
    "CMC Markets": ("cmcmarkets.com", "likely"),
    "Amundi": ("amundi.com", "known"),
    "Maven Securities": ("mavensecurities.com", "likely"),
    "UniCredit": ("unicredit.eu", "likely"),
    "Mizuho": ("mizuhogroup.com", "likely"),
    "Jefferies": ("jefferies.com", "known"),
    "Macquarie": ("macquarie.com", "known"),
    "Standard Chartered": ("sc.com", "known"),
    "ING": ("ing.com", "known"),
    "Optiver": ("optiver.com", "known"),
    "IMC": ("imc.com", "likely"),
    "Flow Traders": ("flowtraders.com", "likely"),
    "Jane Street": ("janestreet.com", "known"),
    "Citadel Securities": ("citadelsecurities.com", "known"),
    "Citadel": ("citadel.com", "known"),
    "XTX Markets": ("xtxmarkets.com", "likely"),
    "Man Group": ("man.com", "known"),
}

# Format per bank where it is known to differ from the dominant convention.
# Placeholders: {f} first initial, {l} last initial, {first}, {last}.
FORMATS: dict[str, str] = {
    # Jefferies is the common exception among these: first initial + surname.
    "Jefferies": "{f}{last}",
}
DEFAULT_FORMAT = "{first}.{last}"


def _ascii_fold(s: str) -> str:
    """Strip accents: European desks are full of names that do not survive a
    naive lower(), and mail systems use the folded form (José -> jose)."""
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _clean_part(s: str) -> str:
    """One name part, mail-safe: folded, lowercased, punctuation dropped.

    Hyphens go too (jean-pierre -> jeanpierre): banks vary, but the unhyphenated
    form is the more common local-part and this is a guess either way.
    """
    s = _ascii_fold(s).lower()
    s = re.sub(r"[^a-z]", "", s)
    return s


def split_name(full_name: str) -> tuple[str, str]:
    """(first, last) from a display name.

    Particles are kept with the surname (van der Berg -> vanderberg), which is
    how they usually appear in a mail local-part. A single-token name yields no
    surname and the caller declines to guess.
    """
    parts = [p for p in re.split(r"\s+", (full_name or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return _clean_part(parts[0]), ""
    first = _clean_part(parts[0])
    last = "".join(_clean_part(p) for p in parts[1:])
    return first, last


def guess_email(full_name: str, bank: str) -> Optional[dict]:
    """A plausible work address, or None when there is nothing to base one on.

    Returns {address, confidence, pattern, domain}. `confidence` is about the
    DOMAIN and FORMAT only — it never means the mailbox exists. Nothing here can
    establish that, which is exactly why the result is labelled a guess.
    """
    entry = MAIL_DOMAINS.get(bank)
    if not entry:
        return None
    domain, domain_confidence = entry
    first, last = split_name(full_name)
    if not first or not last:
        # A mononym or an initial-only surname gives nothing to build from, and
        # inventing one would be worse than offering nothing.
        return None

    fmt = FORMATS.get(bank, DEFAULT_FORMAT)
    local = fmt.format(first=first, last=last, f=first[:1], l=last[:1])
    return {
        "address": f"{local}@{domain}",
        "confidence": domain_confidence if bank in FORMATS or fmt == DEFAULT_FORMAT
        else "likely",
        "pattern": fmt.replace("{first}", "first").replace("{last}", "last")
                      .replace("{f}", "f").replace("{l}", "l") + "@" + domain,
        "domain": domain,
    }
