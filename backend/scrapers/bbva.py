"""BBVA careers scraper (Workday CXS).

BBVA runs Workday at bbva.wd3.myworkdayjobs.com, career site `BBVA`. Mostly
Spain + LatAm; the shared filter keeps the Spanish (Madrid/Bilbao) markets roles.
"""
from __future__ import annotations

from .generic import WorkdayScraper


class BBVAScraper(WorkdayScraper):
    bank_name = "BBVA"
    careers_url = "https://careers.bbva.com/"
    wd_host = "bbva.wd3.myworkdayjobs.com"
    tenant = "bbva"
    site = "BBVA"
