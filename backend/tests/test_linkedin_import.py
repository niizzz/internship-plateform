"""LinkedIn connections import: employer matching, role classification, ranking.

The employer matcher is the risky part. It runs on free text a user typed into
their own profile, so a naive substring test quietly turns "Ingenico" into ING
and "SEBastian Consulting" into SEB, and the user then writes to a stranger
about a trading desk they have never worked on.

Run:  .venv/Scripts/python.exe -m tests.test_linkedin_import   (from backend/)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import email_patterns as ep  # noqa: E402
import linkedin_import as li  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, got=None) -> None:
    global PASS, FAIL
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + ("" if cond else f"   got={got!r}"))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


SAMPLE = '''Notes:
"When exporting your connection data, you may notice that some connections do not have an email address listed."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Tom,Baker,https://www.linkedin.com/in/tbaker,,Goldman Sachs International,"Analyst, Equity Derivatives Sales",02 Feb 2026
Mark,Evans,https://www.linkedin.com/in/mevans,,Ingenico,Head of Sales,03 Mar 2024
Sara,Chen,https://www.linkedin.com/in/schen,,Barclays,Software Engineer - Markets,08 Aug 2025
Ines,Dupont,https://www.linkedin.com/in/idupont,ines@example.com,Citi,"Campus Recruiter, Early Careers",21 Sep 2025
'''


def main() -> int:
    # ------------------------------------------------- employer matching
    for company, expect, tier in [
        ("Goldman Sachs International", "Goldman Sachs", li.TIER_TARGET),
        ("J.P. Morgan", "JPMorgan", li.TIER_TARGET),
        ("BNP Paribas CIB", "BNP Paribas", li.TIER_TARGET),
        # Credit Suisse folded into UBS in 2023; a CS connection is a UBS one.
        ("Credit Suisse", "UBS", li.TIER_TARGET),
        ("Jefferies International", "Jefferies", li.TIER_IB),
        ("Optiver", "Optiver", li.TIER_MM),
    ]:
        got, got_tier = li.match_employer(company)
        check(f"{company!r} -> {expect}", got == expect and got_tier == tier, (got, got_tier))

    # Word-boundary matching. Each of these contains a bank alias as a substring
    # and must NOT match.
    for company in ["Ingenico", "SEBastian Consulting", "Citadel Bakery Ltd",
                    "Mansfield Group", "Amundsen Logistics", "Marexpo Events"]:
        got, _ = li.match_employer(company)
        check(f"{company!r} is not a bank", got is None, got)

    # Aliases that are also ordinary words or surnames. "Deutsche Telekom" and
    # "Deutsche Post" are big employers and neither is Deutsche Bank.
    for company in ["Deutsche Telekom AG", "Deutsche Post DHL", "Citadel Bakery Ltd",
                    "Chase Plumbing Ltd", "Goldman Plumbing", "Maven Marketing Agency",
                    "Intesa Bakery", "Tradition Bakery"]:
        got, _ = li.match_employer(company)
        check(f"{company!r} does not match on an ordinary word", got is None, got)

    # ...while the real firms still resolve through the same guard, including the
    # European legal forms that follow the name.
    for company, expect in [
        ("Deutsche Bank AG", "Deutsche Bank"),
        ("Nordea Bank Abp", "Nordea"),
        ("Danske Bank A/S", "Danske Bank"),        # a/s must not tokenise to a + s
        ("Intesa Sanpaolo S.p.A.", "Intesa Sanpaolo"),
        ("Tradition Securities", "Tradition"),
        ("Man Group plc", "Man Group"),
        ("JPMorgan Chase & Co", "JPMorgan"),
        ("Citadel", "Citadel"),
    ]:
        got, _ = li.match_employer(company)
        check(f"{company!r} still resolves to {expect}", got == expect, got)

    # Longest-alias-first ordering, or "citadel securities" resolves to "Citadel".
    check("citadel securities beats the shorter citadel alias",
          li.match_employer("Citadel Securities")[0] == "Citadel Securities",
          li.match_employer("Citadel Securities"))
    check("blank employer matches nothing", li.match_employer("")[0] is None)

    # ------------------------------------------------- role classification
    for pos, expect in [
        ("Analyst, Equity Derivatives Sales", "sales"),
        ("Structurer, Cross-Asset Solutions", "structuring"),
        ("FX Trader", "trading"),
        ("Summer Analyst - Markets", "markets"),
        ("Vente Derives Actions", "sales"),          # French desk title
        ("Negociateur Produits Structures", "structuring"),
    ]:
        cat, _ = li.classify_role(pos)
        check(f"{pos!r} -> {expect}", cat == expect, cat)

    # A control/tech/ops function that merely supports Markets is not someone who
    # can talk about the desk or refer into it.
    for pos in ["Software Engineer - Markets", "Operations Analyst",
                "Market Risk Manager", "Compliance Officer - Global Markets",
                "Product Control Analyst"]:
        cat, reasons = li.classify_role(pos)
        check(f"{pos!r} is excluded as non-front-office", cat is None, (cat, reasons))

    for pos in ["Head of Sales", "Account Executive", "Software Developer"]:
        check(f"{pos!r} has no markets signal", li.classify_role(pos)[0] is None)

    check("an empty title is not classified", li.classify_role("")[0] is None)

    # ------------------------------------------------- seniority
    for pos, expect in [
        ("Summer Analyst, Global Markets", "intern"),     # intern beats analyst
        ("Managing Director - FX Sales", "senior"),
        ("Analyst, Equity Sales", "junior"),
        ("Campus Recruiter, Early Careers", "recruiter"),
        ("Stagiaire Salle des Marches", "intern"),
        ("Something Unlabelled", "junior"),               # safe default
    ]:
        check(f"seniority of {pos!r} is {expect}", li.detect_seniority(pos) == expect,
              li.detect_seniority(pos))

    # ------------------------------------------------- scoring
    sales_j = li.score_row("sales", li.TIER_TARGET, "junior", "Equity Derivatives Sales")[0]
    trade_j = li.score_row("trading", li.TIER_TARGET, "junior", "Rates Trader")[0]
    sales_s = li.score_row("sales", li.TIER_TARGET, "senior", "Head of Sales")[0]
    sales_mm = li.score_row("sales", li.TIER_MM, "junior", "Sales")[0]
    check("sales outranks trading (the desk being targeted)", sales_j > trade_j, (sales_j, trade_j))
    check("a junior outranks a senior (reply rate, not importance)",
          sales_j > sales_s, (sales_j, sales_s))
    check("a tracked bank outranks a market maker", sales_j > sales_mm, (sales_j, sales_mm))
    check("the candidate's own product area scores a bonus",
          li.score_row("sales", li.TIER_TARGET, "junior", "Structured Products Sales")[0]
          > li.score_row("sales", li.TIER_TARGET, "junior", "Sales")[0])
    check("the score is capped at 100", sales_j <= 100, sales_j)

    # ------------------------------------------------- csv parsing
    rows = li.parse_connections_csv(SAMPLE)
    check("the Notes preamble is skipped and all rows parse", len(rows) == 4, len(rows))
    check("first and last name are joined", rows[0]["full_name"] == "Tom Baker", rows[0])
    check("a blank email becomes None", rows[0]["email"] is None, rows[0]["email"])
    check("an email is read when present", rows[3]["email"] == "ines@example.com")
    check("utf-8 BOM is tolerated",
          len(li.parse_connections_csv(SAMPLE.encode("utf-8-sig"))) == 4)

    try:
        li.parse_connections_csv("a,b,c\n1,2,3")
        check("a non-LinkedIn csv is rejected", False, "no error raised")
    except ValueError as e:
        check("a non-LinkedIn csv is rejected with a pointer to the export",
              "Connections" in str(e), str(e))

    # ------------------------------------------------- end to end
    matches, stats = li.analyse(rows)
    names = [m["full_name"] for m in matches]
    check("the desk hire is kept", "Tom Baker" in names, names)
    check("the non-finance employer is dropped", "Mark Evans" not in names, names)
    check("the engineer on the markets floor is dropped", "Sara Chen" not in names, names)
    # A recruiter has no markets term in their title and would fail the role
    # filter, but they are exactly who answers "is this programme open?".
    check("a campus recruiter at a tracked bank is kept", "Ines Dupont" in names, names)
    check("results are ranked, best first",
          matches == sorted(matches, key=lambda m: (-m["score"], m["full_name"])))
    check("every match explains itself", all(m["reasons"] for m in matches))
    check("skip reasons are counted", stats["no_employer_match"] == 1
          and stats["not_markets_role"] == 1, stats)

    # ------------------------------------------------- search links
    links = li.search_links()
    check("search links are produced", len(links) > 5, len(links))
    check("searches are scoped to existing connections",
          all("network=" in l["url"] for l in links))
    check("searches point at LinkedIn's own search page",
          all(l["url"].startswith("https://www.linkedin.com/search/") for l in links))

    # ------------------------------------------------- derived work emails
    # Derivation from a published mail format, never a lookup. Every result is a
    # guess and the UI must never route to one on its own.
    g = ep.guess_email("Tom Baker", "Goldman Sachs")
    check("a known bank yields an address on its real mail domain",
          g and g["address"] == "tom.baker@gs.com", g)
    check("confidence is reported", g and g["confidence"] in ("known", "likely"), g)
    # European desks are full of names a naive lower() mangles.
    check("accents are folded",
          ep.guess_email("Jose Garcia", "Santander")["address"].startswith("jose.garcia@"))
    check("accented input folds to the same local part",
          ep.guess_email("José García", "Santander")["address"]
          == ep.guess_email("Jose Garcia", "Santander")["address"])
    check("hyphens are dropped",
          ep.guess_email("Jean-Pierre Dubois", "BNP Paribas")["address"]
          == "jeanpierre.dubois@bnpparibas.com")
    check("surname particles join the surname",
          ep.guess_email("Nina van der Berg", "ING")["address"] == "nina.vanderberg@ing.com")
    check("a bank with a different format uses it",
          ep.guess_email("Emma Klein", "Jefferies")["address"] == "eklein@jefferies.com")
    # Guessing from half a name would invent a person.
    check("a mononym yields no guess", ep.guess_email("Madonna", "Citi") is None)
    check("an unknown employer yields no guess", ep.guess_email("A B", "Nowhere Ltd") is None)
    check("an empty name yields no guess", ep.guess_email("", "Citi") is None)

    # A real address always beats a derived one, so a shared address suppresses
    # the guess rather than sitting alongside it.
    rows2 = [
        {"full_name": "Tom Baker", "company": "Goldman Sachs",
         "position": "Analyst, Equity Derivatives Sales", "email": None},
        {"full_name": "Claire Moreau", "company": "Societe Generale",
         "position": "VP Structured Products Sales", "email": "real@sg.com"},
    ]
    m2, st2 = li.analyse(rows2)
    by = {x["full_name"]: x for x in m2}
    check("a missing address gets a derived one",
          by["Tom Baker"]["email_guess"] == "tom.baker@gs.com", by["Tom Baker"])
    check("a shared address suppresses the guess",
          by["Claire Moreau"]["email_guess"] is None, by["Claire Moreau"])
    check("shared and guessed are counted separately",
          st2["with_shared_email"] == 1 and st2["with_guessed_email"] == 1, st2)


    print(f"\nTOTAL: {PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
