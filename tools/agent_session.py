"""Provider-neutral Agent session boundary used by interactive retesting.

The execution core must not know whether the selected Agent is Codex Desktop,
OpenCode, Trae, Claude, or another host.  A host registers (or configures) a
small Python factory with this contract:

``factory(binding: dict, initial_context: dict) -> session``

The returned session exposes ``request(payload: dict) -> dict`` and may expose
``close()``.  The runner creates a new session for each retested case, while
the binding (Agent name and model) is inherited from the planner by default.
No CLI process, network client, or provider-specific SDK is started here.
"""

from __future__ import annotations

import importlib
import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping


FACTORY_ENV = "SIXGILL_AGENT_SESSION_FACTORY"


class AgentSessionError(RuntimeError):
    """Raised when the host cannot create or use an Agent session."""


@dataclass(frozen=True)
class AgentSessionSpec:
    """Auditable identity of one fresh Agent session."""

    role: str
    agent: str
    model: str
    prompt_version: str
    session_id: str
    source: str = "agent_binding"

    def as_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "agent": self.agent,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "session_id": self.session_id,
            "source": self.source,
            "session_policy": "fresh",
        }


class AgentSessionHandle:
    """Normalize a host-provided session to a small runner-facing API."""

    def __init__(self, raw: Any, spec: AgentSessionSpec) -> None:
        request = getattr(raw, "request", None)
        if not callable(request):
            raise AgentSessionError(
                "Agent session factory 返回对象缺少 request(payload) 方法"
            )
        self._raw = raw
        self.spec = spec

    @property
    def binding(self) -> dict[str, str]:
        return self.spec.as_dict()

    def request(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            value = self._raw.request(dict(payload))
        except Exception as exc:  # pragma: no cover - provider-owned failure
            raise AgentSessionError(f"Agent session request 失败: {exc}") from exc
        if not isinstance(value, Mapping):
            raise AgentSessionError("Agent session 必须返回 JSON 对象")
        return value

    def close(self) -> None:
        closer = getattr(self._raw, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception as exc:  # pragma: no cover - provider-owned failure
                raise AgentSessionError(f"Agent session close 失败: {exc}") from exc


_REGISTERED_FACTORY: Callable[[Mapping[str, Any], Mapping[str, Any]], Any] | None = None


def register_agent_session_factory(
    factory: Callable[[Mapping[str, Any], Mapping[str, Any]], Any] | None,
) -> None:
    """Register an in-process host factory, primarily for desktop hosts/tests."""

    global _REGISTERED_FACTORY
    _REGISTERED_FACTORY = factory


def session_factory_available(environment: Mapping[str, Any] | None = None) -> bool:
    env = environment if environment is not None else os.environ
    return _REGISTERED_FACTORY is not None or bool(str(env.get(FACTORY_ENV) or "").strip())


def _load_factory(reference: str) -> Callable[[Mapping[str, Any], Mapping[str, Any]], Any]:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise AgentSessionError(
            f"{FACTORY_ENV} 必须使用 module:function 格式，当前为 {reference!r}"
        )
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise AgentSessionError(
            f"无法加载 Agent session factory {reference!r}: {exc}"
        ) from exc
    if not callable(factory):
        raise AgentSessionError(f"Agent session factory 不可调用: {reference!r}")
    return factory


def create_agent_session(
    binding: Mapping[str, Any],
    *,
    initial_context: Mapping[str, Any] | None = None,
    environment: Mapping[str, Any] | None = None,
) -> AgentSessionHandle:
    """Create one fresh session for a planner-inherited role.

    A session id is always generated here, even when the host later maps it to
    a desktop thread.  This prevents a retest from silently inheriting the
    planner or another case's conversation.
    """

    role = str(binding.get("role") or "retester").strip()
    agent = str(binding.get("agent") or "").strip()
    model = str(binding.get("model") or "").strip()
    prompt_version = str(binding.get("prompt_version") or "").strip()
    if not agent or not model or not prompt_version:
        raise AgentSessionError("retester binding 必须包含 agent、model、prompt_version")

    spec = AgentSessionSpec(
        role=role,
        agent=agent,
        model=model,
        prompt_version=prompt_version,
        session_id=f"session-{uuid.uuid4().hex}",
        source=str(binding.get("source") or "agent_binding"),
    )
    env = environment if environment is not None else os.environ
    factory = _REGISTERED_FACTORY
    if factory is None:
        reference = str(env.get(FACTORY_ENV) or "").strip()
        if not reference:
            raise AgentSessionError(
                f"未配置桌面 Agent session factory；请设置 {FACTORY_ENV}=module:function，"
                "不能退回旧的 recovery-agent-command/CLI 传输"
            )
        factory = _load_factory(reference)

    try:
        raw = factory(spec.as_dict(), dict(initial_context or {}))
    except Exception as exc:  # pragma: no cover - provider-owned failure
        raise AgentSessionError(f"Agent session factory 创建失败: {exc}") from exc
    return AgentSessionHandle(raw, spec)

