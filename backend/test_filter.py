"""Filter sanity checks.

Two layers:
  1. is_snt_role  — category detection (title + description aware)
  2. the full orchestrator gate — is_internship_or_grad(TITLE-only)
                                   AND is_snt_role AND in_europe

Run: .venv/Scripts/python.exe test_filter.py
"""
from scrapers.base import is_snt_role, is_internship_or_grad, in_europe

# --- Layer 1: is_snt_role (category) ---------------------------------------
# (title, description, expected_category_or_None, label)
SNT_CASES = [
    # KEEP — strong S&T markers in title
    ("Global Markets Summer Analyst", "", "markets", "umbrella division"),
    ("Equity Derivatives Intern", "", "markets", "product name only"),
    ("FX Sales Analyst", "", "sales", "fx sales in title"),
    ("Macro Strategist - Off-Cycle", "", "markets", "macro desk"),
    ("Cross-Asset Solutions Intern", "", "markets", "structured solutions desk"),
    ("Prime Brokerage Summer Analyst", "", "markets", "prime services"),
    ("Sales Trader Internship", "", "sales", "sales trader title"),
    ("Stage - Equity Sales - Rothschild & Co Redburn - Septembre 2026", "", "sales", "Redburn equity sales (live Rothschild)"),
    ("Equity Sales Summer Analyst", "", "sales", "equity sales singular"),
    ("London - Long Term Internship 2025 - Automated Client Execution", "", "markets",
     "BNP automated client execution = GM e-trading desk"),
    ("Equity Execution Services Intern", "", "markets", "execution services desk"),
    ("Securities Division Internship", "", "markets", "securities umbrella"),
    ("S&T Summer Analyst", "", "markets", "S&T abbreviation"),
    ("Quant Trading Strategist", "", "trading", "quant trading"),
    ("Flow Sales Analyst", "", "sales", "flow sales"),
    ("Rates Trading Intern", "", "trading", "rates trading"),
    ("Credit Flow Analyst", "", "markets", "credit flow"),
    ("Delta One Summer Analyst", "", "markets", "delta one"),
    ("Structured Products Intern", "", "markets", "structured products"),
    ("FIC Off-Cycle", "", "markets", "BNP FIC abbreviation"),
    ("Secured Financing Structuring", "", "structuring", "structuring desk"),
    ("Automated Market Making (AMM)", "", "markets", "market making"),
    ("Commercial & Investment Banking - Markets - Off-Cycle Internship - London", "", "markets", "JPM CIB-Markets division = S&T"),
    # BofA campus board (tal.net), live 2026-09-15
    ("Prime Financing, 2027 1-Year Placement Analyst - London", "", "markets",
     "BofA prime financing = prime brokerage desk"),
    ("Global Markets, (Sales & Trading Private Side) Rotational Programme 2027, Summer Analyst - London",
     "", "trading", "BofA GM S&T rotational summer"),
    ("Global Markets Chief Operating Office 2027 Summer Analyst Program - London", "", None,
     "BofA GM COO = business management, not a desk seat"),
    ("GM COO Summer Analyst", "", None, "COO acronym form"),
    ("GM Credit 2027 Summer Analyst - London", "", None, "BofA enterprise credit = underwriting, not S&T"),
    # KEEP — generic title rescued by an UNAMBIGUOUS desk phrase in desc
    ("Summer Analyst", "Join our Global Markets division covering Fixed Income, FX and Equities.",
     "markets", "generic title, strong desk phrase in desc"),
    ("Intern - Team Placement", "You will sit on the equity derivatives trading desk.",
     "trading", "generic title, equity derivatives in desc"),
    # DROP — generic title, desc only mentions markets as weak boilerplate
    ("Summer Internship", "A rotational programme with exposure to various markets and teams.",
     None, "generic title, weak markets boilerplate"),
    # DROP — corporate/commercial bank & adjacent functions (DB-style noise)
    ("Internship - Structured Lending Team", "Part of our Global Markets-adjacent financing group.",
     None, "structured lending = financing, not S&T"),
    ("Intern - Global Corporate Coverage Belgium", "Supports trading and markets clients.",
     None, "corporate coverage"),
    ("Internship Private Equity (Club Deal)", "Exposure to capital markets and trading.",
     None, "private equity"),
    ("Private Equity Sales Support Intern", "", None, "private equity sales NOT rescued by equity-sales signal"),
    ("Intern - Relationship & Transaction Management", "Global markets transaction support.",
     None, "transaction management"),
    ("DWS CEO Office Intern", "Asset management, some markets exposure.", None, "DWS asset mgmt"),
    # DROP — title has its own domain noun, desc boilerplate must NOT rescue it
    ("Stage Economie - Juillet 2026", "Au sein de la salle des marchés, structured products...",
     None, "FR economics title, desk in desc boilerplate"),
    ("Internship Business Development", "Our global markets division covers FICC and equities.",
     None, "biz-dev title, markets in desc"),
    # KEEP — French desk terms in title
    ("Stage Assistant Négociateur - Juillet 2026", "", "trading", "FR negociateur=trader"),
    ("Stage Produits Structurés", "", "structuring", "FR produits structures"),
    # DROP — non-front-office function in title beats S&T boilerplate in desc
    ("Market Risk Internship", "Supports the Global Markets trading desks.", None, "market risk = risk fn"),
    ("Credit Risk Analyst", "Covers the markets and trading book.", None, "credit risk = risk fn"),
    ("Operations - Cash Management", "Global Markets settlement support.", None, "ops"),
    ("Commodities Product Control VP", "Controls the commodities trading P&L.", None, "product control"),
    ("Custody Product Manager", "Securities services for institutional clients.", None, "custody"),
    ("Regulatory Governance Specialist", "Markets regulation.", None, "governance"),
    # DROP — other divisions
    ("Investment Banking Summer Analyst", "M&A coverage of TMT clients.", None, "pure IB"),
    ("Wealth Management Intern", "", None, "wealth mgmt"),
    ("Compliance Officer", "Markets surveillance.", None, "compliance"),
    ("Private Banking Sales Intern", "FX and equities for HNW clients.", None, "PB sales not SNT"),
    ("Software Engineer - Markets", "", None, "tech role mentioning markets"),
    ("Equity Capital Markets Analyst", "", None, "ECM = banking; no delimiter before 'markets'"),
    ("2027 Corporate & Investment Bank - Global Investment Banking - Off-Cycle Internship", "", None, "JPM Global IB = coverage"),
    ("Asset Management Summer Analyst", "", None, "AM"),
    ("HR Intern", "", None, "HR"),
]

# --- Layer 2: full gate (program is TITLE-only) ----------------------------
# (title, description, location, expected_kept: bool, label)
GATE_CASES = [
    # KEEP — real internships seen in live data
    ("Markets - Sales, Trading & Structuring, Off-Cycle Internship, Frankfurt",
     "", "Frankfurt, Germany", True, "Citi Frankfurt S&T"),
    ("Markets Quantitative Analysis Summer Intern, Hungary, 2026",
     "", "Budapest, Hungary", True, "Citi Hungary quant"),
    ("London - Long Term Internship 2026 - Credit E-Trading",
     "", "London", True, "BNP credit e-trading"),
    ("London - Long Internship 2026 - FIC Off-Cycle", "", "London", True, "BNP FIC"),
    ("London - 2027 Summer Internship - Global Markets", "", "London", True,
     "BNP 2027 GM summer (live 18 Aug 2026)"),
    ("London - 2027 Women in Trading Academy - Global Markets", "", "London", True,
     "BNP Academy programme — no other program word in the title"),
    ("London - Long Term Internship 2025 - Automated Client Execution", "", "London", True,
     "BNP automated client execution"),
    ("Stage - Equity Sales - Rothschild & Co Redburn - Septembre 2026", "", "Paris, France",
     True, "Rothschild Redburn equity-sales intern (live)"),
    ("Tirocinio Global Markets Structuring - Milano", "", "Milan, Italy", True,
     "UniCredit Italian tirocinio S&T"),
    ("Working Student Markets Trading (m/f/d)", "", "Munich, Germany", True,
     "UniCredit German working-student S&T"),
    # Iberian program words + Santander's HQ suburb. Santander/BBVA post Madrid
    # roles in Spanish and Lisboa/Porto roles in Portuguese; before 2026-08-25
    # neither language was in the program vocabulary, and "Boadilla del Monte"
    # (Ciudad Santander, where the SCIB trading floor sits) failed in_europe —
    # so a Spanish-language S&T internship was dropped twice over.
    ("Becario Sala de Mercados | Trading", "", "Madrid, Spain", True,
     "ES becario S&T"),
    ("Prácticas Trading - SCIB", "", "Madrid, Spain", True, "ES practicas S&T"),
    ("Estágio Global Markets", "", "Porto, Portugal", True, "PT estagio S&T"),
    ("Estagiário | Sala de Mercados", "", "Lisboa, Portugal", True, "PT estagiario S&T"),
    ("Markets Sales & Trading Summer Analyst | SCIB", "", "Boadilla del Monte, Spain", True,
     "Santander HQ suburb resolves to Spain"),
    # DROP — Iberian program words on a non-S&T role stay out
    ("Estágio Controlo e Governo de Riscos", "", "Lisboa, Portugal", False,
     "PT risk-governance estagio is not S&T (live Santander)"),
    ("Becario Auditoría Interna", "", "Madrid, Spain", False, "ES audit becario is not S&T"),
    # DROP — Brazilian Porto Alegre must not read as Portugal
    ("Estágio Global Markets", "", "Porto Alegre, Brazil", False,
     "Santander Brasil Porto Alegre is not Europe"),
    # DROP — full-time roles whose DESCRIPTION mentions intern/grad boilerplate
    ("Fixed Income Analyst", "We also run a summer internship programme.", "London",
     False, "FT analyst, desc-only intern word"),
    ("Commercial Banking Onboarding Manager", "Gain valuable work experience.", "Warsaw, Poland",
     False, "FT manager, desc work-experience"),
    ("GCM - Private Capital Markets - Vice President", "Placement opportunities exist.",
     "London", False, "VP, desc placement"),
    ("Junior Natural Gas Option Trader", "Our internships are world-class.", "London",
     False, "junior FT trader, not an internship"),
    # DROP — internship but wrong category / region
    ("Technology - Cybersecurity, Summer Analyst", "", "Budapest, Hungary", False, "tech intern"),
    ("London - 2027 Summer Internship - M&A - Global Banking", "", "London", False,
     "BNP M&A summer is IB, not S&T"),
    ("London - 2027 Summer Internship - Capital Markets -Global Banking", "", "London",
     False, "BNP ECM/DCM summer is IB, not S&T"),
    ("Academy Manager, Learning & Development", "", "London", False,
     "'Academy' in a non-S&T FT role stays out"),
    ("Investment Banking Placement Analyst", "", "Milan, Italy", False, "IB placement"),
    ("Global Markets Summer Analyst", "", "New York, United States", False, "S&T intern but US"),
    # BofA campus board (tal.net), live 2026-09-15
    ("Global Markets Sales and Trading 2027 Off-Cycle Analyst - Frankfurt", "", "Frankfurt, Germany",
     True, "BofA S&T off-cycle Frankfurt"),
    ("Prime Financing, 2027 1-Year Placement Analyst - London", "", "London, United Kingdom",
     True, "BofA prime financing placement"),
    ("Global Markets Chief Operating Office, Summer Analyst - Paris", "", "Paris, France",
     False, "BofA GM COO summer is not S&T"),
    ("Global Corporate Banking 2027, Off-Cycle Analyst – Dubai", "", "Dubai, United Arab Emirates",
     False, "BofA EMEA-region row outside Europe"),
]


def run_snt():
    ok = bad = 0
    print("=== Layer 1: is_snt_role ===")
    for title, desc, want, label in SNT_CASES:
        got = is_snt_role(title, desc)
        flag = got == want
        ok += flag
        bad += not flag
        print(f"[{'OK ' if flag else 'FAIL'}] {label!r:38s} want={want!r:10s} got={got!r:10s}")
    return ok, bad


def run_gate():
    ok = bad = 0
    print("\n=== Layer 2: full gate (program=title-only AND s&t AND europe) ===")
    for title, desc, loc, want, label in GATE_CASES:
        kept = bool(is_internship_or_grad(title, desc) and is_snt_role(title, desc) and in_europe(loc))
        flag = kept == want
        ok += flag
        bad += not flag
        print(f"[{'OK ' if flag else 'FAIL'}] {label!r:38s} want={want!s:5s} got={kept!s:5s}  {title[:45]!r}")
    return ok, bad


if __name__ == "__main__":
    import sys

    o1, b1 = run_snt()
    o2, b2 = run_gate()
    print(f"\nLayer 1: {o1}/{o1 + b1}   Layer 2: {o2}/{o2 + b2}   TOTAL: {o1 + o2}/{o1 + o2 + b1 + b2}")
    # Exit non-zero on failure: this script used to always exit 0, so a broken
    # filter still looked green to any runner that checks the exit code.
    sys.exit(1 if (b1 + b2) else 0)
