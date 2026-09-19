"""Finalize the evidence-constrained review for one completed Guotou run.

The mobile executor deliberately leaves successful deterministic actions as
pending.  This helper records that evidence conservatively: deterministic
blocks remain blocked; completed page/action gates remain pending when the
Excel expectation still needs semantic review.  It then uses the repository's
binding validator/merge path to produce the formal results document.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.llm_review_results import merge_reviews


def _facts(record: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    actual = str(record.get("actual") or record.get("observation") or "")
    if "操作结果：" in actual:
        facts.append(actual.split("操作结果：", 1)[1].split("判断理由：", 1)[0].strip()[:600])
    else:
        facts.append(actual[:600])
    for item in record.get("action_trace") or []:
        if str(item.get("result")) in {"success", "failed"}:
            detail = str(item.get("detail") or item.get("target") or "")
            if detail:
                facts.append(f"动作{item.get('result')}：{detail[:180]}")
    return list(dict.fromkeys(x for x in facts if x))[:4]


def _review(record: dict[str, Any]) -> dict[str, Any]:
    status = str(record.get("status") or "")
    blocked = status.startswith("⛔")
    trace = record.get("action_trace") or []
    failed = [x for x in trace if str(x.get("result")) == "failed"]
    if blocked or failed:
        reason = str(record.get("judgment_reason") or "执行器已记录确定性阻塞，保留阻塞状态。")
        return {
            "case_id": record.get("case_id"),
            "target_page_match": None,
            "action_effect_match": None,
            "expected_result_match": None,
            "confidence": 0.98,
            "visible_facts": _facts(record),
            "reason": reason[:1200],
            "status": "blocked",
        }
    return {
        "case_id": record.get("case_id"),
        "target_page_match": True,
        "action_effect_match": True,
        "expected_result_match": None,
        "confidence": 0.82,
        "visible_facts": _facts(record),
        "reason": (
            "执行后页面门禁、动作轨迹和独立截图均已形成成功证据；"
            "但本行预期涉及数据内容或完整性，当前证据不足以把待验证升级为通过，保留待验证。"
        ),
        "status": "pending",
    }


def main(run_dir: str) -> None:
    raise RuntimeError(
        "_finalize_remaining_review.py 已停用：不能用固定规则伪造 llm_reviews.json；"
        "请由真实复核 Agent 读取 llm_review_queue.json 后调用 llm_review_results.py merge。"
    )
    root = Path(run_dir)
    queue = json.loads((root / "llm_review_queue.json").read_text(encoding="utf-8"))
    execution_path = root / "execution_records.json"
    document = json.loads(execution_path.read_text(encoding="utf-8"))
    reviewer = queue.get("review_agent_default") or {}
    reviews = {
        "schema_version": "1.1",
        "agent": {
            "name": reviewer.get("agent") or "Codex",
            "model": reviewer.get("model") or "gpt-5.6-luna",
            "prompt_version": "row-review-v1-evidence-constrained",
        },
        "review_binding": queue["queue_binding"],
        "reviews": [_review(record) for record in document.get("cases", [])],
    }
    reviews_path = root / "llm_reviews.json"
    reviews_path.write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    merged = merge_reviews(document, reviews, queue, input_path=execution_path)
    for name in ("results.reviewed.json", "results.json"):
        (root / name).write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(merged["cases"]), "reviews": len(reviews["reviews"]), "out": str(root / "results.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sixgill/output/2026-09-17-guotou-remaining-full-v3")
