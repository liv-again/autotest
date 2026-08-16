import argparse
import os
import sys

# 作为脚本直接运行(python tools/prereq_extract.py)时 sys.path[0] 是 tools/,
# 注入仓根使 `import tools.*` 可用,避免 ModuleNotFoundError: No module named 'tools'。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import yaml  # noqa: E402

from tools.contracts.validate import validate  # noqa: E402
from tools.prereq.extract import extract  # noqa: E402
from tools.prereq.rules import load_rules  # noqa: E402

NEED_POL = {"positive", "negative_property"}


def _load_known_codes(prerequisites_path):
    if not prerequisites_path:
        return []
    with open(prerequisites_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return doc.get("known_codes", []) or []


def _resolve_needed_codes(required_instruments, known_codes):
    """属性子集匹配:code 命中某 required_instrument ⇔ 该 instrument 的每个 (k,v) 都在 code.attributes 里且相等。"""
    out = []
    for code in known_codes:
        attrs = code.get("attributes", {}) or {}
        for inst in required_instruments:
            if inst and all(attrs.get(k) == v for k, v in inst.items()):
                out.append(code["code"])
                break
    return sorted(set(out))


def _fmt_attrs(case):
    parts = []
    for inst in case.get("required_instruments", []):
        if inst:
            parts.append(",".join(f"{k}={v}" for k, v in inst.items()))
    acct = case.get("required_account", "any")
    if acct and acct != "any":
        parts.append(f"account={acct}")
    return "; ".join(p for p in parts if p) or "-"


def render_md(req):
    s = req["summary"]
    lines = [
        "# 本轮前置(自动派生,勿手改;改规则/清单再生成)",
        "",
        f"- 规则版本: {req['rules_version']}",
        f"- 概览: identified={s.get('identified', 0)} / "
        f"unidentified={s.get('unidentified', 0)} / "
        f"conflict={s.get('conflict', 0)} / 缺码={len(s.get('missing_codes', []))}",
        "",
        "## 需备码清单",
        "| TC | 标题 | 状态 | 所需属性 | 已解析码 |",
        "|---|---|---|---|---|",
    ]
    for c in req["cases"]:
        if c["polarity"] not in NEED_POL:
            continue
        codes = c.get("needed_codes") or []
        code_cell = "、".join(codes) if codes else "⚠️缺码"
        lines.append(
            f"| {c['tc_id']} | {c['title']} | {c['status']} | {_fmt_attrs(c)} | {code_cell} |"
        )
    lines += ["", "## 无需专门前置(no_prereq)"]
    lines += [f"- {c['tc_id']} {c['title']}"
              for c in req["cases"] if c["polarity"] == "no_prereq"] or ["- (无)"]
    lines += ["", "## 未识别(需人工确认规则覆盖)"]
    lines += [f"- {tid}" for tid in req["unidentified"]] or ["- (无)"]
    lines += ["", "## 冲突(多规则不相容,需人工裁决)"]
    lines += [f"- {cf['tc_id']} rules={cf['rule_ids']} note={cf['note']}"
              for cf in req["conflicts"]] or ["- (无)"]
    lines.append("")
    return "\n".join(lines)


def run(cases_path, rules_path, out_yaml, out_md,
        prerequisites_path=None, app_slug="guojin", market="北交所"):
    with open(cases_path, encoding="utf-8") as f:
        cases = (yaml.safe_load(f) or {}).get("cases", []) or []
    rules_doc = load_rules(rules_path)
    req = extract(cases, rules_doc, app_slug=app_slug, market=market)

    known = _load_known_codes(prerequisites_path)
    missing = []
    for c in req["cases"]:
        if c["polarity"] in NEED_POL:
            c["needed_codes"] = _resolve_needed_codes(c["required_instruments"], known)
            if not c["needed_codes"]:
                missing.append(c["tc_id"])
        else:                        # no_prereq / unknown(未识别) 永不判缺码
            c["needed_codes"] = []
    req["summary"]["missing_codes"] = missing

    errs = validate(req, "prereq_request")
    if errs:
        raise ValueError(f"prereq_request schema errors: {errs}")

    with open(out_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(req, f, allow_unicode=True, sort_keys=False)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(render_md(req))
    return req


def main(argv=None):
    ap = argparse.ArgumentParser(description="从用例集派生本轮前置(yaml+md,缺码高亮)")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--rules", default="tools/prereq_rules.yaml")
    ap.add_argument("--out-yaml", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--prerequisites", default=None)
    ap.add_argument("--app", default="guojin")
    ap.add_argument("--market", default="北交所")
    a = ap.parse_args(argv)
    req = run(a.cases, a.rules, a.out_yaml, a.out_md,
              prerequisites_path=a.prerequisites, app_slug=a.app, market=a.market)
    s = req["summary"]
    print(f"identified={s['identified']} unidentified={s['unidentified']} "
          f"conflict={s['conflict']} missing_codes={s['missing_codes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
