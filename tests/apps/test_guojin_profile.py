from tools.contracts.validate import load_and_validate
import pathlib
P = pathlib.Path(__file__).resolve().parents[2] / "apps/guojin/profile.yaml"

def test_profile_schema_valid():
    doc, errs = load_and_validate(P, "profile")
    assert errs == []

def test_profile_has_core_entries():
    doc, _ = load_and_validate(P, "profile")
    keys = {e["key"] for e in doc["entries"]}
    assert {"trade.putong.buy", "trade.rzrq", "quote.detail"} <= keys

def test_profile_keys_unique():
    doc, _ = load_and_validate(P, "profile")
    for section in ("entries", "capabilities", "verified_chains"):
        ks = [x["key"] for x in doc[section]]
        assert len(ks) == len(set(ks)), section

def test_profile_masked(forbidden_full_accounts):
    txt = P.read_text(encoding="utf-8")
    # 忠实转写但主动脱敏：股东/资金全号、登录号一律不得出现完整值
    for full in forbidden_full_accounts:
        assert full not in txt
