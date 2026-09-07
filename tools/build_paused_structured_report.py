"""Build an evidence-only structured report for a paused sheet run.

The paused runner persisted the manifest, state and screenshots, but not the
per-row action records. This script deliberately does not infer pass/fail
from a screenshot. It emits a machine-readable inventory and a human report
with the missing rows called out explicitly.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import xlrd


RUN_DIR = Path(__file__).resolve().parents[1] / "output" / "2026-09-06-guotou-hk-other-fundflow-full"
MANIFEST_PATH = RUN_DIR / "execution_manifest.json"
STATE_PATH = RUN_DIR / "execution_state.json"
SOURCE_PATH = Path(__file__).resolve().parents[1] / "国投行情测试用例(1).xls"
JSON_PATH = RUN_DIR / "structured_report.json"
MD_PATH = RUN_DIR / "structured_report.md"

SHEETS = ["港股", "其他", "看资金"]
SCREENSHOT_RE = re.compile(r"^(港股|其他|看资金)_row_(\d+)\.png$")


def clean(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return str(value).strip()


def iso_timestamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def workbook_cases():
    book = xlrd.open_workbook(str(SOURCE_PATH), formatting_info=False)
    result = {}
    columns = [
        "一级目录",
        "二级目录",
        "三级目录",
        "四级目录",
        "用例名称",
        "优先级",
        "入口",
        "步骤名称",
        "前置条件",
        "操作描述",
        "参数",
        "预期结果",
    ]
    for sheet_name in SHEETS:
        sheet = book.sheet_by_name(sheet_name)
        for row_index in range(1, sheet.nrows):
            values = [clean(value) for value in sheet.row_values(row_index)]
            row_number = row_index + 1
            item = dict(zip(columns, values))
            item.update(
                {
                    "sheet": sheet_name,
                    "row": row_number,
                    "case_id": f"{sheet_name}-row-{row_number:03d}",
                }
            )
            result[(sheet_name, row_number)] = item
    return result


def manifest_cases(manifest):
    return {(item["sheet"], int(item["row"])): item for item in manifest["selected_cases"]}


def merge_case(source_item, manifest_item):
    return {
        "case_id": manifest_item["case_id"],
        "sheet": manifest_item["sheet"],
        "row": int(manifest_item["row"]),
        "case_name": manifest_item["case_name"],
        "category": {
            "level_1": source_item.get("一级目录", ""),
            "level_2": source_item.get("二级目录", ""),
            "level_3": source_item.get("三级目录", ""),
            "level_4": source_item.get("四级目录", ""),
        },
        "priority": source_item.get("优先级", ""),
        "entry": source_item.get("入口", ""),
        "step_name": source_item.get("步骤名称", ""),
        "precondition": source_item.get("前置条件", ""),
        "action": source_item.get("操作描述", ""),
        "parameters": source_item.get("参数", ""),
        "expected": source_item.get("预期结果", ""),
    }


def build_report():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    source = workbook_cases()
    selected = manifest_cases(manifest)

    screenshots = {}
    for path in sorted((RUN_DIR / "shots").glob("*.png")):
        match = SCREENSHOT_RE.match(path.name)
        if not match:
            continue
        sheet_name, row_text = match.groups()
        key = (sheet_name, int(row_text))
        screenshots[key] = path

    evidence_cases = []
    missing_cases = []
    for key, manifest_item in selected.items():
        base = merge_case(source.get(key, {}), manifest_item)
        screenshot = screenshots.get(key)
        if screenshot:
            base.update(
                {
                    "outcome": "evidence_captured",
                    "judgement": "not_determined",
                    "trace_available": False,
                    "evidence": [
                        {
                            "type": "screenshot",
                            "path": str(screenshot.relative_to(RUN_DIR)).replace("\\", "/"),
                            "exists": True,
                            "captured_at": iso_timestamp(screenshot),
                        }
                    ],
                    "notes": "已生成本轮独立截图；暂停时未持久化该行的完整逐步动作轨迹和断言结论，不能仅凭截图判定通过或失败。",
                }
            )
            evidence_cases.append(base)
        else:
            base.update(
                {
                    "outcome": "not_executed",
                    "judgement": "not_determined",
                    "trace_available": False,
                    "evidence": [],
                    "notes": "本轮暂停前没有对应独立截图，不能视为已执行。",
                }
            )
            missing_cases.append(base)

    counts_by_sheet = {}
    for sheet_name in SHEETS:
        expected = [item for item in selected.values() if item["sheet"] == sheet_name]
        captured = [item for item in evidence_cases if item["sheet"] == sheet_name]
        missing = [item for item in missing_cases if item["sheet"] == sheet_name]
        expected_count = len(expected)
        counts_by_sheet[sheet_name] = {
            "expected": expected_count,
            "evidence_captured": len(captured),
            "not_executed": len(missing),
            "pass": 0,
            "fail": 0,
            "blocked": 0,
            "not_determined": expected_count,
            "evidence_coverage_pct": round((len(captured) / expected_count * 100), 2) if expected_count else 0,
            "captured_rows": [item["row"] for item in captured],
            "missing_rows": [item["row"] for item in missing],
        }

    report = {
        "schema_version": "2.0-intermediate",
        "report_type": "paused_evidence_inventory",
        "execution_mode": "intermediate",
        "run_status": state.get("status", "paused"),
        "source_file": manifest.get("source_file", str(SOURCE_PATH)),
        "selected_sheets": manifest.get("source_sheets", SHEETS),
        "manifest_expected_count": int(manifest.get("expected_count", len(selected))),
        "evidence_case_count": len(evidence_cases),
        "missing_case_count": len(missing_cases),
        "pass_count": 0,
        "fail_count": 0,
        "blocked_count": 0,
        "not_determined_count": len(selected),
        "old_results_used": bool(state.get("old_results_used", False)),
        "results_records_written": bool(state.get("results_records_written", False)),
        "final_report_written": bool(state.get("final_report_written", False)),
        "evidence_only": True,
        "counts_by_sheet": counts_by_sheet,
        "state_snapshot": state,
        "cases_with_evidence": evidence_cases,
        "cases_not_executed": missing_cases,
        "limitations": [
            "本报告只基于本轮 execution_manifest.json、execution_state.json 和 shots/*.png 生成。",
            "本轮暂停后没有 execution_records.json，因此没有把截图存在推断为通过或失败。",
            "旧轮次结果未读取、未合并。",
            "如需正式最终测试报告，必须补齐缺失用例并持久化每条用例的 action_trace、实际结果和断言结论。",
        ],
    }
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 港股 / 其他 / 看资金 · 本轮暂停执行结构化报告",
        "",
        "> 报告类型：暂停轮次的证据型中间报告。仅整理本轮已有输出，不代表最终通过率或缺陷结论。",
        "",
        "## 1. 执行摘要",
        "",
        f"- 执行状态：**{report['run_status']}**",
        f"- 选定用例：**{report['manifest_expected_count']}** 条",
        f"- 已留存独立截图：**{report['evidence_case_count']}** 条",
        f"- 未留存本轮证据：**{report['missing_case_count']}** 条",
        "- 通过：0（本轮没有可用于判定通过的持久化断言结果）",
        "- 失败：0（本轮没有可用于判定失败的持久化断言结果）",
        "- 旧结果：未读取、未合并",
        "",
        "## 2. 按 Sheet 汇总",
        "",
        "| Sheet | 应执行 | 已留证 | 未执行/无截图 | 证据覆盖率 | 判定 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for sheet_name in SHEETS:
        item = counts_by_sheet[sheet_name]
        lines.append(
            f"| {sheet_name} | {item['expected']} | {item['evidence_captured']} | {item['not_executed']} | {item['evidence_coverage_pct']:.2f}% | 待判定 |"
        )

    lines.extend(
        [
            "",
            "## 3. 未执行范围",
            "",
            "- 港股：无缺失，已留存第 2–40 行截图。",
            "- 其他：无缺失，已留存第 2–58 行截图。",
            "- 看资金：已留存第 2–12 行截图；第 13–47 行共 35 条没有本轮独立截图，列为未执行。",
            "",
            "## 4. 结果口径",
            "",
            "- `evidence_captured`：仅表示对应截图文件存在。",
            "- `not_executed`：本轮没有对应截图，不视为执行成功或失败。",
            "- `not_determined`：由于缺少逐条动作轨迹和断言结论，本报告不推断通过、失败或阻塞。",
            "",
            "## 5. 机器可读明细",
            "",
            "完整逐条明细见同目录的 [`structured_report.json`](./structured_report.json)，其中包含用例名称、Excel 行号、操作描述、预期结果、证据路径、状态和限制说明。",
            "",
            "## 6. 生成限制",
            "",
            "本轮暂停后未生成 `execution_records.json` 和最终结果文件，因此当前报告是可审计的中间报告，不替代正式最终测试报告。",
        ]
    )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    result = build_report()
    print(
        json.dumps(
            {
                "json": str(JSON_PATH),
                "markdown": str(MD_PATH),
                "expected": result["manifest_expected_count"],
                "evidence": result["evidence_case_count"],
                "missing": result["missing_case_count"],
            },
            ensure_ascii=False,
        )
    )
