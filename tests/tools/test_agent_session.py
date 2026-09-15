import pytest

from tools.agent_session import (
    AgentSessionError,
    create_agent_session,
    register_agent_session_factory,
)


class _FakeSession:
    def __init__(self, binding, context):
        self.binding = binding
        self.context = context

    def request(self, payload):
        return {
            "schema_version": "1.0",
            "case_id": payload.get("case", {}).get("case_id"),
        }


def test_planner_inherited_binding_uses_distinct_fresh_sessions():
    created = []

    def factory(binding, context):
        session = _FakeSession(binding, context)
        created.append(session)
        return session

    register_agent_session_factory(factory)
    try:
        binding = {
            "role": "retester",
            "agent": "Trae",
            "model": "model-a",
            "prompt_version": "row-retest-v1",
        }
        first = create_agent_session(binding, initial_context={"case_id": "row-1"})
        second = create_agent_session(binding, initial_context={"case_id": "row-2"})

        assert first.binding["agent"] == second.binding["agent"] == "Trae"
        assert first.binding["model"] == second.binding["model"] == "model-a"
        assert first.binding["session_id"] != second.binding["session_id"]
        assert created[0].context == {"case_id": "row-1"}
        assert created[1].context == {"case_id": "row-2"}
    finally:
        register_agent_session_factory(None)


def test_missing_desktop_session_factory_fails_closed():
    register_agent_session_factory(None)
    with pytest.raises(AgentSessionError, match="session factory"):
        create_agent_session(
            {
                "role": "retester",
                "agent": "Trae",
                "model": "model-a",
                "prompt_version": "row-retest-v1",
            },
            environment={},
        )
