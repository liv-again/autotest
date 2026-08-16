# tests/tools/test_init_app.py
"""init_app.py 骨架生成器测试（方案 §8.4 Phase A）。"""
import pytest
import yaml

from tools.contracts.validate import validate
from tools.init_app import (
    DEFAULT_COMPAT_MAX_EXCL,
    scaffold_docs,
    seed_entries,
    write_app,
    main,
)

TODAY = "2026-08-13"


def _seed_app(tmp_path):
    """造一个最小种子 app 目录：含 1 条 verified entry + 1 条 capability 的 profile.yaml、
    空 prerequisites.yaml。capability 用于断言"种子只抄 entries、不抄能力矩阵"。"""
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": "seedapp",
                "app_version": "1.0.0",
                "entries": [
                    {
                        "key": "trade.putong.buy",
                        "path": "交易→买入；代码auto_stockcode",
                        "last_verified": "2026-07-29",
                        "app_version": "1.0.0",
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
                "verified_chains": [],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (seed / "prerequisites.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": "seedapp",
                "account_capabilities": [],
                "instrument_properties": [],
                "known_codes": [],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return seed


def _docs():
    return scaffold_docs("newapp", "com.example.app", "9.02.10", verified_at=TODAY)


def test_scaffold_docs_all_pass_schema():
    docs = _docs()
    assert validate(docs["app.yaml"], "app") == []
    assert validate(docs["profile.yaml"], "profile") == []
    assert validate(docs["prerequisites.yaml"], "prerequisites") == []


def test_scaffold_defaults():
    docs = _docs()
    app = docs["app.yaml"]
    assert app["slug"] == "newapp"
    assert app["packages"] == ["com.example.app"]
    assert app["verified_versions"] == [{"version": "9.02.10", "verified_at": TODAY}]
    assert app["compatibility"] == {"min": "9.02.10", "max_exclusive": DEFAULT_COMPAT_MAX_EXCL}
    assert app["test_accounts"] == []  # 脱敏账户信息交人工补录
    assert docs["profile.yaml"]["entries"] == []
    assert docs["profile.yaml"]["capabilities"] == []
    assert docs["prerequisites.yaml"]["known_codes"] == []


def test_scaffold_accepts_aliases_and_compat_overrides():
    docs = scaffold_docs(
        "newapp", "com.example.app", "9.02.10", verified_at=TODAY,
        aliases=["新App", "example"], compat_min="9.00.000", compat_max_excl="9.10.000",
    )
    app = docs["app.yaml"]
    assert app["aliases"] == ["新App", "example"]
    assert app["compatibility"] == {"min": "9.00.000", "max_exclusive": "9.10.000"}


def test_write_app_writes_all_files_and_derives_md(tmp_path):
    from tools.lint_profile import lint
    docs = _docs()
    app_dir = tmp_path / "newapp"
    written = write_app(app_dir, docs)
    assert (app_dir / "app.yaml").exists()
    assert (app_dir / "profile.yaml").exists()
    assert (app_dir / "prerequisites.yaml").exists()
    assert (app_dir / "画像.md").exists()
    assert (app_dir / "前置条件.md").exists()
    assert (app_dir / "速览.md").exists()
    assert lint(app_dir, TODAY) == []  # 派生 md 齐全后无漂移/无 stale


def test_write_app_does_not_generate_env_yaml(tmp_path):
    # P0 红线：环境认证永不自动生成。
    docs = _docs()
    app_dir = tmp_path / "newapp"
    write_app(app_dir, docs)
    assert not (app_dir / "env.yaml").exists()


def test_write_app_fail_closed_on_invalid_docs(tmp_path):
    # 任一文档不过 schema 则整体不写盘（与 reback_run 同哲学）。
    docs = _docs()
    docs["profile.yaml"]["entries"] = [{"key": "x"}]  # 缺必填字段，schema 必拒
    app_dir = tmp_path / "newapp"
    with pytest.raises(ValueError, match="schema 校验失败"):
        write_app(app_dir, docs)
    assert not app_dir.exists()  # 目录都没建，更无文件


def test_seed_entries_marks_unverified_and_renames_evidence(tmp_path):
    seed = _seed_app(tmp_path)
    docs = _docs()
    seed_entries(docs, seed)
    entries = docs["profile.yaml"]["entries"]
    assert len(entries) == 1
    e = entries[0]
    assert e["key"] == "trade.putong.buy"
    assert e["path"] == "交易→买入；代码auto_stockcode"
    assert e["status"] == "unverified"
    assert e["evidence_run"] == "seed-from-seedapp"
    assert e["last_verified"] == "2026-07-29"  # 保留种子原值
    assert e["app_version"] == "1.0.0"


def test_seed_entries_does_not_copy_capabilities_or_chains(tmp_path):
    # 业务判断不跨券商抄；链路需新 App 实测。
    seed = _seed_app(tmp_path)
    docs = _docs()
    seed_entries(docs, seed)
    assert docs["profile.yaml"]["capabilities"] == []
    assert docs["profile.yaml"]["verified_chains"] == []


def test_seed_entries_skips_dirty_entries_without_key_or_path(tmp_path):
    seed = _seed_app(tmp_path)
    (seed / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": "seedapp",
                "app_version": "1.0.0",
                "entries": [
                    {"key": "ok.entry", "path": "首页→OK", "status": "verified"},
                    {"key": "no.path"},            # 缺 path，跳过
                    {"path": "no.key"},            # 缺 key，跳过
                ],
                "capabilities": [],
                "verified_chains": [],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    docs = _docs()
    seed_entries(docs, seed)
    assert [e["key"] for e in docs["profile.yaml"]["entries"]] == ["ok.entry"]


def test_main_rejects_invalid_slug(capsys):
    # 非法 slug（含路径分隔符）必须被 CLI 拒绝——防路径穿越。
    with pytest.raises(SystemExit):
        main(["Bad/Slug!", "--package", "com.example.app", "--version", "1.0.0"])
    out = capsys.readouterr()
    assert "slug 非法" in out.err
