"""Maven Securities careers scraper (Greenhouse).

Maven Securities is a London-based proprietary trading firm / options market
maker (offices in London, Amsterdam, Hong Kong, Chicago). Its careers site
embeds a Greenhouse board (token `mavensecuritiesholdingltd`); the "Emerging
Talent" trader internship + graduate trader roles are prime S&T targets.
"""
from __future__ import annotations

from .generic import GreenhouseScraper


class MavenSecuritiesScraper(GreenhouseScraper):
    bank_name = "Maven Securities"
    careers_url = "https://www.mavensecurities.com/jobs/"
    gh_token = "mavensecuritiesholdingltd"
