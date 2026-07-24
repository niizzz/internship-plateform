"""CMC Markets careers scraper (Workday CXS).

CMC Markets (UK CFD / spread-betting broker and institutional liquidity
provider) runs Workday at cmcmarkets.wd3.myworkdayjobs.com, career site
`CMC_Markets_Careers`. Offices in London, Warsaw, and APAC; the shared filter
keeps the London/EU trading-adjacent early-careers roles.
"""
from __future__ import annotations

from .generic import WorkdayScraper


class CMCMarketsScraper(WorkdayScraper):
    bank_name = "CMC Markets"
    careers_url = "https://www.cmcmarkets.com/group/careers"
    wd_host = "cmcmarkets.wd3.myworkdayjobs.com"
    tenant = "cmcmarkets"
    site = "CMC_Markets_Careers"
