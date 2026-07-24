"""Registry of all implemented bank scrapers.

To add a new bank: implement a `BankScraper` subclass in its own module under
`scrapers/`, then append the class to `ALL_SCRAPERS` below.

Status:
  IMPLEMENTED (26): JPMorgan, Citi, Goldman Sachs, Morgan Stanley, UBS,
    BNP Paribas, HSBC, Deutsche Bank, Barclays, SocGen, Santander, CACIB,
    Natixis, Lazard, Rothschild & Co, Bank of America, Commerzbank,
    Kepler Cheuvreux, Nomura (empty board off-season is a clean zero),
    BBVA, Euronext, RBC, CMC Markets, Amundi, Lazard Frères Gestion,
    Maven Securities.
  HSBC now scrapes the Emerging Talent board (apply.careers.hsbc.com), which
  carries the real "Markets - Sales and Trading" student programmes; London
  S&T internships appear Sept–Nov.
"""
from __future__ import annotations

from .base import BankScraper
from .jpmorgan import JPMorganScraper
from .citi import CitiScraper
from .goldman import GoldmanScraper
from .morganstanley import MorganStanleyScraper
from .ubs import UBSScraper
from .bnp import BNPParibasScraper
from .hsbc import HSBCScraper
from .deutschebank import DeutscheBankScraper
from .barclays import BarclaysScraper
from .socgen import SocGenScraper
from .santander import SantanderScraper
from .cacib import CACIBScraper
from .natixis import NatixisScraper
from .lazard import LazardScraper
from .rothschild import RothschildScraper
from .bofa import BofAScraper
from .commerzbank import CommerzbankScraper
from .kepler import KeplerScraper
from .nomura import NomuraScraper
# Firms on standardized ATS platforms (config-driven, see scrapers/generic.py).
from .bbva import BBVAScraper
from .euronext import EuronextScraper
from .rbc import RBCScraper
from .cmc import CMCMarketsScraper
from .amundi import AmundiScraper
from .lazard_fg import LazardFreresGestionScraper
from .maven import MavenSecuritiesScraper
from .unicredit import UniCreditScraper

ALL_SCRAPERS: list[type[BankScraper]] = [
    JPMorganScraper,
    CitiScraper,
    GoldmanScraper,
    MorganStanleyScraper,
    UBSScraper,
    BNPParibasScraper,
    HSBCScraper,
    DeutscheBankScraper,
    BarclaysScraper,
    SocGenScraper,
    SantanderScraper,
    CACIBScraper,
    NatixisScraper,
    LazardScraper,
    RothschildScraper,
    BofAScraper,
    CommerzbankScraper,
    KeplerScraper,
    NomuraScraper,
    BBVAScraper,
    EuronextScraper,
    RBCScraper,
    CMCMarketsScraper,
    AmundiScraper,
    LazardFreresGestionScraper,
    MavenSecuritiesScraper,
    UniCreditScraper,
]


def build_scrapers(headless: bool = True) -> list[BankScraper]:
    return [cls(headless=headless) for cls in ALL_SCRAPERS]
