"""Euronext careers scraper (Workday CXS).

Euronext (the pan-European exchange operator) runs Workday under the shared
`hrhub` tenant, career site `Euronext_Career_Page`. Postings are essentially all
European (Paris, Amsterdam, Milan, Brussels, Lisbon, Dublin, Oslo…), and market-
infrastructure / trading-adjacent roles are common here.
"""
from __future__ import annotations

from .generic import WorkdayScraper


class EuronextScraper(WorkdayScraper):
    bank_name = "Euronext"
    careers_url = "https://www.euronext.com/en/about/careers"
    wd_host = "hrhub.wd3.myworkdayjobs.com"
    tenant = "hrhub"
    site = "Euronext_Career_Page"
