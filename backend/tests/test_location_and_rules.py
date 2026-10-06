"""Location parsing correctness + assisted-apply sponsorship rules.

Run:  .venv/Scripts/python.exe -m tests.test_location_and_rules   (from backend/)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapers.base import parse_location  # noqa: E402
from apply_assist import (_dial_from_text, _national_display, _phone_value,  # noqa: E402
                          _split_phone, build_field_values)

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
                    ("The Hague", "Netherlands"), ("Roma", "Italy"),
                    # Santander's global HQ / SCIB trading floor is posted as
                    # this suburb, never as "Madrid".
                    ("Boadilla del Monte", "Spain"),
                    ("Boadilla del Monte, Madrid, Spain", "Spain"),
                    ("Porto", "Portugal"), ("Oporto", "Portugal")]:
        got = parse_location(c)
        check(f"{c} -> {want}", got[1] == want, got)

    # Non-European compounds of a European city name. Santander Brasil posts
    # hundreds of Porto Alegre roles; whole-word matching alone lets them
    # through as Portugal.
    for trap in ["Porto Alegre", "PORTO ALEGRE, RS", "Porto Velho", "Porto Seguro"]:
        got = parse_location(trap)
        check(f"{trap} is not Portugal", got == (None, None), got)

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

    # --- secondary address / phone slots stay empty -------------------------
    # Found live on UBS BrassRing (2026-08-17): the street address was
    # duplicated into the optional "Address Line 2" and the mobile into
    # "Other phone". Both are optional fields where a duplicate is noise.
    addr_profile = {**base, "offer_country": "United Kingdom",
                    "address": "1 Rue de Exemple", "city": "Paris",
                    "phone": "+33 6 12 34 56 78"}
    r2 = build_field_values(addr_profile)

    def fills(ctx, value):
        return any(re.search(rx, ctx.lower()) and val == value
                   for rx, val, _alts, _kind in r2)

    street, phone = "1 Rue de Exemple", "+33 6 12 34 56 78"
    for ctx, want in [("profile_5_0_address1_txt_0 address line 1", True),
                      ("addressLine1", True),
                      ("street address", True),
                      ("adresse", True),
                      ("profile_6_0_address2_txt_0 address line 2", False),
                      ("addressline2", False),
                      ("apartment number", False)]:
        check(f"address{'  fills' if want else ' skips'} {ctx!r}",
              fills(ctx, street) == want)

    for ctx, want in [("mobile phone", True),
                      ("home phone", True),
                      ("phone number with country code", True),
                      ("profile_12_0_workphone_txt_0 other phone", False),
                      ("work phone", False)]:
        check(f"phone{'    fills' if want else '   skips'} {ctx!r}",
              fills(ctx, phone) == want)

    # --- phone format: national beside a code picker, international alone -----
    # The user's rule: when the form has its OWN country-code box, the number
    # field gets the number as dialled in France ("0612345678"), never the
    # trunk-stripped "612345678" nor a second "+33".
    me = "+33 6 12 34 56 78"
    check("split spaced", _split_phone(me) == ("33", "612345678"), _split_phone(me))
    check("split compact (not dial 337)", _split_phone("+33612345678") == ("33", "612345678"),
          _split_phone("+33612345678"))
    check("split 00-prefix", _split_phone("0033 6 12 34 56 78") == ("33", "612345678"))
    check("split 3-digit dial", _split_phone("+212 6 12 34 56 78") == ("212", "612345678"))
    check("national FR keeps trunk 0", _national_display(me) == "0612345678", _national_display(me))
    check("national UK keeps trunk 0", _national_display("+44 7700 900123") == "07700900123")
    check("national IT has no trunk 0", _national_display("+39 347 123 4567") == "3471234567")
    for raw, want in [("+33", "33"), ("France (+33)", "33"), ("FR +33", "33"), ("33", "33"),
                      ("France", "33"), ("United Kingdom (+44)", "44"), ("Select one", ""),
                      ("", ""), ("Ghana (+233)", "233")]:
        check(f"dial shown {raw!r} -> {want!r}", _dial_from_text(raw) == want, _dial_from_text(raw))
    for name, info, want in [
        ("separate picker already +33", {"hasDial": True, "dialVal": "+33"}, "0612345678"),
        ("separate picker France (+33)", {"hasDial": True, "dialVal": "France (+33)"}, "0612345678"),
        ("separate picker still blank", {"hasDial": True, "dialVal": "Select one"}, "0612345678"),
        ("separate picker on +1 -> wait", {"hasDial": True, "dialVal": "+1"}, None),
        ("intl-tel-input widget", {"iti": True}, me),
        ("lone field", {}, me),
        ("lone digits-only field", {"noPlus": True}, "0612345678"),
        ("lone field maxlength 12", {"maxLen": 12}, "+33612345678"),
        ("lone field maxlength 10", {"maxLen": 10}, "0612345678"),
    ]:
        got = _phone_value(me, info)
        check(f"phone value: {name}", got == want, got)

    print(f"\nTOTAL: {PASS}/{PASS + FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
