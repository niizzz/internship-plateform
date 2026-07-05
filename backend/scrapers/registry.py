"""Registry of all implemented bank scrapers.

To add a new bank: implement a `BankScraper` subclass in its own module under
`scrapers/`, then append the class to `ALL_SCRAPERS` below.

Status:
  IMPLEMENTED (19): JPMorgan, Citi, Goldman Sachs, Morgan Stanley, UBS,
    BNP Paribas, HSBC, Deutsche Bank, Barclays, SocGen, Santander, CACIB,
    Natixis, Lazard, Rothschild & Co, Bank of America, Commerzbank,
    Kepler Cheuvreux, Nomura (nomuracampus.tal.net; empty board off-season
    is a clean zero-result, offers appear when programmes open ~September)
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
]


def build_scrapers(headless: bool = True) -> list[BankScraper]:
    return [cls(headless=headless) for cls in ALL_SCRAPERS]
