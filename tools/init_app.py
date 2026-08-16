"""新 App 骨架初始化（spec §8.6 / D6 → 方案 §8.4 Phase A）：为 apps/<slug> 生成过 schema 的
app.yaml / profile.yaml（空三节）/ prerequisites.yaml（空三节）骨架，可选 --seed-from 从白标
种子（如 apps/guojin）复制 entries 作为机器候选（全标 status: unverified）。

设计要点（对应 §8 方案红线与接口盘点）：
- **写盘前三个文档全部过 schema 校验，任一失败不写任何文件**（fail closed，与 reback_run
  同哲学——§七-8 证明手写绕过 schema 门即翻车）；
- **不生成 env.yaml**——环境认证永远人工（P0：执行程序不得自我认证，见 §8.6 红线 1）；
- **--seed-from 只复制 entries**（导航路径，同花顺白标高度相似），**不复制 capabilities**
  （业务判断"是缺陷还是设计"随券商而异，机器无权跨券商抄）与 **verified_chains**
  （需在新 App 实测走安全门）；
- 写盘后顺带派生 画像.md/前置条件.md/速览.md，否则 lint_profile 会报 md-yaml 漂移；
- seed 条目的 last_verified/app_version 保留种子原值（语义：路径按种子版本抄来、未在本次
  接入 App 上验证），evidence_run 改写为 seed-from-<seed_slug> 且 status 改 unverified。
"""
import argparse
import datetime
import os
import pathlib
import re
import sys

# 作为脚本直接运行(python tools/init_app.py)时 sys.path[0] 是 tools/，
# 注入仓根使 `import tools.*` 可用（与 prereq_extract.py 同模式）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import yaml  # noqa: E402

from tools.contracts.validate import validate  # noqa: E402
from tools.derive_docs import main as derive_main  # noqa: E402

# slug 用作目录名 apps/<slug>/，收紧格式同时防路径穿越（不信任任意输入）。
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# 新 App 的兼容区间上限无历史可推，用占位值并提示人工收紧（min 默认 = 接入版本）。
DEFAULT_COMPAT_MAX_EXCL = "999.999.999"
SEED_EVIDENCE_PREFIX = "seed-from-"
DERIVED_MD = ("画像.md", "前置条件.md", "速览.md")


def _yaml_dump(doc):
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def scaffold_docs(slug, package, version, verified_at=None, aliases=None,
                  compat_min=None, compat_max_excl=None):
    """构造三个骨架文档（内存 dict，未写盘）。

    verified_at / compat_* 可注入以便测试确定性；默认 verified_at=今天、
    compat_max_excl=占位值（DEFAULT_COMPAT_MAX_EXCL，需人工收紧）。
    test_accounts 留空数组——账户信息属脱敏敏感数据，交人工补录。
    """
    verified_at = verified_at or datetime.date.today().isoformat()
    app_doc = {
        "slug": slug,
        "packages": [package],
        "verified_versions": [{"version": version, "verified_at": verified_at}],
        "compatibility": {
            "min": compat_min or version,
            "max_exclusive": compat_max_excl or DEFAULT_COMPAT_MAX_EXCL,
        },
        "test_accounts": [],
    }
    if aliases:
        app_doc["aliases"] = aliases
    profile_doc = {
        "slug": slug,
        "app_version": version,
        "entries": [],
        "capabilities": [],
        "verified_chains": [],
    }
    prereq_doc = {
        "slug": slug,
        "account_capabilities": [],
        "instrument_properties": [],
        "known_codes": [],
    }
    return {"app.yaml": app_doc, "profile.yaml": profile_doc, "prerequisites.yaml": prereq_doc}


def seed_entries(docs, seed_app_dir):
    """把种子 app 的 profile.yaml entries 复制为机器候选（§8.5.2 白标复制）。

    每条种子 entry 复制 key/path，last_verified/app_version 保留种子原值（表示"路径按种子
    版本验证过、但未在本次接入 App 上验证"），evidence_run 改写为 seed-from-<seed_slug>、
    status 强制 unverified。种子条目缺 key/path 的跳过（脏种子由 lint 兜底暴露）。
    只动 entries，capabilities/verified_chains 保持空骨架。
    """
    seed_path = pathlib.Path(seed_app_dir) / "profile.yaml"
    with open(seed_path, encoding="utf-8") as f:
        seed_profile = yaml.safe_load(f) or {}
    seed_slug = seed_profile.get("slug") or pathlib.Path(seed_app_dir).name
    docs["profile.yaml"]["entries"] = [
        {
            "key": e["key"],
            "path": e["path"],
            "last_verified": e.get("last_verified", ""),
            "app_version": e.get("app_version", ""),
            "evidence_run": f"{SEED_EVIDENCE_PREFIX}{seed_slug}",
            "status": "unverified",
        }
        for e in seed_profile.get("entries", [])
        if isinstance(e, dict) and e.get("key") and e.get("path")
    ]
    return docs


def _validate_all(docs):
    """三个文档分别过对应 schema；返回 {文件名: 错误列表}（全空 = 通过）。"""
    problems = {}
    for name, schema_name in (("app.yaml", "app"),
                              ("profile.yaml", "profile"),
                              ("prerequisites.yaml", "prerequisites")):
        errs = validate(docs[name], schema_name)
        if errs:
            problems[name] = errs
    return problems


def write_app(app_dir, docs, derive=True):
    """校验全部通过后写盘；任一 schema 失败抛 ValueError 且不建目录不写任何文件（fail closed）。

    derive=True 时顺带派生三份 md（否则 lint_profile 会报 md-yaml 漂移）。返回写盘文件列表。
    """
    app_dir = pathlib.Path(app_dir)
    problems = _validate_all(docs)
    if problems:
        raise ValueError(f"init_app: schema 校验失败，未写盘：{problems}")
    app_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, doc in docs.items():
        (app_dir / name).write_text(_yaml_dump(doc), encoding="utf-8")
        written.append(app_dir / name)
    if derive:
        derive_main(app_dir)
        written.extend(app_dir / name for name in DERIVED_MD)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="初始化新 App 骨架：生成过 schema 的 app.yaml / profile.yaml / "
        + "prerequisites.yaml（+派生 md）。不生成 env.yaml——环境认证必须人工。",
    )
    parser.add_argument("slug", help="App 标识（小写字母数字连字符，将作为目录名 apps/<slug>/）")
    parser.add_argument("--package", required=True, help="Android 包名，如 com.example.app")
    parser.add_argument("--version", required=True, help="当前接入的 App 版本，如 9.02.10")
    parser.add_argument("--aliases", help="别名列表，逗号分隔（可选）")
    parser.add_argument("--compat-min", help="兼容区间下限（默认 = --version）")
    parser.add_argument("--compat-max-excl",
                        help=f"兼容区间上限（开区间；默认占位 {DEFAULT_COMPAT_MAX_EXCL}，需人工收紧）")
    parser.add_argument("--app-dir", help="输出目录（默认 apps/<slug>）")
    parser.add_argument("--seed-from", help="白标种子 app 目录（如 apps/guojin），复制其 entries 为机器候选")
    parser.add_argument("--verified-at", help="验证日期 YYYY-MM-DD（默认今天；测试注入用）")
    parser.add_argument("--no-derive", action="store_true", help="不派生 md（默认派生）")
    args = parser.parse_args(argv)

    if not SLUG_RE.match(args.slug):
        parser.error(f"slug 非法：{args.slug!r}（应匹配 {SLUG_RE.pattern}）")

    aliases = [a.strip() for a in args.aliases.split(",")] if args.aliases else None
    docs = scaffold_docs(
        args.slug,
        args.package,
        args.version,
        verified_at=args.verified_at,
        aliases=aliases,
        compat_min=args.compat_min,
        compat_max_excl=args.compat_max_excl,
    )
    if args.seed_from:
        seed_entries(docs, args.seed_from)

    app_dir = args.app_dir or f"apps/{args.slug}"
    written = write_app(app_dir, docs, derive=not args.no_derive)

    print(f"已生成骨架：{app_dir}")
    for p in written:
        print(f"  - {p}")
    if args.seed_from:
        seeded = len(docs["profile.yaml"]["entries"])
        print(f"  从种子复制 {seeded} 条 entries（status: unverified，待探索/实测确认）")
    print("提示：env.yaml 需人工创建（认证永不自动生成，见 .claude/skills/app-selftest/references/safety-policy.md）")


if __name__ == "__main__":
    main()
