"""yaml → md 派生（spec §8.6 / D3）：把 apps/<app>/{profile,prerequisites}.yaml（机器权威）
渲染成人读的 apps/<app>/{画像.md,前置条件.md,速览.md}（脚本派生产物）。
纯字符串渲染、确定性、幂等；账户只渲染 yaml 里已存在的脱敏 mask 字段，不接触/不新增完整账号。
每份派生文件首行固定 banner，标注"勿手改"——改 yaml 后重跑 main() 重新生成。"""
import pathlib

import yaml

BANNER = "<!-- 本文件由 tools/derive_docs.py 从 *.yaml 派生，勿手改；改 yaml 再生成 -->"


def _load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _render_profile_md(profile):
    lines = [BANNER, "", f"# {profile.get('slug', '')} · 券商画像（脚本派生）", ""]
    lines.append(f"App 版本：{profile.get('app_version', '')}")
    lines.append("")
    lines.append("## 功能支持矩阵")
    lines.append("")
    lines.append("| 能力 | 支持 | 说明 | 状态 | 最近验证 |")
    lines.append("|---|---|---|---|---|")
    for c in profile.get("capabilities", []):
        supported = "✅" if c.get("supported") else "❌"
        lines.append(
            f"| {c.get('key', '')} | {supported} | {c.get('note', '')} "
            f"| {c.get('status', '')} | {c.get('last_verified', '')} |"
        )
    lines.append("")
    lines.append("## 入口地图")
    lines.append("")
    for e in profile.get("entries", []):
        lines.append(
            f"- **{e.get('key', '')}**（{e.get('status', '')}，{e.get('last_verified', '')}，"
            f"evidence={e.get('evidence_run', '')}）"
        )
        lines.append(f"  {e.get('path', '')}")
    lines.append("")
    lines.append("## 已验证链路")
    lines.append("")
    for ch in profile.get("verified_chains", []):
        steps = " → ".join(ch.get("steps", []))
        lines.append(
            f"- **{ch.get('key', '')}**：{steps}（{ch.get('status', '')}，{ch.get('last_verified', '')}，"
            f"evidence={ch.get('evidence_run', '')}）"
        )
    lines.append("")
    return "\n".join(lines)


def _render_prerequisites_md(prereq):
    lines = [BANNER, "", f"# {prereq.get('slug', '')} · 前置条件（脚本派生）", ""]
    lines.append("## 账户能力（仅脱敏尾号）")
    lines.append("")
    lines.append("| 别名 | 类型 | 能力 | 脱敏尾号 |")
    lines.append("|---|---|---|---|")
    for a in prereq.get("account_capabilities", []):
        caps = "、".join(a.get("capabilities", []))
        lines.append(f"| {a.get('alias', '')} | {a.get('type', '')} | {caps} | {a.get('mask', '')} |")
    lines.append("")
    lines.append("## 已知代码")
    lines.append("")
    lines.append("| 代码 | 名称 | 市场 | 属性 |")
    lines.append("|---|---|---|---|")
    for c in prereq.get("known_codes", []):
        attrs = "、".join(k for k, v in (c.get("attributes") or {}).items() if v)
        lines.append(f"| {c.get('code', '')} | {c.get('name', '')} | {c.get('market', '')} | {attrs} |")
    lines.append("")
    lines.append("## 标的属性")
    lines.append("")
    lines.append("| 代码 | 名称 | 属性 |")
    lines.append("|---|---|---|")
    for p in prereq.get("instrument_properties", []):
        props = "、".join(p.get("props", []))
        lines.append(f"| {p.get('code', '')} | {p.get('name', '')} | {props} |")
    lines.append("")
    return "\n".join(lines)


def _render_brief_md(profile, prereq):
    lines = [BANNER, "", f"# {profile.get('slug', '')} · 速览（脚本派生）", ""]
    lines.append("## 核心入口")
    lines.append("")
    for e in profile.get("entries", []):
        lines.append(f"- {e.get('key', '')}：{e.get('status', '')}")
    lines.append("")
    lines.append("## 核心码")
    lines.append("")
    for c in prereq.get("known_codes", []):
        lines.append(f"- {c.get('code', '')} {c.get('name', '')}")
    lines.append("")
    lines.append("## 已验证链摘要")
    lines.append("")
    for ch in profile.get("verified_chains", []):
        lines.append(f"- {ch.get('key', '')}")
    lines.append("")
    return "\n".join(lines)


def derive(app_dir):
    """读 app_dir/{profile,prerequisites}.yaml，返回 {文件名: 内容} 三份派生 md。
    纯字符串渲染，无副作用、确定性、幂等（同输入必产出同输出）。"""
    app_dir = pathlib.Path(app_dir)
    profile = _load_yaml(app_dir / "profile.yaml")
    prereq = _load_yaml(app_dir / "prerequisites.yaml")
    return {
        "画像.md": _render_profile_md(profile),
        "前置条件.md": _render_prerequisites_md(prereq),
        "速览.md": _render_brief_md(profile, prereq),
    }


def main(app_dir):
    """把 derive(app_dir) 的结果写盘到 app_dir 下同名文件。"""
    app_dir = pathlib.Path(app_dir)
    for name, content in derive(app_dir).items():
        (app_dir / name).write_text(content, encoding="utf-8")


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else ".")
