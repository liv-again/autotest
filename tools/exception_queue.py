"""Create a compact module-level queue for LLM exception analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.results_quality import status_bucket


EXCEPTION_BUCKETS = {"fail", "partial", "blocked", "pending", "other"}


def _load(path: Path) -> dict[str, Any] | list[Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, (dict, list)):
        raise ValueError("输入结果必须是对象或数组")
    return value


def build_exception_queue(document: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(document, dict):
        records = document.get("cases") or document.get("results") or []
    else:
        records = document
    if not isinstance(records, list):
        raise ValueError("结果中的 cases/results 必须是数组")
    exceptions = []
    for record in records:
        if not isinstance(record, dict):
            continue
        bucket = status_bucket(record.get("status"))
        if bucket not in EXCEPTION_BUCKETS:
            continue
        exceptions.append(
            {
                "exception_id": record.get("case_id") or f"{record.get('sheet')}!{record.get('row')}",
                "sheet": record.get("sheet"),
                "row": record.get("row"),
                "source_order": record.get("source_order"),
                "execution_order": record.get("execution_order"),
                "case_name": record.get("case_name"),
                "status": record.get("status"),
                "actual": record.get("actual") or record.get("observation"),
                "blocked_reason": record.get("blocked_reason") or record.get("reason"),
                "evidence": record.get("evidence") or record.get("evidence_paths") or [],
                "runtime_recovery": record.get("runtime_recovery") or {},
            }
        )
    return {
        "schema_version": "1.0",
        "queue_type": "module_exception_review",
        "review_scope": "module",
        "llm_instruction": "仅分析本队列中的异常用例；不要重新读取或复述整个 Sheet。",
        "exception_count": len(exceptions),
        "cases": exceptions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成模块级异常分析队列")
    parser.add_argument("--input", required=True, help="results.json 或 execution_records.json")
    parser.add_argument("--out", required=True, help="exception_queue.json")
    args = parser.parse_args()
    result = build_exception_queue(_load(Path(args.input).expanduser().resolve()))
    output = Path(args.out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(output), "exceptions": result["exception_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

