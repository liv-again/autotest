import os
from tools.contracts.validate import load_and_validate

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")

def test_prereq_rules_valid_passes():
    _, errs = load_and_validate(os.path.join(FIX, "prereq_rules_valid.yaml"), "prereq_rules")
    assert errs == []

def test_prereq_rules_invalid_reports():
    # 缺 polarity + bad confidence enum
    _, errs = load_and_validate(os.path.join(FIX, "prereq_rules_invalid.yaml"), "prereq_rules")
    assert errs

def test_shipped_rules_file_valid():
    _, errs = load_and_validate("tools/prereq_rules.yaml", "prereq_rules")
    assert errs == []

def test_prereq_rules_empty_match_rejected(tmp_path):
    # match:{} 会命中所有用例(footgun) → schema 须 fail-closed 拒绝
    p = tmp_path / "empty_match.yaml"
    p.write_text(
        "version: 1\nrules:\n"
        "  - {id: em, applies_to: {app: '*', market: 北交所}, match: {}, "
        "requires: {account: any}, polarity: no_prereq, priority: 1, "
        "confidence: high, provenance: t}\n",
        encoding="utf-8")
    _, errs = load_and_validate(str(p), "prereq_rules")
    assert errs

def test_prereq_rules_none_only_match_allowed(tmp_path):
    # 仅 none 非空(all/any 空)是合法的:排除式规则,不属全空 footgun
    p = tmp_path / "none_only.yaml"
    p.write_text(
        "version: 1\nrules:\n"
        "  - {id: only-none, applies_to: {app: '*', market: 北交所}, "
        "match: {all: [], any: [], none: [大宗]}, "
        "requires: {account: any}, polarity: no_prereq, priority: 1, "
        "confidence: high, provenance: t}\n",
        encoding="utf-8")
    _, errs = load_and_validate(str(p), "prereq_rules")
    assert errs == []

def test_prereq_request_valid_passes():
    _, errs = load_and_validate(os.path.join(FIX, "prereq_request_valid.yaml"), "prereq_request")
    assert errs == []

def test_prereq_request_invalid_reports():
    # cases[0].status = "maybe"(非枚举)
    _, errs = load_and_validate(os.path.join(FIX, "prereq_request_invalid.yaml"), "prereq_request")
    assert errs

import pytest
from tools.prereq.rules import load_rules, index_rules, rules_for

def test_load_rules_ok():
    doc = load_rules("tools/prereq_rules.yaml")
    assert doc["version"] >= 1 and len(doc["rules"]) >= 9

def test_load_rules_duplicate_id_raises(tmp_path):
    p = tmp_path / "dup.yaml"
    p.write_text(
        "version: 1\nrules:\n"
        "  - {id: x, applies_to: {app: '*', market: 北交所}, match: {all: [a]}, requires: {account: any}, polarity: no_prereq, priority: 1, confidence: high, provenance: t}\n"
        "  - {id: x, applies_to: {app: '*', market: 北交所}, match: {all: [b]}, requires: {account: any}, polarity: no_prereq, priority: 1, confidence: high, provenance: t}\n",
        encoding="utf-8")
    with pytest.raises(ValueError):
        load_rules(str(p))

def test_rules_for_filters_market_and_app():
    doc = load_rules("tools/prereq_rules.yaml")
    got = rules_for(doc, "guojin", "北交所")
    assert got and all(r["applies_to"]["market"] == "北交所" for r in got)
    assert index_rules(doc)["basic-bjse-etf"]["polarity"] == "no_prereq"
