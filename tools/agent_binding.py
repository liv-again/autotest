"""Resolve the Agent/model binding shared by one execution run.

The action plan is the canonical source for the logical Agent and model. The
runtime recovery bridge and the row-review queue inherit that binding unless a
role-specific environment override is explicitly supplied. Transport and
prompt versions remain role-specific metadata.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


DEFAULT_RUNTIME_TRANSPORT = "desktop_app_server"
DEFAULT_RUNTIME_NAME = "Codex Desktop"
DEFAULT_RUNTIME_MODEL = "desktop-default"
DEFAULT_REVIEW_NAME = "configured-agent"
DEFAULT_REVIEW_MODEL = "configured-model"
DEFAULT_RECOVERY_PROMPT_VERSION = "agent-recovery-v1"
DEFAULT_REVIEW_PROMPT_VERSION = "row-review-v1"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _env_text(environment: Mapping[str, Any], name: str) -> str:
    return _text(environment.get(name))


def _role(
    agent: str,
    model: str,
    prompt_version: str,
    *,
    source: str,
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "agent": agent,
        "model": model,
        "prompt_version": prompt_version,
        "source": source,
    }
    result.update(extra)
    return result


def resolve_agent_binding(
    action_plan: Mapping[str, Any] | None,
    *,
    environment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve planner, runtime-recovery and reviewer Agent metadata.

    ``SIXGILL_RUNTIME_AGENT_*`` and ``SIXGILL_AGENT_*`` are intentional
    role-specific overrides. When absent, both roles inherit
    ``action_plan.planner``. The returned mapping is safe to persist; it does
    not contain the process environment.
    """

    env: Mapping[str, Any] = environment if environment is not None else os.environ
    planner_raw = action_plan.get("planner") if isinstance(action_plan, Mapping) else None
    planner = planner_raw if isinstance(planner_raw, Mapping) else {}
    planner_agent = _text(planner.get("agent"))
    planner_model = _text(planner.get("model"))
    planner_prompt = _text(planner.get("prompt_version"))

    canonical_agent = planner_agent or _env_text(env, "SIXGILL_AGENT_NAME") or DEFAULT_REVIEW_NAME
    canonical_model = planner_model or _env_text(env, "SIXGILL_AGENT_MODEL") or DEFAULT_REVIEW_MODEL
    canonical_prompt = planner_prompt or "planner-unknown"
    canonical_source = "action_plan.planner" if planner_agent and planner_model else "environment/default"

    runtime_agent_override = _env_text(env, "SIXGILL_RUNTIME_AGENT_NAME")
    runtime_model_override = _env_text(env, "SIXGILL_RUNTIME_AGENT_MODEL")
    runtime_agent = runtime_agent_override or planner_agent or DEFAULT_RUNTIME_NAME
    runtime_model = runtime_model_override or planner_model or DEFAULT_RUNTIME_MODEL
    runtime_source = (
        "explicit_environment"
        if runtime_agent_override or runtime_model_override
        else ("action_plan.planner" if planner_agent and planner_model else "environment/default")
    )
    runtime_transport = _env_text(env, "SIXGILL_RUNTIME_AGENT_TRANSPORT") or DEFAULT_RUNTIME_TRANSPORT

    review_agent_override = _env_text(env, "SIXGILL_AGENT_NAME")
    review_model_override = _env_text(env, "SIXGILL_AGENT_MODEL")
    review_agent = review_agent_override or canonical_agent
    review_model = review_model_override or canonical_model
    review_source = (
        "explicit_environment"
        if review_agent_override or review_model_override
        else canonical_source
    )

    return {
        "schema_version": "1.0",
        "default": _role(
            canonical_agent,
            canonical_model,
            canonical_prompt,
            source=canonical_source,
        ),
        "planner": _role(
            planner_agent or canonical_agent,
            planner_model or canonical_model,
            planner_prompt or canonical_prompt,
            source="action_plan.planner" if planner_agent and planner_model else canonical_source,
        ),
        "runtime_recovery": _role(
            runtime_agent,
            runtime_model,
            DEFAULT_RECOVERY_PROMPT_VERSION,
            source=runtime_source,
            transport=runtime_transport,
        ),
        "reviewer": _role(
            review_agent,
            review_model,
            DEFAULT_REVIEW_PROMPT_VERSION,
            source=review_source,
        ),
    }


def runtime_agent_environment(
    binding: Mapping[str, Any],
    *,
    environment: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Return a child-process environment carrying the resolved runtime binding."""

    base_environment = environment if environment is not None else os.environ
    result = {str(key): str(value) for key, value in base_environment.items()}
    runtime = binding.get("runtime_recovery") if isinstance(binding, Mapping) else None
    if isinstance(runtime, Mapping):
        agent = _text(runtime.get("agent"))
        model = _text(runtime.get("model"))
        if agent:
            result["SIXGILL_RUNTIME_AGENT_NAME"] = agent
        if model:
            result["SIXGILL_RUNTIME_AGENT_MODEL"] = model
        transport = _text(runtime.get("transport"))
        if transport:
            result["SIXGILL_RUNTIME_AGENT_TRANSPORT"] = transport
    return result


def reviewer_default_from_document(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Read the reviewer default from an execution document or review queue."""

    if not isinstance(document, Mapping):
        return None
    direct = document.get("review_agent_default")
    if isinstance(direct, Mapping):
        return dict(direct)
    binding = document.get("agent_binding")
    if isinstance(binding, Mapping):
        reviewer = binding.get("reviewer")
        if isinstance(reviewer, Mapping):
            return dict(reviewer)
    manifest = document.get("execution_manifest")
    if isinstance(manifest, Mapping):
        nested = reviewer_default_from_document(manifest)
        if nested:
            return nested
        agent = _text(manifest.get("planner_agent"))
        model = _text(manifest.get("planner_model"))
        if agent and model:
            return _role(
                agent,
                model,
                DEFAULT_REVIEW_PROMPT_VERSION,
                source="execution_manifest.planner",
            )
    return None
