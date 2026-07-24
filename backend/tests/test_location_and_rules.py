"""Location parsing correctness + assisted-apply sponsorship rules.

Run:  .venv/Scripts/python.exe -m tests.test_location_and_rules   (from backend/)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapers.base import parse_location  # noqa: E402
from apply_assist import build_field_values  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, got=None) -> None:
    global PASS, FAIL
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + ("" if cond else f"   got={got!r}"))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def sponsor_rules(rules: list) -> list:
    return [r for r in rules if "sponsor" in r[0]]


def main() -> int:
    # --- parse_location: no substring false-positives -----------------------
    city, country = parse_location("Kyiv, Ukraine")
    check("Ukraine is not the UK", country != "UK", (city, country))
    city, country = parse_location("Fukuoka, Japan")
    check("Fukuoka is not the UK", country != "UK", (city, country))
    city, country = parse_location("London, United Kingdom")
    check("London still resolves", (city, country) == ("London", "United Kingdom"), (city, country))

    # --- city -> country map gaps (drove wrong sponsorship answers) ---------
    for c, want in [("Manchester", "United Kingdom"), ("Birmingham", "United Kingdom"),
                    ("Glasgow", "United Kingdom"), ("Lugano", "Switzerland"),
                    ("The Hague", "Netherlands"), ("Roma", "Italy")]:
        got = parse_location(c)
        check(f"{c} -> {want}", got[1] == want, got)

    # --- sponsorship prefill ------------------------------------------------
    base = {"full_name": "A B", "email": "a@b.c", "gender": "male",
            "nationality": "Moroccan", "extras_json": {}}

    rules = build_field_values({**base, "offer_country": "United Kingdom"})
    sp = sponsor_rules(rules)
    check("UK offer: sponsor rule present and Yes", bool(sp) and sp[0][1] == "Yes",
          sp)

    rules = build_field_values({**base, "offer_country": "France"})
    sp = sponsor_rules(rules)
    check("France offer: sponsor rule present and No", bool(sp) and sp[0][1] == "No",
          sp)

    # Unknown country must produce NO sponsorship/right-to-work prefill at all:
    # a wrong pre-selected visa answer is worse than an empty field.
    rules = build_field_values({**base, "offer_country": "Unknown"})
    check("Unknown offer country: no sponsor prefill", not sponsor_rules(rules),
          sponsor_rules(rules))
    rules = build_field_values({**base, "offer_country": ""})
    check("Empty offer country: no sponsor prefill", not sponsor_rules(rules),
          sponsor_rules(rules))

    print(f"\nTOTAL: {PASS}/{PASS + FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
