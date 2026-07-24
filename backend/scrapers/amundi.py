"""Amundi careers scraper (Talentsoft).

Amundi (Europe's largest asset manager, Crédit Agricole / SocGen heritage) runs
Talentsoft at jobs.amundi.com — same platform family as CACIB, but the newer
`ts-offer-list-item` list markup. Location + description come from the detail
page (JobPosting JSON-LD). Mostly Paris, plus London / Dublin / Milan / Madrid.
Amundi has real markets desks (trading, structuring, quant) so S&T interns do
appear here.
"""
from __future__ import annotations

from .generic import TalentsoftScraper


class AmundiScraper(TalentsoftScraper):
    bank_name = "Amundi"
    careers_url = "https://jobs.amundi.com/"
    base_url = "https://jobs.amundi.com"
    lcid = "2057"
