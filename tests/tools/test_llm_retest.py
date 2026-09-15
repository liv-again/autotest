import pytest

from tools.llm_retest import (
    LLMRetestError,
    RetestLimits,
    build_retest_request,
    run_retest_case,
    validate_retest_turn,
)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.binding = {
            "role": "retester",
            "agent": "Trae",
            "model": "model-a",
            "prompt_version": "row-retest-v1",
            "session_id": "session-test-1",
        }

    def request(self, payload):
        self.requests.append(payload)
        return self.responses.pop(0)


def _case():
    return {
        "case_id": "股指-row-010",
        "sheet": "股指",
        "row": 10,
        "case_name": "搜索股票",
        "action": "输入股票代码并选择结果",
        "expected": "进入股票详情页",
        "profile_hints": [{"key": "quote.search", "path": "行情/搜索"}],
        "action_plan_case": {"actions": [{"type": "tap_id", "id": "old_id"}]},
    }


def _observation(phase, turn):
    return {
        "phase": phase,
        "turn": turn,
        "screenshot": f"shots/{phase}-{turn}.png",
        "ui_tree": f"ui/{phase}-{turn}.xml",
        "page_observation": "当前页面观察：搜索、结果列表",
        "evidence": [f"shots/{phase}-{turn}.png"],
    }


def _act(turn=1, *, intent="recover"):
    return {
        "schema_version": "1.0",
        "case_id": "股指-row-010",
        "session_id": "session-test-1",
        "turn": turn,
        "decision": "act",
        "intent": intent,
        "action": {"type": "tap_text", "text": "搜索"},
        "reason": "当前截图显示搜索入口，先重新定位可见的搜索控件",
        "visible_facts": ["截图可见搜索入口"],
    }


def _pass(turn=2):
    return {
        "schema_version": "1.0",
        "case_id": "股指-row-010",
        "session_id": "session-test-1",
        "turn": turn,
        "decision": "pass",
        "target_page_match": True,
        "expected_result_match": True,
        "confidence": 0.94,
        "reason": "当前截图显示搜索结果已经进入目标股票详情页，页面元素与预期一致",
        "visible_facts": ["目标页可见", "预期结果可见"],
    }


def test_build_request_excludes_old_action_plan_authority():
    request = build_retest_request(
        _case(),
        session_id="session-test-1",
        turn=1,
        observation=_observation("initial", 0),
        profile={"hints": [{"key": "quote.search"}]},
        first_pass={
            "initial_status": "⛔阻塞",
            "initial_record": {
                "action_plan_case": {"actions": [{"type": "tap_text", "text": "旧按钮"}]},
                "blocked_reason": "旧计划控件失效",
            },
        },
    )

    assert "action_plan_case" not in request["case"]
    assert "action_plan_case" not in request["first_pass"]["initial_record"]
    assert "旧 action plan 仅是历史信息" in request["instruction"]
    assert request["case"]["action"] == "输入股票代码并选择结果"


def test_validate_retest_turn_requires_one_action_for_act():
    response = _act()
    response["action"] = None

    with pytest.raises(LLMRetestError, match="retest\\.turn\\.action"):
        validate_retest_turn(
            response,
            case_id="股指-row-010",
            session_id="session-test-1",
            turn=1,
        )


def test_validate_retest_turn_requires_page_and_expected_for_pass():
    response = _pass()
    response["expected_result_match"] = False

    with pytest.raises(LLMRetestError, match="才能判定通过"):
        validate_retest_turn(
            response,
            case_id="股指-row-010",
            session_id="session-test-1",
            turn=2,
        )


def test_run_retest_reacts_to_action_failure_and_replans():
    session = FakeSession([_act(), _pass()])
    observed = []
    executed = []

    def observe(phase, turn):
        observed.append((phase, turn))
        return _observation(phase, turn)

    def execute(action):
        executed.append(action)
        return {
            "ok": False,
            "detail": "当前 resource-id 已失效，未找到搜索控件",
            "events": [{"type": "tap", "target": "搜索", "result": "not_found"}],
        }

    result = run_retest_case(
        _case(),
        session=session,
        observe=observe,
        execute=execute,
        profile={"hints": []},
        limits=RetestLimits(max_turns=3),
    )

    assert result["status"] == "✅通过"
    assert len(session.requests) == 2
    assert len(executed) == 1
    assert observed == [("initial", 0), ("after_action", 1)]
    assert result["steps"][0]["operation"]["ok"] is False
    assert "AI执行步骤：" in result["actual"]
    assert "操作结果：" in result["actual"]
    assert "判断理由：判定为✅通过。" in result["actual"]
    assert any(item["type"] == "tap" for item in result["action_trace"])


def test_run_retest_blocks_after_repeated_protocol_errors():
    invalid = {"schema_version": "1.0"}
    session = FakeSession([invalid, invalid])

    result = run_retest_case(
        _case(),
        session=session,
        observe=lambda phase, turn: _observation(phase, turn),
        execute=lambda action: {"ok": True},
        limits=RetestLimits(max_turns=3, max_protocol_errors=2),
    )

    assert result["status"] == "⛔阻塞"
    assert "无效协议" in result["reason"]
    assert len(session.requests) == 2
