import pytest
import yaml

from tools.reback import upsert, reback_run
import tools.reback as reback_mod

def test_upsert_appends_new_key():
    doc = {"entries": [{"key": "a", "last_verified": "2026-07-01"}]}
    out = upsert(doc, "entries", {"key": "b", "last_verified": "2026-07-29"})
    assert len(out["entries"]) == 2

def test_upsert_updates_existing_no_dup():
    doc = {"entries": [{"key": "a", "last_verified": "2026-07-01", "status": "stale"}]}
    out = upsert(doc, "entries", {"key": "a", "last_verified": "2026-07-29", "status": "verified"})
    assert len(out["entries"]) == 1
    assert out["entries"][0]["last_verified"] == "2026-07-29"
    assert out["entries"][0]["status"] == "verified"

def test_upsert_by_code_key():
    doc = {"known_codes": [{"code": "950025", "name": "x"}]}
    out = upsert(doc, "known_codes", {"code": "950025", "attributes": {"t0": True}})
    assert len(out["known_codes"]) == 1 and out["known_codes"][0]["attributes"]["t0"]

def test_upsert_account_capabilities_matches_by_alias_not_first_item():
    """复审 repro：account_capabilities（Task 3 数据模型）用 alias 标识，既无 key 也无
    code。更新 xy 时绝不能误命中/污染第一条 pt 记录。"""
    doc = {
        "account_capabilities": [
            {"alias": "pt", "type": "普通", "capabilities": ["北交所交易"], "mask": "***5183"},
            {"alias": "xy", "type": "信用", "capabilities": ["两融"], "mask": "***2927"},
        ]
    }
    out = upsert(
        doc,
        "account_capabilities",
        {"alias": "xy", "capabilities": ["两融", "担保品持仓"]},
    )
    assert len(out["account_capabilities"]) == 2
    pt, xy = out["account_capabilities"]
    assert pt["alias"] == "pt" and pt["mask"] == "***5183" and pt["type"] == "普通"
    assert xy["alias"] == "xy"
    assert xy["capabilities"] == ["两融", "担保品持仓"]
    assert xy["mask"] == "***2927"  # 未被 pt 的 mask 污染
    assert xy["type"] == "信用"  # 未被 pt 的 type 污染

def test_upsert_unknown_section_without_key_or_code_raises():
    """entry 既无 key 也无 code，section 又未在 SECTION_ID_FIELD 声明——fail closed 抛错，
    不得静默按 item.get('code') == None 匹配第一条记录。"""
    doc = {"custom_section": [{"alias": "pt", "mask": "***5183"}]}
    with pytest.raises(ValueError):
        upsert(doc, "custom_section", {"alias": "xy", "mask": "***2927"})

def test_upsert_explicit_id_field_overrides_default():
    doc = {"custom_section": [{"alias": "pt", "mask": "***5183"}]}
    out = upsert(doc, "custom_section", {"alias": "pt", "mask": "***9999"}, id_field="alias")
    assert len(out["custom_section"]) == 1
    assert out["custom_section"][0]["mask"] == "***9999"

def test_upsert_missing_identifier_value_raises():
    doc = {"entries": [{"key": "a"}]}
    with pytest.raises(ValueError):
        upsert(doc, "entries", {"last_verified": "2026-07-29"})

def test_upsert_shallow_replaces_nested_attributes_caller_must_pass_full():
    """终审 Minor：upsert 用浅 update()，命中条目时 entry 的嵌套 dict（如 attributes）会整块
    替换而非深合并。本测试固化该契约——部分反哺 attributes 必须传全量，否则丢字段。"""
    doc = {"known_codes": [
        {"code": "950025", "name": "x",
         "attributes": {"market": "北交所", "has_nav": True, "financing_eligible": False}},
    ]}
    # 只带一个键的部分 attributes → 整块替换，market/financing_eligible 丢失（证明非深合并）
    upsert(doc, "known_codes", {"code": "950025", "attributes": {"has_nav": False}})
    attrs = doc["known_codes"][0]["attributes"]
    assert attrs == {"has_nav": False}
    assert "market" not in attrs and "financing_eligible" not in attrs

    # 正确用法：传全量 attributes，字段不丢
    upsert(doc, "known_codes", {"code": "950025", "attributes":
                                {"market": "北交所", "has_nav": False, "financing_eligible": False}})
    full = doc["known_codes"][0]["attributes"]
    assert full["market"] == "北交所" and full["has_nav"] is False and full["financing_eligible"] is False


# --- reback_run 端到端（monkeypatch tools.reback.validate，不依赖 Task 3 的
# profile.schema.json/prerequisites.schema.json —— 那两个 schema、以及
# apps/guojin/profile.yaml、prerequisites.yaml（Task 5/6）在本分支尚未落地，
# 一旦落地应补一轮用真实 schema/真实 yaml 的端到端回归。---

def _write(path, doc):
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")

def test_reback_run_merges_validates_and_writes(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    prereq_path = tmp_path / "prerequisites.yaml"
    _write(profile_path, {
        "slug": "guojin",
        "app_version": "8.05.001",
        "entries": [{"key": "trade.putong.buy", "last_verified": "2026-07-01", "status": "stale"}],
        "capabilities": [],
        "verified_chains": [],
    })
    _write(prereq_path, {
        "slug": "guojin",
        "account_capabilities": [
            {"alias": "pt", "type": "普通", "capabilities": ["北交所交易"], "mask": "***5183"},
        ],
        "instrument_properties": [],
        "known_codes": [{"code": "950025", "name": "old"}],
    })

    monkeypatch.setattr(reback_mod, "validate", lambda doc, schema_name: [])

    results = {
        "profile": {
            "entries": [{"key": "trade.putong.buy", "last_verified": "2026-07-29", "status": "verified"}],
        },
        "prerequisites": {
            "account_capabilities": [{"alias": "xy", "type": "信用", "capabilities": ["两融"], "mask": "***2927"}],
            "known_codes": [{"code": "950025", "name": "北证50ETF测试39"}],
        },
    }
    reback_run(profile_path, prereq_path, results)

    written_profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    written_prereq = yaml.safe_load(prereq_path.read_text(encoding="utf-8"))

    assert len(written_profile["entries"]) == 1
    assert written_profile["entries"][0]["status"] == "verified"
    assert written_profile["entries"][0]["last_verified"] == "2026-07-29"

    aliases = {a["alias"] for a in written_prereq["account_capabilities"]}
    assert aliases == {"pt", "xy"}
    pt = next(a for a in written_prereq["account_capabilities"] if a["alias"] == "pt")
    assert pt["mask"] == "***5183"  # 未被新增 xy 记录污染

    assert len(written_prereq["known_codes"]) == 1
    assert written_prereq["known_codes"][0]["name"] == "北证50ETF测试39"

def test_reback_run_validate_fails_blocks_write(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    prereq_path = tmp_path / "prerequisites.yaml"
    profile_doc = {"slug": "guojin", "entries": [{"key": "a", "last_verified": "2026-07-01"}]}
    prereq_doc = {"slug": "guojin", "known_codes": [{"code": "950025", "name": "old"}]}
    _write(profile_path, profile_doc)
    _write(prereq_path, prereq_doc)
    profile_before = profile_path.read_text(encoding="utf-8")
    prereq_before = prereq_path.read_text(encoding="utf-8")

    def fake_validate(doc, schema_name):
        return ["boom"] if schema_name == "prerequisites" else []

    monkeypatch.setattr(reback_mod, "validate", fake_validate)

    results = {
        "profile": {"entries": [{"key": "a", "last_verified": "2026-07-29"}]},
        "prerequisites": {"known_codes": [{"code": "950025", "name": "new"}]},
    }
    with pytest.raises(ValueError):
        reback_run(profile_path, prereq_path, results)

    # 校验失败 → 两个文件都不写，磁盘内容原封不动
    assert profile_path.read_text(encoding="utf-8") == profile_before
    assert prereq_path.read_text(encoding="utf-8") == prereq_before
