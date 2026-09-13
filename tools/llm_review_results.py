"""Merge structured LLM verdicts without weakening deterministic gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.llm_review_contract import (
    ReviewContractError,
    validate_queue_integrity,
    validate_review_binding,
)
from tools.results_quality import append_judgment_reason, judgment_reason_issue, status_bucket


class LLMReviewError(ValueError):
    """Raised when row-level LLM review results are incomplete or unsafe."""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _key(item: Mapping[str, Any]) -> str:
    case_id = _text(item.get("case_id"))
    if case_id:
        return f"id:{case_id}"
    sheet = _text(item.get("sheet"))
    row = item.get("row")
    if sheet and row not in (None, ""):
        return f"{sheet}!{row}"
    raise LLMReviewError(f"LLM 复核项缺少 case_id 或 sheet+row: {item!r}")


def _reviews(document: Mapping[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(document, Mapping):
        values = document.get("reviews") or document.get("cases") or []
    else:
        values = document
    return [item for item in values if isinstance(item, dict)]


def _normalised_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.strip().casefold()
        if value in {"true", "yes", "1", "pass", "通过"}:
            return True
        if value in {"false", "no", "0", "fail", "不通过"}:
            return False
    raise LLMReviewError(f"LLM 复核布尔字段非法: {value!r}")


def _verdict_status(review: Mapping[str, Any]) -> str:
    target = _normalised_bool(review.get("target_page_match"))
    action = _normalised_bool(review.get("action_effect_match"))
    expected = _normalised_bool(review.get("expected_result_match"))
    requested = _text(review.get("status")).casefold()
    if target is False or action is False or expected is False:
        return "❌不通过"
    if target is True and action is True and expected is True:
        return "✅通过"
    if requested == "blocked":
        return "⛔阻塞"
    return "🟡待验证"


def merge_reviews(
    document: Mapping[str, Any],
    review_document: Mapping[str, Any] | list[Any],
    queue_document: Mapping[str, Any] | None = None,
    *,
    input_path: str | Path | None = None,
) -> dict[str, Any]:
    if queue_document is None:
        raise LLMReviewError("必须提供当前运行生成的 llm_review_queue.json，不能只合并 reviews.json")
    if not isinstance(review_document, Mapping):
        raise LLMReviewError("llm_reviews.json 必须是带 review_binding 和 agent 元数据的 JSON 对象")
    try:
        queue_binding = validate_queue_integrity(document, queue_document, input_path=input_path)
        agent_metadata = validate_review_binding(review_document, queue_binding)
    except ReviewContractError as exc:
        raise LLMReviewError(str(exc)) from exc

    records = [dict(item) for item in document.get("cases", []) if isinstance(item, dict)]
    reviews = _reviews(review_document)
    review_map: dict[str, dict[str, Any]] = {}
    for review in reviews:
        identity = _key(review)
        if not _text(review.get("case_id")):
            raise LLMReviewError(f"LLM 复核项必须包含当前队列中的 case_id: {identity}")
        if identity in review_map:
            raise LLMReviewError(f"LLM 复核结果重复: {identity}")
        confidence = review.get("confidence")
        if confidence not in (None, ""):
            try:
                if not 0 <= float(confidence) <= 1:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise LLMReviewError(f"LLM 置信度必须在 0..1: {identity}") from exc
        reason_issue = judgment_reason_issue(review.get("reason"))
        if reason_issue:
            raise LLMReviewError(f"LLM 复核项的判断理由无效: {identity}: {reason_issue}")
        review_map[identity] = dict(review)

    merged: list[dict[str, Any]] = []
    missing: list[str] = []
    for record in records:
        identity = _key(record)
        review = review_map.get(identity) or review_map.get(f"id:{_text(record.get('case_id'))}")
        if review is None:
            missing.append(identity)
            continue
        item = dict(record)
        executor_bucket = status_bucket(record.get("status"))
        proposed = _verdict_status(review)
        # Deterministic setup/action/evidence failures are terminal.  LLM can
        # explain them but cannot upgrade them to pass.
        if executor_bucket == "blocked":
            final_status = record.get("status") or "⛔阻塞"
            reason = _text(record.get("blocked_reason")) or "确定性执行门禁失败，LLM不能覆盖"
        elif not record.get("action_trace") or not record.get("evidence"):
            final_status = "⛔阻塞"
            reason = "缺少真实 action_trace 或独立 evidence，不能判定通过"
        else:
            final_status = proposed
            reason = _text(review.get("reason"))
        item["pre_review_status"] = record.get("status")
        item["status"] = final_status
        item["llm_review"] = {
            "status": proposed,
            "target_page_match": review.get("target_page_match"),
            "action_effect_match": review.get("action_effect_match"),
            "expected_result_match": review.get("expected_result_match"),
            "confidence": review.get("confidence"),
            "visible_facts": review.get("visible_facts") or [],
            "reason": reason,
        }
        if not reason:
            raise LLMReviewError(f"无法为用例生成非空判断理由: {identity}")

        actual = _text(item.get("actual")) or _text(item.get("observation"))
        if actual:
            item["actual"] = append_judgment_reason(actual, final_status, reason)
        for step in item.get("steps") or []:
            if isinstance(step, dict):
                step_actual = _text(step.get("actual")) or _text(step.get("observation"))
                if step_actual:
                    step["actual"] = append_judgment_reason(step_actual, final_status, reason)
                step["judgment_reason"] = reason
                step["judgment"] = {
                    "status": final_status,
                    "reason": reason,
                    "source": "llm_review" if executor_bucket != "blocked" else "executor_gate",
                }
        item["judgment_reason"] = reason
        item["judgment"] = {
            "status": final_status,
            "reason": reason,
            "source": "llm_review" if executor_bucket != "blocked" else "executor_gate",
        }
        if reason and final_status != "✅通过":
            item["blocked_reason"] = reason
        merged.append(item)

    if missing:
        raise LLMReviewError(f"缺少逐行 LLM 复核结果: {', '.join(missing)}")
    queue_keys = {_key(item) for item in queue_document.get("cases", []) if isinstance(item, Mapping)}
    record_keys = {_key(item) for item in records}
    review_keys = set(review_map)
    if queue_keys != record_keys:
        raise LLMReviewError("当前 llm_review_queue.json 与执行记录的用例集合不一致")
    extra = review_keys - record_keys
    if extra:
        raise LLMReviewError(f"LLM 复核结果包含当前运行之外的用例: {', '.join(sorted(extra))}")
    if len(merged) != len(records):
        raise LLMReviewError("LLM 复核结果数量与执行记录不一致")
    result = {
        "schema_version": "1.1",
        "reviewed": True,
        "review_scope": "single_excel_row",
        "review_binding": {
            "run_id": queue_binding.get("run_id"),
            "queue_id": queue_binding.get("queue_id"),
            "queue_sha256": queue_binding.get("queue_sha256"),
            "execution_document_sha256": queue_binding.get("execution_document_sha256"),
            "evidence_manifest_sha256": queue_binding.get("evidence_manifest_sha256"),
        },
        "agent": agent_metadata,
        "execution_manifest": document.get("execution_manifest"),
        "setup_trace": document.get("setup_trace") or [],
        "cases": merged,
    }
    return {key: value for key, value in result.items() if value is not None}


def _load(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, (dict, list)):
        raise ValueError("输入必须是 JSON 对象或数组")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="合并逐行 LLM 复核结果")
    parser.add_argument("command", nargs="?", default="merge", choices=["merge"])
    parser.add_argument("--input", required=True, help="execution_records.json")
    parser.add_argument("--queue", required=True, help="本次运行生成的 llm_review_queue.json")
    parser.add_argument("--reviews", required=True, help="LLM 输出的 reviews.json")
    parser.add_argument("--out", required=True, help="results.reviewed.json")
    args = parser.parse_args()
    output = Path(args.out).expanduser().resolve()
    input_path = Path(args.input).expanduser().resolve()
    queue_path = Path(args.queue).expanduser().resolve()
    result = merge_reviews(
        _load(input_path),
        _load(Path(args.reviews).expanduser().resolve()),
        _load(queue_path),
        input_path=input_path,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(output), "cases": len(result["cases"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
