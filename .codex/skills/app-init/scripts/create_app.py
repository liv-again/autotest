"""Create a new app from the app-init skill's standard prerequisite template.

The script is deliberately separate from ``tools/init_app.py``: the repository
initializer keeps its historical blank-skeleton default, while this skill opts
into the reusable standard ``known_codes`` set.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import pathlib
import re
import sys
from typing import Any

import yaml


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_TEMPLATE = SKILL_DIR / "config" / "standard_known_codes.yaml"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
REMINDER_NAME = "待补充.md"
ENV_EXAMPLE_NAME = "env.yaml.example"


def _repo_root() -> pathlib.Path:
    # scripts/create_app.py -> app-init -> skills -> .codex -> repository root
    return SCRIPT_DIR.parents[3]


def load_standard_known_codes(template_path: pathlib.Path | str = DEFAULT_TEMPLATE) -> list[dict[str, Any]]:
    """Load and validate the skill-owned standard known-code configuration."""
    template_path = pathlib.Path(template_path)
    with template_path.open(encoding="utf-8") as f:
        document = yaml.safe_load(f) or {}
    if not isinstance(document, dict) or not isinstance(document.get("known_codes"), list):
        raise ValueError(f"标准 known_codes 配置格式错误：{template_path}")

    codes = copy.deepcopy(document["known_codes"])
    for index, item in enumerate(codes):
        if not isinstance(item, dict):
            raise ValueError(f"标准 known_codes 第 {index + 1} 条不是对象")
        missing = [key for key in ("code", "name", "market", "attributes") if key not in item]
        if missing or not isinstance(item.get("attributes"), dict):
            detail = ", ".join(missing) or "attributes 必须是对象"
            raise ValueError(f"标准 known_codes 第 {index + 1} 条字段错误：{detail}")
    return codes


def _resolve_path(path: str | pathlib.Path, repo_root: pathlib.Path) -> pathlib.Path:
    candidate = pathlib.Path(path)
    return candidate if candidate.is_absolute() else repo_root / candidate


def _assert_new_or_empty(app_dir: pathlib.Path) -> None:
    """Protect existing app data; an already-created empty directory is fine."""
    if app_dir.exists() and any(app_dir.iterdir()):
        raise FileExistsError(
            f"目标 App 目录非空，为保护已有内容已停止，未覆盖任何文件：{app_dir}"
        )


def render_reminder(slug: str, package: str, version: str, code_count: int) -> str:
    today = datetime.date.today().isoformat()
    return f"""<!-- 由 .codex/skills/app-init/scripts/create_app.py 生成；请逐项确认 -->

# {slug} · 新 App 待补充事项

初始化时间：{today}
包名：`{package}`
版本：`{version}`
已写入标准 `known_codes`：{code_count} 条（仅测试候选，尚未证明在本 App 可用）

## 必须人工补充或确认

- [ ] 以 `env.yaml.example` 为起点创建并人工认证 `env.yaml`；确认包名、版本范围、账户别名后才解除模板中的 `revoked: true`。不要把完整账号、密码、密钥或签名写入产物。
- [ ] 在 `app.yaml` 的 `test_accounts` 中补充脱敏的账户别名、类型和尾号，例如 `mask: "***1234"`；不要填写完整账号。
- [ ] 将 `app.yaml` 的 `compatibility.max_exclusive` 从占位值 `999.999.999` 收紧到实际兼容上限。
- [ ] 在目标 App 中确认标准 `known_codes` 是否存在、名称和属性是否正确；删除不适用代码，补充该 App 专属代码。
- [ ] 按实际测试需求补齐 `prerequisites.yaml` 的 `instrument_properties` 和 `account_capabilities`。
- [ ] 探索并补齐 `profile.yaml` 的入口、能力矩阵和已验证链路；新写入内容先保持 `unverified`，实测后再标记验证状态。
- [ ] 修改 YAML 后运行 `python tools/derive_docs.py apps/{slug}`，再运行 `python tools/lint_profile.py apps/{slug}`。

## 安全边界

标准 `known_codes` 是跨 App 的测试数据起点，不是当前 App 的验证结论。首次探索和交易相关测试应按项目安全策略执行，未完成人工认证时保持 `confirm_only`。
"""


def render_env_example(package: str, version: str) -> str:
    """Render a fail-closed trusted-internal simulation environment template."""
    return f"""# 由 .codex/skills/app-init/scripts/create_app.py 生成。
# 这是 env.yaml.example 模板，不会被执行流读取；不要把它直接当作已认证 env.yaml。
#
# 使用前请人工确认：包名、兼容版本范围、测试账户别名和模拟盘环境。
# 默认 revoked: true，误复制/未审查时会安全回退到 confirm_only。
# 仅团队确认的模拟盘可使用 trusted_internal；真实账户/生产环境必须走
# operator_attested/technical_verified 严格认证和项目安全流程。
type: simulation
assurance_level: trusted_internal
evidence:
  package: {package}
  version_range:
    min: "{version}"
    max_exclusive: "999.999.999"  # TODO: 收紧到实际兼容上限
  account_aliases: []              # TODO: 填入脱敏账户别名，如 [pt, xy]
revoked: true                      # TODO: 人工审查通过后才可改为 false
"""


def create_app(
    slug: str,
    package: str,
    version: str,
    *,
    repo_root: pathlib.Path | str | None = None,
    app_dir: pathlib.Path | str | None = None,
    aliases: list[str] | None = None,
    seed_from: pathlib.Path | str | None = None,
    compat_min: str | None = None,
    compat_max_excl: str | None = None,
    verified_at: str | None = None,
    template_path: pathlib.Path | str = DEFAULT_TEMPLATE,
) -> list[pathlib.Path]:
    """Create a protected new app and return all written paths."""
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(f"slug 非法：{slug!r}（应匹配 {SLUG_RE.pattern}）")

    root = pathlib.Path(repo_root) if repo_root else _repo_root()
    root = root.resolve()
    output_dir = _resolve_path(app_dir or pathlib.Path("apps") / slug, root)
    _assert_new_or_empty(output_dir)

    # Import the repository initializer only after resolving the target. This
    # keeps the skill's configuration independent from the current shell cwd.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools.init_app import scaffold_docs, seed_entries, write_app

    docs = scaffold_docs(
        slug,
        package,
        version,
        verified_at=verified_at,
        aliases=aliases,
        compat_min=compat_min,
        compat_max_excl=compat_max_excl,
    )
    docs["prerequisites.yaml"]["known_codes"] = load_standard_known_codes(template_path)
    if seed_from:
        seed_entries(docs, _resolve_path(seed_from, root))

    written = list(write_app(output_dir, docs))
    env_example = output_dir / ENV_EXAMPLE_NAME
    env_example.write_text(render_env_example(package, version), encoding="utf-8")
    written.append(env_example)
    reminder = output_dir / REMINDER_NAME
    reminder.write_text(
        render_reminder(slug, package, version, len(docs["prerequisites.yaml"]["known_codes"])),
        encoding="utf-8",
    )
    written.append(reminder)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="初始化新 App，写入 skill 配置中的标准 known_codes；非空目录绝不覆盖。"
    )
    parser.add_argument("slug")
    parser.add_argument("--package", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--aliases")
    parser.add_argument("--seed-from")
    parser.add_argument("--compat-min")
    parser.add_argument("--compat-max-excl")
    parser.add_argument("--verified-at")
    parser.add_argument("--app-dir")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    args = parser.parse_args(argv)

    aliases = [item.strip() for item in args.aliases.split(",") if item.strip()] if args.aliases else None
    try:
        written = create_app(
            args.slug,
            args.package,
            args.version,
            app_dir=args.app_dir,
            aliases=aliases,
            seed_from=args.seed_from,
            compat_min=args.compat_min,
            compat_max_excl=args.compat_max_excl,
            verified_at=args.verified_at,
            template_path=args.template,
        )
    except (FileExistsError, ValueError, OSError) as exc:
        print(f"app-init 失败：{exc}", file=sys.stderr)
        return 2

    output_dir = pathlib.Path(args.app_dir) if args.app_dir else pathlib.Path("apps") / args.slug
    print(f"已创建新 App：{output_dir}")
    print(f"  标准 known_codes：{len(load_standard_known_codes(args.template))} 条")
    for path in written:
        print(f"  - {path}")
    print(f"  请查看：{output_dir / ENV_EXAMPLE_NAME}")
    print(f"  请查看：{output_dir / REMINDER_NAME}")
    print("  注意：env.yaml 未生成；已生成安全模板 env.yaml.example，需人工复制、补充和认证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
