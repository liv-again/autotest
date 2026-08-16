import os, yaml
from tools.prereq.rules import load_rules
from tools.prereq.extract import extract
from tools.prereq.metrics import score

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")

def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return yaml.safe_load(f)

def test_spike_acceptance_thresholds():
    cases = _load("guojin_728_cases.yaml")["cases"]
    gold = _load("guojin_728_gold.yaml")["gold"]
    rules_doc = load_rules("tools/prereq_rules.yaml")
    req = extract(cases, rules_doc, app_slug="guojin")
    m = score(req, gold, rules_doc)
    assert m["recall"] >= 0.9, m                 # 召回优先不漏（含 1 条 paraphrase 漏识别，10/11≈0.909）
    assert m["polarity_accuracy"] == 1.0, m      # positive/negative_property 绝不混淆
    assert m["traceability"] == 1.0, m           # 完整性门:required_instruments 并集==命中规则属性并集
    # 误报率：召回优先，误报仅设宽松上界（TC-017 过触发样本被计为误报，1/11≈0.09）
    assert m["false_positive_rate"] <= 0.34, m
    # 未识别必须显式列出（不静默丢）：实质校验规则覆盖不到的用例确实进了列表
    assert "TC-099" in req["unidentified"], req["unidentified"]
    assert m["unidentified_count"] >= 1, m


# ── Fix B: traceability 完整性门(守 Fix A,静默丢即跌破 1.0) ──
_A = {"id": "a", "applies_to": {"app": "*", "market": "北交所"},
      "match": {"all": ["市价"]},
      "requires": {"instrument": {"market": "北交所", "product": "ETF", "orderbook_depth": True}},
      "polarity": "positive", "priority": 6, "confidence": "high", "provenance": "t"}
_B = {"id": "b", "applies_to": {"app": "*", "market": "北交所"},
      "match": {"all": ["IOPV"]},
      "requires": {"instrument": {"market": "北交所", "product": "ETF", "has_nav": True}},
      "polarity": "positive", "priority": 6, "confidence": "high", "provenance": "t"}
_RULES_DOC = {"version": 1, "rules": [_A, _B]}


def _req_with(required_instruments):
    return {"cases": [{
        "tc_id": "TC-D", "title": "市价 IOPV", "status": "identified",
        "matched_rule_ids": ["a", "b"], "polarity": "positive",
        "required_account": "any", "needed_codes": [],
        "required_instruments": required_instruments,
    }], "unidentified": [], "conflicts": [], "summary": {}}


def test_traceability_catches_silent_drop():
    # 命中 a+b 但 required_instruments 只留 a 的属性(丢 has_nav) → 门须跌破 1.0
    dropped = _req_with([{"market": "北交所", "product": "ETF", "orderbook_depth": True}])
    assert score(dropped, {}, _RULES_DOC)["traceability"] < 1.0


def test_traceability_full_union_passes():
    full = _req_with([{"market": "北交所", "product": "ETF",
                       "orderbook_depth": True, "has_nav": True}])
    assert score(full, {}, _RULES_DOC)["traceability"] == 1.0


def test_traceability_catches_phantom_attr():
    # 凭空多出 has_nav 之外的属性(命中规则并无) → 门须跌破 1.0(双向)
    phantom = _req_with([{"market": "北交所", "product": "ETF",
                          "orderbook_depth": True, "has_nav": True, "bogus": True}])
    assert score(phantom, {}, _RULES_DOC)["traceability"] < 1.0
