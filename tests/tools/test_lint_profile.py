# tests/tools/test_lint_profile.py
import yaml

from tools.derive_docs import main as derive_main
from tools.lint_profile import lint

TODAY = "2026-07-30"


def _base_profile():
    return {
        "slug": "guojin",
        "app_version": "8.05.001",
        "entries": [
            {
                "key": "trade.putong.buy",
                "path": "交易→买入",
                "last_verified": "2026-07-29",
                "app_version": "8.05.001",
                "evidence_run": "r1",
                "status": "verified",
            },
        ],
        "capabilities": [
            {
                "key": "cap.putong.limit_trade",
                "supported": True,
                "note": "限价买卖",
                "last_verified": "2026-07-29",
                "status": "verified",
            },
        ],
        "verified_chains": [
            {
                "key": "chain.putong.buy_cancel",
                "steps": ["买入", "撤单"],
                "last_verified": "2026-07-29",
                "evidence_run": "r1",
                "status": "verified",
            },
        ],
    }


def _base_prereq():
    return {
        "slug": "guojin",
        "account_capabilities": [
            {"alias": "pt", "type": "普通", "capabilities": ["北交所交易"], "mask": "***5183"},
        ],
        "instrument_properties": [
            {"code": "950025", "name": "北证50ETF测试39", "props": ["t0"]},
        ],
        "known_codes": [
            {
                "code": "950025",
                "name": "北证50ETF测试39",
                "market": "北交所",
                "attributes": {"t0": True},
            },
        ],
    }


def _write_app(tmp_path, profile, prereq):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "profile.yaml").write_text(
        yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (app_dir / "prerequisites.yaml").write_text(
        yaml.safe_dump(prereq, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    derive_main(app_dir)  # 生成与 yaml 一致的派生 md，避免其它断言被漂移检查误伤
    return app_dir


def test_clean_app_dir_returns_empty(tmp_path):
    app_dir = _write_app(tmp_path, _base_profile(), _base_prereq())
    assert lint(app_dir, TODAY) == []


def test_duplicate_key_within_profile_section_caught(tmp_path):
    profile = _base_profile()
    profile["entries"].append(dict(profile["entries"][0]))  # 植入重复 key
    app_dir = _write_app(tmp_path, profile, _base_prereq())
    issues = lint(app_dir, TODAY)
    assert any("trade.putong.buy" in i and "重复" in i for i in issues)


def test_duplicate_code_within_prerequisites_section_caught(tmp_path):
    prereq = _base_prereq()
    prereq["known_codes"].append(dict(prereq["known_codes"][0]))  # 植入重复 code
    app_dir = _write_app(tmp_path, _base_profile(), prereq)
    issues = lint(app_dir, TODAY)
    assert any("950025" in i and "重复" in i for i in issues)


def test_cross_artifact_duplication_between_entries_and_chains_caught(tmp_path):
    profile = _base_profile()
    profile["verified_chains"].append(
        {
            "key": "trade.putong.buy",  # 与 entries 同 key：同一结构化信息跨产物重复承载
            "steps": ["买入"],
            "last_verified": "2026-07-29",
            "evidence_run": "r1",
            "status": "verified",
        }
    )
    app_dir = _write_app(tmp_path, profile, _base_prereq())
    issues = lint(app_dir, TODAY)
    assert any("trade.putong.buy" in i and "跨产物" in i for i in issues)


def test_stale_entry_not_marked_stale_caught(tmp_path):
    profile = _base_profile()
    profile["entries"][0]["last_verified"] = "2020-01-01"
    profile["entries"][0]["status"] = "verified"  # 早过期但未标 stale
    app_dir = _write_app(tmp_path, profile, _base_prereq())
    issues = lint(app_dir, TODAY)
    assert any("trade.putong.buy" in i and "stale" in i for i in issues)


def test_stale_entry_already_marked_stale_not_flagged(tmp_path):
    profile = _base_profile()
    profile["entries"][0]["last_verified"] = "2020-01-01"
    profile["entries"][0]["status"] = "stale"  # 已如实标 stale，不应重复告警
    app_dir = _write_app(tmp_path, profile, _base_prereq())
    issues = lint(app_dir, TODAY)
    assert not any("trade.putong.buy" in i and "stale" in i for i in issues)


def test_missing_last_verified_emitted_not_silent(tmp_path):
    # 终审 Minor：last_verified 缺失/空不得静默 continue，应 emit 一条可见 issue。
    profile = _base_profile()
    profile["entries"][0]["last_verified"] = ""  # 空值
    app_dir = _write_app(tmp_path, profile, _base_prereq())
    issues = lint(app_dir, TODAY)
    assert any("trade.putong.buy" in i and "last_verified" in i for i in issues)


def test_unparseable_last_verified_emitted_not_silent(tmp_path):
    # 终审 Minor：last_verified 无法解析(ValueError)不得静默 continue，应 emit。
    profile = _base_profile()
    profile["entries"][0]["last_verified"] = "not-a-date"
    app_dir = _write_app(tmp_path, profile, _base_prereq())
    issues = lint(app_dir, TODAY)
    assert any("trade.putong.buy" in i and "last_verified" in i for i in issues)


def test_duplicate_alias_in_account_capabilities_caught(tmp_path):
    # account_capabilities 按 alias 去重（无 key/code）。
    prereq = _base_prereq()
    prereq["account_capabilities"].append(dict(prereq["account_capabilities"][0]))  # 重复 alias
    app_dir = _write_app(tmp_path, _base_profile(), prereq)
    issues = lint(app_dir, TODAY)
    assert any("pt" in i and "重复" in i for i in issues)


def test_duplicate_code_in_instrument_properties_caught(tmp_path):
    # instrument_properties 按 code 去重。
    prereq = _base_prereq()
    prereq["instrument_properties"].append(dict(prereq["instrument_properties"][0]))  # 重复 code
    app_dir = _write_app(tmp_path, _base_profile(), prereq)
    issues = lint(app_dir, TODAY)
    assert any("950025" in i and "重复" in i for i in issues)


def test_md_drift_from_manual_edit_caught(tmp_path):
    app_dir = _write_app(tmp_path, _base_profile(), _base_prereq())
    (app_dir / "画像.md").write_text("手改内容，不是脚本派生的", encoding="utf-8")
    issues = lint(app_dir, TODAY)
    assert any("画像.md" in i and "漂移" in i for i in issues)


def test_missing_derived_md_caught(tmp_path):
    app_dir = _write_app(tmp_path, _base_profile(), _base_prereq())
    (app_dir / "速览.md").unlink()
    issues = lint(app_dir, TODAY)
    assert any("速览.md" in i and "漂移" in i for i in issues)
