"""Lazard Frères Gestion careers scraper.

Lazard Frères Gestion (LFG) is the Lazard group's French asset-management arm
(listed-security investing: equity, fixed income, structured products, private
wealth) — a distinct entity from Lazard's M&A / advisory business, but posted on
the *same* Oracle HCM site (icbpjb.fa.ocs.oraclecloud.com/LazardProfessionalCareers).

Only LFG job descriptions name "Lazard Frères Gestion", so this scraper reuses
the Lazard Oracle machinery unchanged and simply keeps the requisitions carrying
that marker (see LazardScraper._keep). Unlike Lazard IB, LFG has real markets
desks, so structuring / fixed-income / cross-asset-sales interns show up here.
"""
from __future__ import annotations

from .lazard import LazardScraper


class LazardFreresGestionScraper(LazardScraper):
    bank_name = "Lazard Frères Gestion"
    careers_url = "https://www.lazardfreresgestion.fr/"
    freres_gestion_only = True
