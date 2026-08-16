from tools.prereq.rules import load_rules
from tools.prereq.extract import match_rule, extract

RULES = load_rules("tools/prereq_rules.yaml")

def test_match_all_and_none():
    idx = {r["id"]: r for r in RULES["rules"]}
    buy = {"title": "北交所ETF 限价买入"}
    assert match_rule(buy, idx["basic-bjse-etf"]) is True
    rz = {"title": "融资买入 北交所ETF"}
    assert match_rule(rz, idx["basic-bjse-etf"]) is False  # none 含"融资"

def test_extract_single_hit_basic():
    cases = [{"tc_id": "TC-001", "title": "北交所ETF 限价买入"}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert c["status"] == "identified"
    assert c["matched_rule_ids"] == ["basic-bjse-etf"]
    assert c["polarity"] == "no_prereq"
    assert c["required_account"] == "any"

def test_extract_sell_needs_holding():
    cases = [{"tc_id": "TC-003", "title": "北交所ETF 限价卖出"}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert "sell-needs-holding" in c["matched_rule_ids"]
    assert c["required_instruments"][0].get("has_holding") is True

def test_extract_financing_positive():
    cases = [{"tc_id": "TC-010", "title": "融资买入 北交所ETF", "keywords": ["融资标的"]}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert c["polarity"] == "positive"
    assert c["required_account"] == "信用"
    assert c["required_instruments"][0].get("financing_eligible") is True

def test_extract_negative_property_carries_expected():
    cases = [{"tc_id": "TC-011", "title": "非融资标的 不可融资买入"}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert c["polarity"] == "negative_property"
    assert c["required_instruments"][0].get("financing_eligible") is False
    assert c.get("expected_capability", {}).get("financing_buy") == "rejected"

def test_extract_traceability_all_ids():
    cases = [{"tc_id": "TC-012", "title": "担保品买入 北交所ETF"}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert "rz-collateral" in c["matched_rule_ids"]
    assert c["status"] == "identified"

def test_extract_conflict_flagged():
    # 构造两条并列最高优先级、互斥的规则
    doc = {"version": 1, "rules": [
        {"id": "p-true", "applies_to": {"app": "*", "market": "北交所"},
         "match": {"all": ["X"]}, "requires": {"instrument": {"financing_eligible": True}},
         "polarity": "positive", "priority": 10, "confidence": "high", "provenance": "t"},
        {"id": "p-false", "applies_to": {"app": "*", "market": "北交所"},
         "match": {"all": ["X"]}, "requires": {"instrument": {"financing_eligible": False}},
         "polarity": "negative_property", "priority": 10, "confidence": "high", "provenance": "t"},
    ]}
    req = extract([{"tc_id": "TC-050", "title": "X 用例"}], doc, app_slug="guojin")
    assert req["cases"][0]["status"] == "conflict"
    assert req["conflicts"] and req["conflicts"][0]["tc_id"] == "TC-050"
    assert req["summary"]["conflict"] == 1

def test_extract_unidentified_listed():
    req = extract([{"tc_id": "TC-099", "title": "完全不相关的东西"}], RULES, app_slug="guojin")
    assert req["cases"][0]["status"] == "unidentified"
    assert "TC-099" in req["unidentified"]


def _union_attrs(case):
    attrs = {}
    for inst in case["required_instruments"]:
        attrs.update(inst)
    return attrs


def test_extract_multi_rule_union_market_and_iopv():
    # 命中 depth+nav+basic:required_instruments 须同时含 orderbook_depth 与 has_nav(不静默丢)
    cases = [{"tc_id": "TC-U1", "title": "北交所ETF 市价买入 IOPV 字段"}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert {"basic-bjse-etf", "market-order-needs-depth", "iopv-needs-nav"} <= set(c["matched_rule_ids"])
    attrs = _union_attrs(c)
    assert attrs.get("orderbook_depth") is True
    assert attrs.get("has_nav") is True
    assert c["status"] == "identified"   # 属性键不重叠→合并,非冲突


def test_extract_multi_rule_union_collateral_sell():
    # 担保品卖出:required_instruments 须同时含 has_holding 与 collateral_eligible
    cases = [{"tc_id": "TC-U2", "title": "担保品卖出 北交所ETF"}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert {"sell-needs-holding", "rz-collateral"} <= set(c["matched_rule_ids"])
    attrs = _union_attrs(c)
    assert attrs.get("has_holding") is True
    assert attrs.get("collateral_eligible") is True
    assert c["status"] == "identified"


def test_extract_conflict_non_tied_priority():
    # 同键取值相反、优先级不并列的两规则也须记冲突(union 级冲突,非仅并列最高)
    doc = {"version": 1, "rules": [
        {"id": "hi", "applies_to": {"app": "*", "market": "北交所"},
         "match": {"all": ["Y"]}, "requires": {"instrument": {"financing_eligible": True}},
         "polarity": "positive", "priority": 12, "confidence": "high", "provenance": "t"},
        {"id": "lo", "applies_to": {"app": "*", "market": "北交所"},
         "match": {"all": ["Y"]}, "requires": {"instrument": {"financing_eligible": False}},
         "polarity": "negative_property", "priority": 3, "confidence": "high", "provenance": "t"},
    ]}
    req = extract([{"tc_id": "TC-060", "title": "Y 用例"}], doc, app_slug="guojin")
    c = req["cases"][0]
    assert c["status"] == "conflict"
    assert req["summary"]["conflict"] == 1
    assert req["conflicts"][0]["tc_id"] == "TC-060"
    assert "financing_eligible" in req["conflicts"][0]["note"]
