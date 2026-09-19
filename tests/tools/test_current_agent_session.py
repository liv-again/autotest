import json
import threading
import time

from tools.current_agent_session import CurrentAgentSession


def test_current_agent_session_writes_one_request_and_reads_one_plan(tmp_path):
    bridge = tmp_path / "bridge"
    session = CurrentAgentSession(
        {
            "agent": "Codex",
            "model": "current-model",
            "prompt_version": "current-agent-case-plan-v1",
        },
        initial_context={"queue": "queue-1"},
        bridge_dir=bridge,
        timeout_seconds=3,
        poll_seconds=0.01,
    )

    def respond() -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            request_files = list((bridge / "requests").glob("*.json"))
            if request_files:
                request = json.loads(request_files[0].read_text(encoding="utf-8"))
                response_path = bridge / "responses" / request["request_id"]
                response_path = response_path.with_suffix(".json")
                response_path.write_text(
                    json.dumps(
                        {
                            "plan": {
                                "schema_version": "1.0",
                                "request_type": "llm_retest_plan",
                                "case_id": "股指-row-001",
                            }
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return
            time.sleep(0.01)
        raise AssertionError("current Agent request was not written")

    thread = threading.Thread(target=respond)
    thread.start()
    result = session.request({"case": {"case_id": "股指-row-001"}})
    thread.join(timeout=2)

    assert result["case_id"] == "股指-row-001"
    assert session.binding["session_mode"] == "current_agent_inline"
    assert session.binding["session_policy"] == "current_thread"
    assert (bridge / "session_context.json").is_file()
    assert json.loads((bridge / "CURRENT_REQUEST.json").read_text(encoding="utf-8"))["status"] == "received"

