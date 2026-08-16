"""apps/<app>/profile.yaml 与 prerequisites.yaml 的一致性 lint（spec §8.6）。
检查项：
①section 内**重复标识**（entries/capabilities/verified_chains 按 key；account_capabilities
  按 alias；instrument_properties/known_codes 按 code——标识字段单一真源复用
  tools.reback.SECTION_ID_FIELD，不在本文件另行声明，避免两处漂移）；
②**跨产物复制**（同一 key 同时出现在 entries 与 verified_chains，视为同一结构化信息被
  两处重复承载：entries 应只管单点入口，verified_chains 才管多步链路）；
③**stale**（profile 的 last_verified 早于 today 超 STALE_DAYS 天，但 status 未标 stale——
  应更新 status 或重新验证）；
④**md-yaml 漂移**（apps/<app>/{画像.md,前置条件.md,速览.md} 与 tools.derive_docs.derive
  的当前派生结果不一致，说明被手改或未重新派生）。
`today` 由调用方注入（YYYY-MM-DD），避免 Date.today() 类不确定性使 lint 不可复现。
"""
import datetime
import os
import pathlib
import sys

# 作为脚本直接运行(python tools/lint_profile.py)时 sys.path[0] 是 tools/，
# 注入仓根使 `import tools.*` 可用（与 prereq_extract.py 同模式）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import yaml  # noqa: E402

from tools.derive_docs import derive  # noqa: E402
from tools.reback import SECTION_ID_FIELD  # noqa: E402

STALE_DAYS = 30

# profile.yaml 的三个 section 既做重复标识、也做 stale 检查（都按 key 标识），合一个常量避免漂移。
PROFILE_SECTIONS = ("entries", "capabilities", "verified_chains")
PREREQ_SECTIONS = ("account_capabilities", "instrument_properties", "known_codes")


def _load_yaml(path):
    path = pathlib.Path(path)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _dup_issues(items, field, label):
    """section 内按 field 值找重复标识，field 缺失(None)的条目不计入（交给 schema 校验管）。"""
    seen = set()
    issues = []
    for item in items:
        ident = item.get(field)
        if ident is None:
            continue
        if ident in seen:
            issues.append(f"重复{field}: {label} 内 {field}={ident!r} 重复出现")
        else:
            seen.add(ident)
    return issues


def _stale_issues(items, label, today_d):
    """仅用于 PROFILE_SECTIONS（都按 key 标识）。last_verified 缺失/空/无法解析时不再静默
    continue——而是 emit 一条 issue 让脏数据可见（否则一条没日期的记录永远逃过 stale 检查）。"""
    issues = []
    for item in items:
        status = item.get("status")
        if status == "stale":
            continue  # 已如实标 stale，不重复告警
        ident = item.get("key")
        lv = item.get("last_verified")
        if not lv:
            issues.append(f"lint: {label}.{ident} last_verified 缺失/为空，无法判 stale（请补验证日期或标 stale）")
            continue
        try:
            lv_d = datetime.date.fromisoformat(str(lv))
        except ValueError:
            issues.append(f"lint: {label}.{ident} last_verified={lv!r} 无法解析（应为 YYYY-MM-DD）")
            continue
        if (today_d - lv_d).days > STALE_DAYS:
            issues.append(
                f"stale: {label}.{ident} last_verified={lv} 距今超 {STALE_DAYS} 天，"
                f"但 status={status!r} 未标 stale"
            )
    return issues


def _cross_artifact_issues(profile):
    entry_keys = {e.get("key") for e in profile.get("entries", []) if e.get("key")}
    chain_keys = {c.get("key") for c in profile.get("verified_chains", []) if c.get("key")}
    overlap = entry_keys & chain_keys
    return [
        f"跨产物复制: key={k!r} 同时出现在 entries 与 verified_chains（同一结构化信息重复承载）"
        for k in sorted(overlap)
    ]


def _drift_issues(app_dir):
    derived = derive(app_dir)
    issues = []
    for name, content in derived.items():
        disk_path = pathlib.Path(app_dir) / name
        if not disk_path.exists():
            issues.append(f"md-yaml漂移: {name} 不存在（应由 tools/derive_docs.py 派生）")
            continue
        if disk_path.read_text(encoding="utf-8") != content:
            issues.append(f"md-yaml漂移: {name} 与 yaml 当前派生结果不一致（疑似手改，重跑 derive_docs 覆盖）")
    return issues


def lint(app_dir, today):
    """检查 app_dir 下 profile.yaml/prerequisites.yaml/派生 md 的一致性，返回问题文案列表；
    空列表=干净。today 为 'YYYY-MM-DD' 字符串，由调用方注入。"""
    app_dir = pathlib.Path(app_dir)
    profile = _load_yaml(app_dir / "profile.yaml")
    prereq = _load_yaml(app_dir / "prerequisites.yaml")
    today_d = datetime.date.fromisoformat(today)

    issues = []
    for section in PROFILE_SECTIONS:
        items = profile.get(section, [])
        issues += _dup_issues(items, SECTION_ID_FIELD[section], f"profile.{section}")
        issues += _stale_issues(items, f"profile.{section}", today_d)
    for section in PREREQ_SECTIONS:
        items = prereq.get(section, [])
        issues += _dup_issues(items, SECTION_ID_FIELD[section], f"prerequisites.{section}")

    issues += _cross_artifact_issues(profile)
    issues += _drift_issues(app_dir)

    return issues


def main(app_dir, today=None):
    today = today or datetime.date.today().isoformat()
    issues = lint(app_dir, today)
    for i in issues:
        print(i)
    return 1 if issues else 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ".", sys.argv[2] if len(sys.argv) > 2 else None))
