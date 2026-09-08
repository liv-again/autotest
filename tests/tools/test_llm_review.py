from pathlib import Path

import pytest

from tools.llm_review_queue import build_review_queue
from tools.llm_review_results import LLMReviewError, merge_reviews


def _execution(status="✅通过", *, evidence="shots/港股_row_010.png", run_id="run-test"):
    return {
        "execution_manifest": {"run_id": run_id, "expected_count": 1},
        "cases": [
            {
                "case_id": "港股-row-10",
                "sheet": "港股",
                "row": 10,
                "source_order": 1,
                "execution_order": 1,
                "page_group_id": "g1",
                "page_group_key": "港股|行情|港股",
                "navigation_context": {"display_path": "港股 / 行情 / 港股"},
                "expected": "显示港股详情",
                "status": status,
                "actual": "AI执行步骤：\n1. 点击已识别控件\n操作结果：\n截图可见文字/标题：港股",
                "page_observation": "当前页面观察：港股、恒生指数",
                "action_trace": [{"type": "tap", "result": "success"}],
                "evidence": [evidence],
            }
        ]
    }


def _review(queue, **verdict):
    return {
        "schema_version": "1.1",
        "agent": {
            "name": "Codex",
            "model": "gpt-test",
            "prompt_version": "row-review-v1",
        },
        "review_binding": {
            key: queue["queue_binding"][key]
            for key in (
                "run_id",
                "queue_id",
                "queue_sha256",
                "execution_document_sha256",
                "evidence_manifest_sha256",
            )
        },
        "reviews": [
            {
                "case_id": "港股-row-10",
                "target_page_match": True,
                "action_effect_match": True,
                "expected_result_match": True,
                "confidence": 0.99,
                "status": "pass",
                "visible_facts": ["港股"],
                "reason": "截图与预期一致",
                **verdict,
            }
        ],
    }


def test_review_queue_is_row_scoped():
    queue = build_review_queue(_execution(), run_dir="output/run")

    assert queue["review_scope"] == "single_excel_row"
    assert queue["case_count"] == 1
    assert queue["cases"][0]["evidence"] == ["shots/港股_row_010.png"]
    assert "target_page_match" in queue["verdict_schema"]
    assert queue["queue_binding"]["run_id"] == "run-test"
    assert queue["queue_binding"]["queue_id"].startswith("queue-")


def test_llm_cannot_upgrade_deterministic_blocked_case():
    document = _execution("⛔阻塞")
    queue = build_review_queue(document, run_dir="output/run")
    result = merge_reviews(
        document,
        _review(queue, reason="截图看起来符合"),
        queue,
    )

    assert result["cases"][0]["status"] == "⛔阻塞"
    assert result["cases"][0]["llm_review"]["status"] == "✅通过"


def test_llm_marks_wrong_page_as_fail():
    document = _execution()
    queue = build_review_queue(document, run_dir="output/run")
    result = merge_reviews(
        document,
        _review(
            queue,
            target_page_match=False,
            reason="截图页面与港股详情不一致",
        ),
        queue,
    )

    assert result["cases"][0]["status"] == "❌不通过"
    assert "港股详情" in result["cases"][0]["blocked_reason"]


def test_llm_review_requires_every_case():
    document = _execution()
    queue = build_review_queue(document, run_dir="output/run")
    with pytest.raises(LLMReviewError, match="缺少逐行"):
        merge_reviews(
            document,
            {**_review(queue), "reviews": []},
            queue,
        )


def test_review_rejects_changed_execution_records():
    document = _execution()
    queue = build_review_queue(document, run_dir="output/run")
    changed = _execution()
    changed["cases"][0]["page_observation"] = "当前页面观察：上证A股"
    with pytest.raises(LLMReviewError, match="执行记录摘要不匹配"):
        merge_reviews(changed, _review(queue), queue)


def test_review_rejects_changed_screenshot(tmp_path: Path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"original")
    document = _execution(evidence=str(shot))
    queue = build_review_queue(document, run_dir=tmp_path)
    shot.write_bytes(b"replaced")
    with pytest.raises(LLMReviewError, match="截图证据摘要不匹配"):
        merge_reviews(document, _review(queue), queue)


def test_review_rejects_stale_queue_and_unknown_agent():
    document = _execution()
    queue = build_review_queue(document, run_dir="output/run")
    other_queue = build_review_queue(document, run_dir="output/other-run")
    with pytest.raises(LLMReviewError, match="queue_id"):
        merge_reviews(document, _review(queue), other_queue)

    invalid = _review(queue)
    invalid["agent"] = {"name": "OtherAgent", "model": "x", "prompt_version": "v1"}
    with pytest.raises(LLMReviewError, match="Codex 或 OpenCode"):
        merge_reviews(document, invalid, queue)


def test_review_requires_model_and_prompt_version():
    document = _execution()
    queue = build_review_queue(document, run_dir="output/run")
    invalid = _review(queue)
    invalid["agent"] = {"name": "OpenCode", "model": "", "prompt_version": ""}
    with pytest.raises(LLMReviewError, match="agent.model"):
        merge_reviews(document, invalid, queue)
