"""Resolve Agent/model bindings for the three execution roles.

The action plan is the canonical source for the logical Agent and model.  The
retester and row-reviewer inherit that binding by default, but each role is
started in a fresh session.  This module records identity/configuration only;
it never starts an Agent transport or imports a provider-specific runtime.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


DEFAULT_REVIEW_NAME = "configured-agent"
DEFAULT_REVIEW_MODEL = "configured-model"
DEFAULT_RETEST_PROMPT_VERSION = "row-retest-v1"
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
    """Resolve planner, retester, and reviewer Agent metadata.

    ``SIXGILL_AGENT_*`` is only a global fallback when the plan does not carry
    the actual planner identity.  Once a plan has an explicit planner, both
    retester and reviewer inherit it by default.  Role-specific overrides are
    opt-in through ``SIXGILL_RETEST_*`` and ``SIXGILL_REVIEW_*``.  The returned
    mapping is safe to persist; it does not contain the process environment or
    a live session handle.
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

    review_agent_override = _env_text(env, "SIXGILL_REVIEW_AGENT_NAME")
    review_model_override = _env_text(env, "SIXGILL_REVIEW_MODEL")
    review_agent = review_agent_override or canonical_agent
    review_model = review_model_override or canonical_model
    review_source = (
        "explicit_environment"
        if review_agent_override or review_model_override
        else canonical_source
    )

    retest_agent_override = _env_text(env, "SIXGILL_RETEST_AGENT_NAME")
    retest_model_override = _env_text(env, "SIXGILL_RETEST_MODEL")
    retest_agent = retest_agent_override or canonical_agent
    retest_model = retest_model_override or canonical_model
    retest_source = (
        "explicit_environment"
        if retest_agent_override or retest_model_override
        else canonical_source
    )

    return {
        "schema_version": "1.0",
        "default": _role(
            canonical_agent,
            canonical_model,
            canonical_prompt,
            source=canonical_source,
            session_policy="new_per_role",
        ),
        "planner": _role(
            planner_agent or canonical_agent,
            planner_model or canonical_model,
            planner_prompt or canonical_prompt,
            source="action_plan.planner" if planner_agent and planner_model else canonical_source,
            session_policy="new_per_module",
        ),
        "retester": _role(
            retest_agent,
            retest_model,
            DEFAULT_RETEST_PROMPT_VERSION,
            source=retest_source,
            session_policy="new_per_case",
        ),
        "reviewer": _role(
            review_agent,
            review_model,
            DEFAULT_REVIEW_PROMPT_VERSION,
            source=review_source,
            session_policy="new_readonly",
            readonly=True,
        ),
    }


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
