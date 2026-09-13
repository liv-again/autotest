from tools.agent_binding import (
    resolve_agent_binding,
    reviewer_default_from_document,
    runtime_agent_environment,
)


def test_runtime_and_reviewer_inherit_planner_by_default():
    plan = {
        "planner": {
            "agent": "Trae",
            "model": "model-a",
            "prompt_version": "planner-v1",
        }
    }

    binding = resolve_agent_binding(plan, environment={})

    assert binding["runtime_recovery"]["agent"] == "Trae"
    assert binding["runtime_recovery"]["model"] == "model-a"
    assert binding["reviewer"]["agent"] == "Trae"
    assert binding["reviewer"]["model"] == "model-a"
    assert binding["runtime_recovery"]["prompt_version"] == "agent-recovery-v1"
    assert binding["reviewer"]["prompt_version"] == "row-review-v1"


def test_role_specific_environment_overrides_are_preserved():
    plan = {"planner": {"agent": "Trae", "model": "model-a", "prompt_version": "planner-v1"}}
    environment = {
        "SIXGILL_RUNTIME_AGENT_NAME": "Codex Desktop",
        "SIXGILL_RUNTIME_AGENT_MODEL": "gpt-5.6-luna",
        "SIXGILL_AGENT_NAME": "Claude",
        "SIXGILL_AGENT_MODEL": "claude-model",
    }

    binding = resolve_agent_binding(plan, environment=environment)

    assert binding["runtime_recovery"]["agent"] == "Codex Desktop"
    assert binding["runtime_recovery"]["model"] == "gpt-5.6-luna"
    assert binding["reviewer"]["agent"] == "Claude"
    assert binding["reviewer"]["model"] == "claude-model"
    assert binding["runtime_recovery"]["source"] == "explicit_environment"
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


def test_runtime_environment_contains_resolved_binding():
    binding = resolve_agent_binding(
        {"planner": {"agent": "Trae", "model": "model-a", "prompt_version": "v1"}},
        environment={},
    )

    child_env = runtime_agent_environment(binding, environment={"PATH": "test"})

    assert child_env["PATH"] == "test"
    assert child_env["SIXGILL_RUNTIME_AGENT_NAME"] == "Trae"
    assert child_env["SIXGILL_RUNTIME_AGENT_MODEL"] == "model-a"
