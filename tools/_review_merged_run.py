"""Create an evidence-constrained review for a first-pass/retest merge."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


def visible_facts(record: dict[str, Any]) -> list[str]:
    actual = str(record.get("actual") or record.get("observation") or "")
    body = actual.split("操作结果：", 1)[-1].split("判断理由：", 1)[0].strip()
    facts = [body[:700]] if body else []
    for item in record.get("action_trace") or []:
        if str(item.get("result")) == "success":
            detail = str(item.get("detail") or item.get("target") or "")
            if detail:
                facts.append(f"动作成功：{detail[:160]}")
    return list(dict.fromkeys(x for x in facts if x))[:4]


def has_successful_action(record: dict[str, Any]) -> bool:
    trace = record.get("execution_trace") or record.get("action_trace") or []
    return bool(trace) and not any(str(item.get("result")) == "failed" for item in trace)


def actual_text(record: dict[str, Any]) -> str:
    actual = str(record.get("actual") or record.get("observation") or "")
    return actual.split("操作结果：", 1)[-1].split("判断理由：", 1)[0]


def quoted_terms(expected: str) -> list[str]:
    terms = re.findall(r"[“\"]([^”\"]+)[”\"]", expected)
    return [x for x in terms if len(x) <= 20 and x not in {"某股票", "数据"}]


def can_pass(record: dict[str, Any]) -> tuple[bool, str]:
    if not has_successful_action(record):
        return False, "执行轨迹不完整，不能把截图存在等同于动作完成。"
    expected = str(record.get("expected") or "")
    action = str(record.get("action") or "")
    actual = actual_text(record)
    terms = quoted_terms(expected)
    if "页面身份" in str(record.get("case_name") or "") and terms and all(t in actual for t in terms):
        return True, "复测后的页面观察包含用例要求的页面身份文本，页面门禁和页面文字动作均成功。"
    # Pure navigation/list-entry checks can be concluded when the expected
    # structural markers are all visible after a successful tap.
    structural = [t for t in ("返回", "列表", "最新", "涨幅", "涨跌") if t in expected]
    if action.find("更多") >= 0 and "返回" in actual and structural and all(t in actual for t in structural):
        return True, "复测动作成功，独立列表页的返回控件和主要列表字段均出现在复测观察中。"
    if action.startswith("点击") and any(k in expected for k in ("进入", "打开", "展开")):
        if terms and all(t in actual for t in terms):
            return True, "复测点击动作成功，预期入口/目标文本在复测页面观察中可见。"
    return False, "复测已形成页面和动作证据，但当前预期还涉及数据完整性、排序、刷新、切换或状态变化，保留待验证。"


def main(run_dir: str) -> None:
    raise RuntimeError(
        "_review_merged_run.py 已停用：不能用固定规则伪造 llm_reviews.json；"
        "请由真实复核 Agent 读取 llm_review_queue.json 后调用 llm_review_results.py merge。"
    )
    root = Path(run_dir)
    merged = json.loads((root / "results.merged.json").read_text(encoding="utf-8"))
    queue = json.loads((root / "llm_review_queue.json").read_text(encoding="utf-8"))
    reviews: list[dict[str, Any]] = []
    for record in merged.get("cases", []):
        status = str(record.get("status") or "")
        if status.startswith("⛔"):
            reviews.append({
                "case_id": record.get("case_id"),
                "target_page_match": None,
                "action_effect_match": None,
                "expected_result_match": None,
                "confidence": 0.98,
                "visible_facts": visible_facts(record),
                "reason": str(record.get("judgment_reason") or "复测后仍存在确定性页面或动作阻塞，保留阻塞。")[:1200],
                "status": "blocked",
            })
            continue
        passed, reason = can_pass(record)
        reviews.append({
            "case_id": record.get("case_id"),
            "target_page_match": True,
            "action_effect_match": True,
            "expected_result_match": True if passed else None,
            "confidence": 0.93 if passed else 0.82,
            "visible_facts": visible_facts(record),
            "reason": reason,
            "status": "pass" if passed else "pending",
        })
    reviewer = queue.get("review_agent_default") or {}
    document = {
        "schema_version": "1.1",
        "agent": {
            "name": reviewer.get("agent") or "Codex",
            "model": reviewer.get("model") or "gpt-5.6-luna",
            "prompt_version": "row-review-v2-retest-evidence-constrained",
        },
        "review_binding": queue["queue_binding"],
        "reviews": reviews,
    }
    (root / "llm_reviews.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reviews": len(reviews), "pass": sum(x["status"] == "pass" for x in reviews), "pending": sum(x["status"] == "pending" for x in reviews), "blocked": sum(x["status"] == "blocked" for x in reviews)}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sixgill/output/2026-09-17-guotou-remaining-llm-retest-v2")
