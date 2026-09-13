"""Build the row-level evidence queue consumed by the LLM reviewer.

The low-level mobile driver remains deterministic, but its normal input is a
structured action plan authored by the selected Agent.  This module makes the semantic LLM
review explicit instead of pretending that a button tap or a screenshot file
is a pass result.  The reviewer receives one compact item per Excel row and
returns verdicts that can be merged by ``llm_review_results.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.llm_review_contract import (
    CONTRACT_SCHEMA_VERSION,
    evidence_fingerprints,
    evidence_manifest,
    make_queue_binding,
    queue_sha256,
    sha256_bytes,
)
from tools.agent_binding import reviewer_default_from_document


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _records(document: Mapping[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(document, Mapping):
        values = document.get("cases") or document.get("results") or []
    else:
        values = document
    return [item for item in values if isinstance(item, dict)]


def build_review_queue(
    document: Mapping[str, Any] | list[Any],
    *,
    run_dir: str | Path,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create a compact, row-scoped LLM review queue."""

    root = Path(run_dir).expanduser().resolve()
    cases: list[dict[str, Any]] = []
    records = _records(document)
    for record in _records(document):
        cases.append(
            {
                "case_id": record.get("case_id"),
                "sheet": record.get("sheet"),
                "row": record.get("row"),
                "source_order": record.get("source_order"),
                "execution_order": record.get("execution_order"),
                "page_group_id": record.get("page_group_id"),
                "page_group_key": record.get("page_group_key"),
                "navigation_context": record.get("navigation_context") or {},
                "profile_hints": record.get("profile_hints") or [],
                "case_name": record.get("case_name"),
                "action": record.get("action"),
                "expected": record.get("expected"),
                "planning_mode": record.get("planning_mode"),
                "action_plan_case": record.get("action_plan_case") or {},
                "executor_status": record.get("status"),
                "actual": record.get("actual") or record.get("observation"),
                "page_observation": record.get("page_observation"),
                "action_trace": record.get("action_trace") or [],
                "runtime_recovery": record.get("runtime_recovery") or {},
                "evidence": [
                    str(path).replace("\\", "/")
                    for path in (record.get("evidence") or record.get("evidence_paths") or [])
                ],
                "evidence_fingerprints": evidence_fingerprints(record, root),
            }
        )
    evidence_items = evidence_manifest(records, root)
    reviewer_default = reviewer_default_from_document(
        document if isinstance(document, Mapping) else None
    )
    execution_manifest = (
        document.get("execution_manifest")
        if isinstance(document, Mapping)
        else None
    )
    run_agent_binding = (
        execution_manifest.get("agent_binding")
        if isinstance(execution_manifest, Mapping)
        else None
    )
    result: dict[str, Any] = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "queue_type": "row_llm_execution_review",
        "review_scope": "single_excel_row",
        "run_dir": str(root).replace("\\", "/"),
        "execution_manifest": execution_manifest,
        "agent_binding": run_agent_binding,
        "review_agent_default": reviewer_default,
        "source_execution_path": (
            str(Path(source_path).expanduser().resolve()).replace("\\", "/")
            if source_path is not None
            else None
        ),
        "llm_instruction": (
            "逐条读取 expected、navigation_context、action_plan_case、action_trace、runtime_recovery、page_observation 和 evidence 截图。"
            "先判断截图是否为目标页面，再判断动作效果和预期结果。不要根据 executor_status 直接通过，"
            "也不能用 LLM 结果覆盖确定性页面/动作阻塞。每项必须返回 JSON；reason 必须是非空的具体判断理由，"
            "并会被回填到 AI实测结果的‘判断理由’段落。默认使用 review_agent_default 中的 Agent 和 model；"
            "若有明确的角色级覆盖，必须在输出 agent 元数据中记录实际使用者。"
        ),
        "verdict_schema": {
            "case_id": "string",
            "target_page_match": "true|false|null",
            "action_effect_match": "true|false|null",
            "expected_result_match": "true|false|null",
            "confidence": "number 0..1",
            "visible_facts": "string[]",
            "reason": "non-empty string; explain why the verdict matches or does not match the evidence",
            "status": "pass|fail|blocked|pending",
        },
        "case_count": len(cases),
        "cases": cases,
        "evidence_manifest": evidence_items,
    }
    binding = make_queue_binding(
        document if isinstance(document, Mapping) else {"cases": records},
        run_dir=root,
        evidence_items=evidence_items,
    )
    if source_path is not None:
        source = Path(source_path).expanduser().resolve()
        if source.is_file():
            binding["execution_source_file_sha256"] = sha256_bytes(source.read_bytes())
    result = {key: value for key, value in result.items() if value is not None}
    result["queue_binding"] = binding
    binding["queue_sha256"] = queue_sha256(result)
    binding["queue_id"] = f"queue-{binding['queue_sha256'][:16]}"
    return result


def _load(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, (dict, list)):
        raise ValueError("输入结果必须是对象或数组")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="生成逐行 LLM 页面和结果复核队列")
    parser.add_argument("--input", required=True, help="execution_records.json 或 results.json")
    parser.add_argument("--out", required=True, help="llm_review_queue.json")
    args = parser.parse_args()
    output = Path(args.out).expanduser().resolve()
    source = Path(args.input).expanduser().resolve()
    result = build_review_queue(_load(source), run_dir=output.parent, source_path=source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(output), "cases": result["case_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
