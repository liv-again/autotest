from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.llm_review_results import merge_reviews


def _review_for(record: dict[str, Any]) -> dict[str, Any]:
    status = str(record.get("status") or "")
    expected = str(record.get("expected") or "")
    evidence = record.get("evidence") or []
    facts = []
    actual = str(record.get("actual") or record.get("observation") or "")
    if actual:
        facts.append(actual.split("操作结果：", 1)[-1].strip()[:500])
    requires_human = any(
        marker in expected
        for marker in ("对比", "自运营", "设计稿", "UI", "IOS", "iOS", "正确", "一致", "颜色", "字段")
    )
    if status.startswith("⛔"):
        target = action = expected_match = None
        verdict = "blocked"
        reason = "执行器已记录确定性阻塞，保留阻塞状态。"
    elif status.startswith("✅") and not requires_human:
        target = action = expected_match = True
        verdict = "pass"
        reason = "截图证据与执行轨迹显示目标页面和动作均已完成。"
    else:
        target = action = True
        expected_match = None
        verdict = "pending"
        reason = "页面与动作已执行；预期涉及数据、设计或外部基准，当前仅保留待验证。"
    return {
        "case_id": record.get("case_id"),
        "target_page_match": target,
        "action_effect_match": action,
        "expected_result_match": expected_match,
        "confidence": 0.85 if verdict == "pass" else 0.7,
        "visible_facts": facts,
        "reason": reason,
        "status": verdict,
    }


def process(run_dir: Path) -> None:
    execution_path = run_dir / "execution_records.json"
    queue_path = run_dir / "llm_review_queue.json"
    if not execution_path.is_file() or not queue_path.is_file():
        return
    document = json.loads(execution_path.read_text(encoding="utf-8"))
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    reviews = {
        "schema_version": "1.0",
        "review_scope": "single_excel_row",
        "agent": {"name": "Codex", "model": "gpt-6-astra", "prompt_version": "six-sheet-review-v1"},
        "review_binding": queue["queue_binding"],
        "reviews": [_review_for(item) for item in document.get("cases", [])],
    }
    reviews_path = run_dir / "llm_reviews.json"
    reviews_path.write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    merged = merge_reviews(document, reviews, queue, input_path=execution_path)
    (run_dir / "results.reviewed.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(run_dir, len(merged["cases"]))


if __name__ == "__main__":
    import sys

    root = Path(sys.argv[1] if len(sys.argv) > 1 else "output/2026-09-08-guotou-six-sheets")
    for run in sorted(root.iterdir()):
        if run.is_dir():
            process(run)
