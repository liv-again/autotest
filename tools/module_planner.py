"""Build a module-scoped plan for row-scoped Excel UI execution.

The LLM reads a Sheet once and produces this plan. A deterministic executor
then consumes one case at a time; it does not need to re-interpret the
workbook for every row.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


CANONICAL_COLUMNS = {
    "一级目录": "level_1",
    "1级目录": "level_1",
    "二级目录": "level_2",
    "2级目录": "level_2",
    "三级目录": "level_3",
    "3级目录": "level_3",
    "四级目录": "level_4",
    "4级目录": "level_4",
    "用例名称": "case_name",
    "用例名": "case_name",
    "优先级": "priority",
    "入口": "entry",
    "步骤名称": "step_name",
    "步骤": "step_name",
    "前置条件": "precondition",
    "操作描述": "action",
    "操作": "action",
    "参数": "parameters",
    "预期结果": "expected",
    "预期": "expected",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _header_key(value: Any) -> str:
    return "".join(_text(value).split()).lower()


def _column_map(headers: Iterable[Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    aliases = {_header_key(key): value for key, value in CANONICAL_COLUMNS.items()}
    for index, header in enumerate(headers):
        canonical = aliases.get(_header_key(header))
        if canonical and canonical not in result:
            result[canonical] = index
    return result


def _row_case(sheet_name: str, row: int, values: list[Any], columns: dict[str, int], source_order: int) -> dict[str, Any] | None:
    def get(name: str) -> str:
        index = columns.get(name)
        return _text(values[index]) if index is not None and index < len(values) else ""

    case_name = get("case_name")
    action = get("action")
    expected = get("expected")
    if not any((case_name, action, expected)):
        return None
    return {
        "case_id": f"{sheet_name}-row-{row:03d}",
        "module": sheet_name,
        "sheet": sheet_name,
        "row": row,
        "source_order": source_order,
        "case_name": case_name,
        "priority": get("priority"),
        "entry": get("entry"),
        "step_name": get("step_name"),
        "precondition": get("precondition"),
        "action": action,
        "parameters": get("parameters"),
        "expected": expected,
        "execution_unit": "single_excel_row",
    }


def _read_xls(source: Path, sheet_names: list[str]) -> list[dict[str, Any]]:
    import xlrd

    book = xlrd.open_workbook(str(source), formatting_info=False)
    available = book.sheet_names()
    selected = sheet_names or available
    missing = [name for name in selected if name not in available]
    if missing:
        raise ValueError(f"Excel 中不存在 Sheet: {', '.join(missing)}")
    cases: list[dict[str, Any]] = []
    source_order = 0
    for sheet_name in selected:
        sheet = book.sheet_by_name(sheet_name)
        columns = _column_map(sheet.row_values(0)) if sheet.nrows else {}
        for row_index in range(1, sheet.nrows):
            item = _row_case(sheet_name, row_index + 1, sheet.row_values(row_index), columns, source_order + 1)
            if item:
                source_order += 1
                item["source_order"] = source_order
                cases.append(item)
    return cases


def _read_xlsx(source: Path, sheet_names: list[str]) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    book = load_workbook(source, read_only=True, data_only=True)
    available = list(book.sheetnames)
    selected = sheet_names or available
    missing = [name for name in selected if name not in available]
    if missing:
        raise ValueError(f"Excel 中不存在 Sheet: {', '.join(missing)}")
    cases: list[dict[str, Any]] = []
    source_order = 0
    for sheet_name in selected:
        sheet = book[sheet_name]
        rows = sheet.iter_rows(values_only=True)
        try:
            headers = next(rows)
        except StopIteration:
            continue
        columns = _column_map(headers)
        for row_number, values in enumerate(rows, start=2):
            item = _row_case(sheet_name, row_number, list(values), columns, source_order + 1)
            if item:
                source_order += 1
                item["source_order"] = source_order
                cases.append(item)
    return cases


def load_cases(source: str | Path, sheet_names: Iterable[str] | None = None) -> list[dict[str, Any]]:
    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    selected = list(sheet_names or [])
    if path.suffix.lower() == ".xls":
        return _read_xls(path, selected)
    return _read_xlsx(path, selected)


def build_module_plan(source: str | Path, sheet_names: Iterable[str] | None = None) -> dict[str, Any]:
    cases = load_cases(source, sheet_names)
    modules: list[dict[str, Any]] = []
    for module_name in dict.fromkeys(case["module"] for case in cases):
        module_cases = [case for case in cases if case["module"] == module_name]
        for module_order, case in enumerate(module_cases, start=1):
            case["module_order"] = module_order
        modules.append(
            {
                "module": module_name,
                "sheet": module_name,
                "expected_count": len(module_cases),
                "cases": module_cases,
            }
        )
    return {
        "schema_version": "1.0",
        "plan_type": "module_execution_plan",
        "planning_scope": "module",
        "execution_scope": "single_excel_row",
        "review_scope": "module_exceptions",
        "page_batching_allowed": False,
        "llm_instruction": "本计划由 LLM 按模块生成一次；执行器按 Excel 行逐条消费，模块完成后只分析 exception_queue.json。",
        "source_file": str(Path(source).expanduser().resolve()),
        "selected_sheets": [module["sheet"] for module in modules],
        "expected_count": len(cases),
        "execution_manifest": {
            "manifest_version": "2.0",
            "mode": "full",
            "execution_scope": "single_excel_row",
            "source_file": str(Path(source).expanduser().resolve()),
            "source_sheets": [module["sheet"] for module in modules],
            "expected_count": len(cases),
            "selected_cases": [
                {
                    "case_id": case["case_id"],
                    "sheet": case["sheet"],
                    "row": case["row"],
                    "source_order": case["source_order"],
                    "case_name": case["case_name"],
                }
                for case in cases
            ],
        },
        "modules": modules,
    }


def write_plan(plan: dict[str, Any], output: str | Path) -> Path:
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="按 Sheet 生成模块规划，供逐行执行器消费")
    parser.add_argument("--source", required=True, help=".xls/.xlsx 用例文件")
    parser.add_argument("--sheet", action="append", dest="sheets", help="指定 Sheet，可重复；默认读取全部")
    parser.add_argument("--out", required=True, help="module_plan.json 输出路径")
    args = parser.parse_args()
    plan = build_module_plan(args.source, args.sheets)
    output = write_plan(plan, args.out)
    print(json.dumps({"out": str(output), "modules": len(plan["modules"]), "cases": plan["expected_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
