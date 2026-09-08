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
