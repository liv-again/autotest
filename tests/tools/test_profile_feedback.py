import json

from tools._review_profile_feedback_once import review
from tools.profile_feedback import build_profile_feedback


def test_profile_feedback_is_evidence_backed_and_not_direct_write():
    result = build_profile_feedback(
        {
            "cases": [
                {
                    "sheet": "港股",
                    "row": 10,
                    "case_id": "港股-row-010",
                    "page_group_id": "港股-page-group-001",
                    "page_group_key": "港股|行情|港股|港股|港股",
                    "navigation_context": {"display_path": "港股 / 行情 / 港股 / 港股"},
                    "status": "✅通过",
                    "action_trace": [{"type": "tap", "result": "success"}],
                    "page_observation": "当前页面观察：港股、恒生指数",
                    "evidence": ["shots/港股_row_010.png"],
                    "tested_at": "2026-09-07T10:00:00+08:00",
                },
                {
                    "sheet": "港股",
                    "row": 11,
                    "case_id": "港股-row-011",
                    "page_group_id": "港股-page-group-001",
                    "page_group_key": "港股|行情|港股|港股|港股",
                    "navigation_context": {"display_path": "港股 / 行情 / 港股 / 港股"},
                    "status": "✅通过",
                    "action_trace": [{"type": "tap", "result": "success"}],
                    "page_observation": "当前页面观察：港股、港股主板",
                    "evidence": ["shots/港股_row_011.png"],
                    "tested_at": "2026-09-07T10:01:00+08:00",
                },
            ]
        },
        run_dir="output/run-1",
        app_slug="guotou",
        app_version="V10.5.4",
    )

    assert result["direct_profile_write"] is False
    assert result["candidate_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["observed_count"] == 2
    assert candidate["confidence"] == "high"
    assert len(candidate["evidence"]) == 2
    assert candidate["suggested_profile_entry"]["status"] == "verified"


def test_profile_feedback_keeps_successful_runtime_recovery_as_candidate():
    result = build_profile_feedback(
        {
            "cases": [
                {
                    "sheet": "港股",
                    "row": 12,
                    "case_id": "港股-row-012",
                    "page_group_id": "港股-page-group-001",
                    "page_group_key": "港股|行情|港股|港股|港股",
                    "navigation_context": {"display_path": "港股 / 行情 / 港股 / 港股"},
                    "status": "🟡待验证",
                    "action_trace": [{"type": "tap", "result": "success"}],
                    "page_observation": "当前页面观察：港股、港股主板",
                    "evidence": ["shots/港股_row_012.png"],
                    "tested_at": "2026-09-07T10:02:00+08:00",
                    "runtime_recovery": {
                        "enabled": True,
                        "scope": "single_excel_row",
                        "mode": "until_case_terminal",
                        "recovered": True,
                        "attempts": [
                            {
                                "attempt": 2,
                                "result": "recovered",
                                "diagnosis": "检测到遮挡弹窗",
                                "reason": "关闭弹窗后目标页和本行动作均完成",
                                "decision": "retry_current_action",
                                "reset": "none",
                                "replay_safety": "safe",
                                "actions": [{"type": "tap_text", "text": "知道了"}],
                                "recovery_action_trace": [
                                    {"type": "tap", "target": "知道了", "result": "success"}
                                ],
                                "replay_trace": [
                                    {"type": "tap", "target": "行情", "result": "success"}
                                ],
                                "screenshot": "shots/recovery/港股_row_012_attempt_02.png",
                            }
                        ],
                    },
                }
            ]
        },
        run_dir="output/run-2",
        app_slug="guotou",
        app_version="V10.5.4",
    )

    assert result["candidate_count"] == 1
    assert result["recovery_candidate_count"] == 1
    candidate = result["recovery_candidates"][0]
    assert candidate["type"] == "runtime_recovery_success"
    assert candidate["diagnosis"] == "检测到遮挡弹窗"
    assert candidate["recovery_actions"] == [{"type": "tap_text", "text": "知道了"}]
    assert candidate["runtime_recovery_trace"].endswith("runtime_recovery_trace.jsonl")
    assert candidate["candidate_status"] == "unverified"
    assert result["direct_profile_write"] is False


def test_profile_reviewer_reads_recovery_candidates_without_direct_reback(tmp_path):
    run_root = tmp_path / "review-root"
    run_dir = tmp_path / "run"
    (run_dir / "shots" / "recovery").mkdir(parents=True)
    (run_dir / "shots" / "港股_row_012.png").write_bytes(b"final")
    (run_dir / "shots" / "recovery" / "港股_row_012_attempt_01.png").write_bytes(b"recovery")

    feedback = build_profile_feedback(
        {
            "cases": [
                {
                    "sheet": "港股",
                    "row": 12,
                    "case_id": "港股-row-012",
                    "page_group_id": "港股-page-group-001",
                    "page_group_key": "港股|行情|港股|港股|港股",
                    "navigation_context": {"display_path": "港股 / 行情 / 港股 / 港股"},
                    "status": "🟡待验证",
                    "action_trace": [{"type": "tap", "result": "success"}],
                    "page_observation": "当前页面观察：港股、港股主板",
                    "evidence": ["shots/港股_row_012.png"],
                    "runtime_recovery": {
                        "attempts": [
                            {
                                "attempt": 1,
                                "result": "recovered",
                                "diagnosis": "临时弹窗",
                                "reason": "关闭弹窗后重试成功",
                                "actions": [{"type": "tap_text", "text": "知道了"}],
                                "screenshot": "shots/recovery/港股_row_012_attempt_01.png",
                            }
                        ]
                    },
                }
            ]
        },
        run_dir=run_dir,
        app_slug="guotou",
        app_version="V10.5.4",
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "profile_feedback.json").write_text(
        json.dumps(feedback, ensure_ascii=False), encoding="utf-8"
    )
    run_root.mkdir(parents=True)
    (run_root / "summary.json").write_text(
        json.dumps(
            {
                "source": "test",
                "app": {"slug": "guotou"},
                "sheets": {"港股": {"run_dir": str(run_dir)}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = review(run_root)
    reviewed = json.loads(
        (run_dir / "profile_feedback.reviewed.json").read_text(encoding="utf-8")
    )

    assert result["aggregate"]["recovery_candidate_count"] == 1
    assert result["aggregate"]["recovery_hold"] == 1
    assert reviewed["recovery_candidates"][0]["llm_review"]["verdict"] == "hold"
    assert reviewed["recovery_candidates"][0]["llm_review"]["eligible_for_reback"] is False
