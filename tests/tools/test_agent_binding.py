from tools.agent_binding import (
    resolve_agent_binding,
    reviewer_default_from_document,
)


def test_planner_and_reviewer_inherit_planner_by_default():
    plan = {
        "planner": {
            "agent": "Trae",
            "model": "model-a",
            "prompt_version": "planner-v1",
        }
    }

    binding = resolve_agent_binding(plan, environment={})

    assert set(binding) == {"schema_version", "default", "planner", "reviewer"}
    assert binding["planner"]["agent"] == "Trae"
    assert binding["planner"]["model"] == "model-a"
    assert binding["reviewer"]["agent"] == "Trae"
    assert binding["reviewer"]["model"] == "model-a"
    assert binding["reviewer"]["prompt_version"] == "row-review-v1"


def test_reviewer_environment_overrides_are_preserved():
    plan = {"planner": {"agent": "Trae", "model": "model-a", "prompt_version": "planner-v1"}}
    environment = {
        "SIXGILL_AGENT_NAME": "Claude",
        "SIXGILL_AGENT_MODEL": "claude-model",
    }

    binding = resolve_agent_binding(plan, environment=environment)

    assert binding["planner"]["agent"] == "Trae"
    assert binding["planner"]["model"] == "model-a"
    assert binding["reviewer"]["agent"] == "Claude"
    assert binding["reviewer"]["model"] == "claude-model"
    assert binding["reviewer"]["source"] == "explicit_environment"


def test_reviewer_default_can_be_read_from_manifest():
    document = {
        "execution_manifest": {
            "agent_binding": {
                "reviewer": {"agent": "Codex", "model": "gpt-5"}
            }
        }
    }

    assert reviewer_default_from_document(document) == {
        "agent": "Codex",
        "model": "gpt-5",
    }
