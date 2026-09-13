import json
import sys

import pytest

from tools import _run_three_sheets as runner
from tools.agent_recovery import (
    AgentRecoveryError,
    CommandRecoveryAgent,
    validate_recovery_plan,
)
from tools.app_adapter import AppConfig, GenericAdapter


def _plan(**overrides):
    value = {
        "schema_version": "1.0",
        "plan_type": "agent_runtime_recovery",
        "agent": {
            "name": "test-agent",
            "model": "test-model",
            "prompt_version": "agent-recovery-v1",
        },
        "decision": "retry_current_action",
        "reset": "none",
        "replay_safety": "safe",
        "diagnosis": "popup",
        "reason": "关闭遮挡弹窗后重试",
        "actions": [{"type": "tap_text", "text": "知道了"}],
    }
    value.update(overrides)
    return value


def test_validate_runtime_recovery_plan_normalizes_agent_response():
    plan = validate_recovery_plan(_plan(confidence="0.75"))

    assert plan["decision"] == "retry_current_action"
    assert plan["confidence"] == 0.75
    assert plan["actions"][0]["type"] == "tap_text"


def test_validate_runtime_recovery_plan_normalizes_provider_aliases():
    plan = validate_recovery_plan(
        _plan(
            decision="recover",
            actions=[
                {
                    "type": "swipe",
                    "start": {"x": 540, "y": 1900},
                    "end": {"x": 540, "y": 700},
                }
            ],
        )
    )

    assert plan["decision"] == "retry_current_action"
    assert plan["actions"][0]["x1"] == 540
    assert plan["actions"][0]["y1"] == 1900
    assert plan["actions"][0]["x2"] == 540
    assert plan["actions"][0]["y2"] == 700


def test_validate_runtime_recovery_plan_rejects_uncertain_replay():
    with pytest.raises(AgentRecoveryError, match="replay_safety"):
        validate_recovery_plan(_plan(replay_safety="uncertain"))


def test_validate_runtime_recovery_plan_rejects_blocked_actions():
    with pytest.raises(AgentRecoveryError, match="blocked"):
        validate_recovery_plan(
            _plan(decision="blocked", replay_safety="not_applicable")
        )


def test_command_recovery_agent_uses_json_stdio(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "assert request['request_type'] == 'agent_runtime_recovery_request'\n"
        "print(json.dumps({\n"
        "  'schema_version': '1.0',\n"
        "  'plan_type': 'agent_runtime_recovery',\n"
        "  'agent': {'name': 'stdio-agent', 'model': 'test', 'prompt_version': 'v1'},\n"
        "  'decision': 'blocked',\n"
        "  'reason': '测试阻塞',\n"
        "  'actions': []\n"
        "}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    agent = CommandRecoveryAgent([sys.executable, str(script)], cwd=tmp_path)

    response = agent({"request_type": "agent_runtime_recovery_request"})

    assert response["plan_type"] == "agent_runtime_recovery"
    assert response["reason"] == "测试阻塞"


def test_generic_adapter_reads_overlay_signals(tmp_path):
    config = AppConfig(
        project_root=tmp_path,
        slug="generic",
        app_dir=tmp_path / "apps" / "generic",
        app_file=None,
        profile_path=None,
        packages=(),
        adapter_file=None,
        document={"runtime_recovery": {"overlay_signals": ["新股", "知道了"]}},
    )

    assert GenericAdapter(config).transient_overlay_signals == ("新股", "知道了")


def test_agent_action_execution_detects_configured_overlay(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "screen_elements", lambda: [{"text": "新股申购"}])
    monkeypatch.setattr(
        runner,
        "tap_text",
        lambda *args: calls.append("tap") or True,
    )

    events = []
    progress = {}
    ok, detail, _ = runner.execute_agent_actions(
        events,
        [{"type": "tap_text", "text": "行情"}],
        phase="本行操作",
        progress=progress,
        interruption_detector=lambda elements: "疑似临时覆盖层信号：新股"
        if elements
        else "",
    )

    assert not ok
    assert "覆盖层" in detail
    assert calls == []
    assert progress["failure_stage"] == "before_action"
    assert any(item["type"] == "interrupt" for item in events)


def test_runtime_agent_recovery_retries_from_failed_action(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "screen_elements", lambda: [{"text": "目标页"}])
    monkeypatch.setattr(runner, "take_shot", lambda path: path.write_bytes(b"png") or True)
    monkeypatch.setattr(runner, "wait_for_agent_target", lambda *args, **kwargs: True)
    monkeypatch.setattr(runner, "transient_overlay_detail", lambda elements: "")
    monkeypatch.setattr(runner, "_active_adapter", lambda: type("A", (), {"name": "generic"})())

    def fake_actions(events, actions, *, phase, **kwargs):
        events.append({"type": "tap", "target": phase, "result": "success"})
        return True, "", "agent"

    monkeypatch.setattr(runner, "execute_agent_actions", fake_actions)
    config = AppConfig(
        project_root=tmp_path,
        slug="generic",
        app_dir=tmp_path / "apps" / "generic",
        app_file=None,
        profile_path=None,
        packages=(),
        adapter_file=None,
        document={},
    )
    plan = {
        "case_id": "模块A-row-002",
        "sheet": "模块A",
        "row": 2,
        "target_page": {"description": "目标页", "all_text": ["目标页"]},
        "navigation": [],
        "actions": [{"type": "tap_text", "text": "行情"}],
    }
    setup_events, action_events = [], []

    def fake_agent(request):
        assert request["failure"]["phase"] == "action"
        return _plan()

    outcome = runner.run_runtime_agent_recovery(
        agent=fake_agent,
        max_attempts=1,
        run_id="run-test",
        app_config=config,
        output=tmp_path,
        sheet_name="模块A",
        row=2,
        case={"case_name": "查看行情"},
        case_plan=plan,
        session=runner.ModuleSession(),
        setup_events=setup_events,
        action_events=action_events,
        setup_ok=True,
        action_ok=False,
        action_mode="agent",
        error_detail="控件未找到",
        failure_phase="action",
        action_progress={"failed_index": 1, "retry_safe": True},
        recovery_trace_path=tmp_path / "runtime_recovery_trace.jsonl",
    )

    assert outcome.setup_ok and outcome.action_ok
    assert outcome.attempts[0]["result"] == "recovered"
    assert (tmp_path / "runtime_recovery_trace.jsonl").is_file()


def test_runtime_agent_recovery_keeps_agent_takeover_for_later_blocker(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(runner, "screen_elements", lambda: [{"text": "目标页"}])
    monkeypatch.setattr(runner, "take_shot", lambda path: path.write_bytes(b"png") or True)
    monkeypatch.setattr(runner, "wait_for_agent_target", lambda *args, **kwargs: True)
    monkeypatch.setattr(runner, "_active_adapter", lambda: type("A", (), {"name": "generic"})())

    agent_requests = []
    replay_calls = 0

    def fake_actions(events, actions, *, phase, progress=None, **kwargs):
        nonlocal replay_calls
        events.append({"type": "tap", "target": phase, "result": "success"})
        if phase.startswith("LLM恢复后"):
            replay_calls += 1
            if replay_calls == 1:
                # The next blocker is found before the next business tap, so
                # the first safe recovery does not consume the business replay
                # safety budget.
                if progress is not None:
                    progress.update(
                        {"failed_index": 1, "failure_stage": "before_action", "retry_safe": True}
                    )
                return False, "第二个弹窗再次遮挡", "agent"
        return True, "", "agent"

    monkeypatch.setattr(runner, "execute_agent_actions", fake_actions)
    config = AppConfig(
        project_root=tmp_path,
        slug="generic",
        app_dir=tmp_path / "apps" / "generic",
        app_file=None,
        profile_path=None,
        packages=(),
        adapter_file=None,
        document={},
    )
    plan = {
        "case_id": "模块A-row-002",
        "sheet": "模块A",
        "row": 2,
        "target_page": {"description": "目标页", "all_text": ["目标页"]},
        "navigation": [],
        "actions": [{"type": "tap_text", "text": "行情"}],
    }

    def fake_agent(request):
        agent_requests.append(request)
        assert request["case_takeover"]["scope"] == "single_excel_row"
        assert request["case_takeover"]["previous_turn_count"] == len(agent_requests) - 1
        return _plan(reason=f"处理第{len(agent_requests)}个阻塞点")

    outcome = runner.run_runtime_agent_recovery(
        agent=fake_agent,
        max_attempts=2,
        run_id="run-test",
        app_config=config,
        output=tmp_path,
        sheet_name="模块A",
        row=2,
        case={"case_name": "查看行情"},
        case_plan=plan,
        session=runner.ModuleSession(),
        setup_events=[],
        action_events=[],
        setup_ok=True,
        action_ok=False,
        action_mode="agent",
        error_detail="第一个弹窗遮挡",
        failure_phase="action",
        action_progress={"failed_index": 1, "retry_safe": True},
        recovery_trace_path=tmp_path / "runtime_recovery_trace.jsonl",
    )

    assert outcome.setup_ok and outcome.action_ok
    assert len(agent_requests) == 2
    assert outcome.attempts[0]["result"] == "replay_failed"
    assert outcome.attempts[1]["result"] == "recovered"
    assert outcome.attempts[1]["diagnosis"] == "popup"
    assert outcome.attempts[1]["recovery_action_count"] == 1
