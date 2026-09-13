import json
import io
import sys
from types import SimpleNamespace

import tools.agent_runtime_recovery_bridge as bridge
from tools.agent_runtime_recovery_bridge import (
    _json_from_text,
    _normalize_recovery_response,
    _prompt,
    _protocol_json,
    _read_request,
    _write_protocol_response,
)


def test_bridge_reads_utf8_bytes_from_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(
        json.dumps({"prompt": "打开行情", "current_state": {"title": "股指"}}, ensure_ascii=False).encode("utf-8")
    ), encoding="cp936"))

    result = _read_request()

    assert result["prompt"] == "打开行情"
    assert result["current_state"]["title"] == "股指"


def test_bridge_protocol_json_is_ascii_and_round_trips_chinese():
    response = {
        "plan_type": "agent_runtime_recovery",
        "decision": "retry_current_action",
        "reason": "关闭开屏广告后重试行情页面",
        "actions": [{"type": "tap_text", "text": "知道了"}],
    }

    payload = _protocol_json(response)

    assert payload.encode("ascii")
    assert "关闭开屏广告" not in payload
    assert json.loads(payload) == response


def test_bridge_protocol_writer_is_safe_with_windows_stdout(monkeypatch):
    response = {
        "decision": "blocked",
        "reason": "页面被开屏广告遮挡",
        "actions": [],
    }
    stdout = type("BinaryStdout", (), {})()
    stdout.buffer = io.BytesIO()
    monkeypatch.setattr(bridge.sys, "stdout", stdout)

    _write_protocol_response(response)

    raw = stdout.buffer.getvalue().rstrip(b"\n")
    assert raw.decode("ascii")
    assert json.loads(raw) == response


def test_bridge_extracts_agent_message_from_cli_jsonl():
    output = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "test"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(
                            {
                                "schema_version": "agent_runtime_recovery@1.0",
                                "plan_type": "runtime_recovery",
                                "decision": "blocked",
                                "reset": {"required": False},
                                "replay_safety": {"retry_safe": False},
                                "actions": [],
                            }
                        ),
                    },
                }
            ),
        ]
    )

    result = _json_from_text(output)

    normalized = _normalize_recovery_response(result)

    assert normalized["schema_version"] == "1.0"
    assert normalized["plan_type"] == "agent_runtime_recovery"
    assert normalized["decision"] == "blocked"
    assert normalized["reset"] == "none"
    assert normalized["replay_safety"] == "uncertain"


def test_bridge_normalizes_safe_recovery_alias_and_coordinate_pairs():
    normalized = _normalize_recovery_response(
        {
            "schema_version": "1.0",
            "plan_type": "agent_runtime_recovery",
            "decision": "recover",
            "replay_safety": {"retry_safe": True},
            "actions": [
                {
                    "type": "swipe",
                    "from": "540,1900",
                    "to": [540, 700],
                }
            ],
        }
    )

    assert normalized["decision"] == "retry_current_action"
    assert normalized["replay_safety"] == "safe"
    assert normalized["actions"][0]["x1"] == 540
    assert normalized["actions"][0]["y1"] == 1900
    assert normalized["actions"][0]["x2"] == 540
    assert normalized["actions"][0]["y2"] == 700


def test_bridge_fails_closed_for_ambiguous_recovery_alias():
    normalized = _normalize_recovery_response(
        {
            "schema_version": "1.0",
            "plan_type": "agent_runtime_recovery",
            "decision": "recover",
            "replay_safety": "uncertain",
            "reason": "无法确认是否可以重试",
            "actions": [{"type": "tap_text", "text": "行情"}],
        }
    )

    assert normalized["decision"] == "blocked"
    assert normalized["actions"] == []
    assert "安全策略阻塞" in normalized["reason"]


def test_bridge_fills_omitted_agent_metadata_from_configuration(monkeypatch):
    monkeypatch.setenv("SIXGILL_RUNTIME_AGENT_NAME", "TestAgent")
    monkeypatch.setenv("SIXGILL_RUNTIME_AGENT_MODEL", "test-model")

    normalized = _normalize_recovery_response(
        {
            "schema_version": "1.0",
            "plan_type": "agent_runtime_recovery",
            "decision": "blocked",
            "reason": "无法安全恢复",
            "actions": [],
        }
    )

    assert normalized["agent"] == {
        "name": "TestAgent",
        "model": "test-model",
        "prompt_version": "agent-recovery-v1",
    }


def test_recovery_prompt_lists_canonical_decisions_and_swipe_shape():
    prompt = _prompt({"prompt": "分析异常", "current_state": {}})

    assert 'decision 只能是 "retry_current_action"、"restart_case" 或 "blocked"' in prompt
    assert '"x1":540,"y1":1900,"x2":540,"y2":700' in prompt


def test_bridge_repairs_invalid_response_once_before_returning(monkeypatch):
    monkeypatch.setenv("SIXGILL_RUNTIME_AGENT_TRANSPORT", "command")
    monkeypatch.setenv("SIXGILL_RUNTIME_AGENT_CLI", "fake-agent")
    outputs = [
        json.dumps(
            {
                "schema_version": "1.0",
                "plan_type": "agent_runtime_recovery",
                "agent": {"name": "test", "model": "m", "prompt_version": "v1"},
                "decision": "unsupported_decision",
                "reason": "第一次输出格式错误",
                "actions": [],
            }
        ),
        json.dumps(
            {
                "schema_version": "1.0",
                "plan_type": "agent_runtime_recovery",
                "agent": {"name": "test", "model": "m", "prompt_version": "v1"},
                "decision": "blocked",
                "reason": "无法确认恢复安全",
                "actions": [],
            }
        ),
    ]
    prompts = []

    def fake_run(_argv, **kwargs):
        prompts.append(kwargs["input"])
        return SimpleNamespace(returncode=0, stdout=outputs.pop(0), stderr="")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    result = bridge._run_agent({"prompt": "分析异常", "current_state": {}})

    assert result["decision"] == "blocked"
    assert len(prompts) == 2
    assert "上一版响应未通过执行器协议校验" in prompts[1]
