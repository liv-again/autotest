from tools.contracts.validate import load_and_validate
import pathlib
P = pathlib.Path(__file__).resolve().parents[2] / "apps/guojin/prerequisites.yaml"

def test_schema_valid():
    _, errs = load_and_validate(P, "prerequisites"); assert errs == []

def test_known_codes_present():
    doc, _ = load_and_validate(P, "prerequisites")
    codes = {c["code"] for c in doc["known_codes"]}
    assert {"950025", "950001", "950015"} <= codes

def test_accounts_masked_only(forbidden_full_accounts):
    txt = P.read_text(encoding="utf-8")
    for full in forbidden_full_accounts:
        assert full not in txt
    doc, _ = load_and_validate(P, "prerequisites")
    assert {a["alias"] for a in doc["account_capabilities"]} == {"pt", "xy"}
