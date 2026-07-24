"""RBC (Royal Bank of Canada) careers scraper (Workday CXS).

RBC runs Workday at rbc.wd3.myworkdayjobs.com. Two career sites carry early-
careers roles: `RBCEARLYTALENT1` (student/graduate pipeline) and `RBCGLOBAL1`
(all roles, incl. RBC Capital Markets London/Paris). We scrape both and dedupe;
the shared filter keeps the European S&T / Capital Markets roles.
"""
from __future__ import annotations

from .generic import WorkdayScraper, WORKDAY_EUROPE_COUNTRY_IDS


class RBCScraper(WorkdayScraper):
    bank_name = "RBC"
    careers_url = "https://jobs.rbc.com/"
    wd_host = "rbc.wd3.myworkdayjobs.com"
    tenant = "rbc"
    # RBCGLOBAL1 is RBC's entire global job base (thousands of roles), so pull
    # only its European subset via the country facet rather than paginating it
    # all. RBCEARLYTALENT1 (student pipeline) carries no European roles.
    sites = ["RBCGLOBAL1"]
    country_facet = "Country"
    europe_country_ids = WORKDAY_EUROPE_COUNTRY_IDS
