"""Inline bridge for reusing the current Codex Agent as a retest planner.

The normal ``AgentSession`` boundary is provider-neutral and expects a host
factory.  The current Codex conversation is not callable from a Python
subprocess, so this module provides an explicit file handoff instead:

* the runner writes one request for one Case;
* the current Agent writes one complete Runner Case Plan response;
* the runner validates that response and continues execution.

This is deliberately not a child Agent or a provider session.  The bridge is
only a durable, auditable handoff between the current Agent and the Runner.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


class CurrentAgentBridgeError(RuntimeError):
    """Raised when the current-Agent handoff cannot be completed safely."""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class CurrentAgentSession:
    """Queue-level inline planner handoff owned by the current Agent."""

    def __init__(
        self,
        binding: Mapping[str, Any],
        *,
        initial_context: Mapping[str, Any] | None,
        bridge_dir: str | Path,
        timeout_seconds: float = 1800.0,
        poll_seconds: float = 0.25,
    ) -> None:
        self._root = Path(bridge_dir).expanduser().resolve()
        self._requests = self._root / "requests"
        self._responses = self._root / "responses"
        self._root.mkdir(parents=True, exist_ok=True)
        self._requests.mkdir(parents=True, exist_ok=True)
        self._responses.mkdir(parents=True, exist_ok=True)
        self._timeout_seconds = float(timeout_seconds)
        self._poll_seconds = max(0.05, float(poll_seconds))
        if self._timeout_seconds <= 0:
            raise ValueError("current Agent bridge timeout 必须大于 0")

        agent = (
            _text(os.environ.get("SIXGILL_CURRENT_AGENT_NAME"))
            or _text(binding.get("agent"))
            or "Codex"
        )
        model = (
            _text(os.environ.get("SIXGILL_CURRENT_AGENT_MODEL"))
            or _text(binding.get("model"))
            or "current-agent"
        )
        if agent == "configured-agent":
            agent = "Codex"
        if model == "configured-model":
            model = "current-agent"
        prompt_version = _text(binding.get("prompt_version")) or "current-agent-case-plan-v1"
        logical_session_id = (
            _text(os.environ.get("SIXGILL_CURRENT_AGENT_SESSION_ID"))
            or _text(binding.get("session_id"))
            or "current-codex-agent"
        )
        self._binding = {
            "role": "retester",
            "agent": agent,
            "model": model,
            "prompt_version": prompt_version,
            "session_id": logical_session_id,
            "source": "current_codex_agent",
            "session_policy": "current_thread",
            "session_mode": "current_agent_inline",
            "bridge_dir": str(self._root),
        }
        self._sequence = 0
        self._context_path = self._root / "session_context.json"
        if not self._context_path.exists():
            _write_json(
                self._context_path,
                {
                    "schema_version": "1.0",
                    "request_type": "current_agent_session_init",
                    "session": self._binding,
                    "context": dict(initial_context or {}),
                },
            )
        _write_json(
            self._root / "session_manifest.json",
            {
                "schema_version": "1.0",
                "mode": "current_agent_inline",
                "session": self._binding,
                "context_path": str(self._context_path),
                "protocol": {
                    "request": "requests/<request_id>.json",
                    "response": "responses/<request_id>.json",
                    "response_body": "a complete Runner Case Plan, or {\"plan\": <plan>}",
                },
            },
        )

    @property
    def binding(self) -> dict[str, Any]:
        return dict(self._binding)

    def request(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self._sequence += 1
        case = payload.get("case") if isinstance(payload, Mapping) else None
        case_id = _text(case.get("case_id") if isinstance(case, Mapping) else "") or "case"
        safe_case_id = "".join(
            char if char.isalnum() or char in {"-", "_"} else "_" for char in case_id
        )
        request_id = f"{self._sequence:04d}-{safe_case_id}-{uuid.uuid4().hex[:8]}"
        request_path = self._requests / f"{request_id}.json"
        response_path = self._responses / f"{request_id}.json"
        request_document = {
            "schema_version": "1.0",
            "request_type": "current_agent_case_plan",
            "request_id": request_id,
            "session": self._binding,
            "session_context_path": str(self._context_path),
            "response_path": str(response_path),
            "payload": dict(payload),
        }
        _write_json(request_path, request_document)
        _write_json(
            self._root / "CURRENT_REQUEST.json",
            {
                "request_id": request_id,
                "request_path": str(request_path),
                "response_path": str(response_path),
                "case_id": case_id,
                "status": "waiting_for_current_agent",
            },
        )
        print(
            f"CURRENT_AGENT_PLAN_REQUEST case_id={case_id} "
            f"request={request_path} response={response_path}",
            flush=True,
        )

        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            if response_path.is_file():
                try:
                    response = json.loads(response_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CurrentAgentBridgeError(
                        f"当前 Agent Plan 响应无法读取: {response_path}: {exc}"
                    ) from exc
                if not isinstance(response, Mapping):
                    raise CurrentAgentBridgeError("当前 Agent Plan 响应必须是 JSON 对象")
                plan = response.get("plan")
                if isinstance(plan, Mapping):
                    response = dict(plan)
                _write_json(
                    self._root / "CURRENT_REQUEST.json",
                    {
                        "request_id": request_id,
                        "request_path": str(request_path),
                        "response_path": str(response_path),
                        "case_id": case_id,
                        "status": "received",
                    },
                )
                return dict(response)
            time.sleep(self._poll_seconds)
        raise CurrentAgentBridgeError(
            f"等待当前 Agent 生成 Case Plan 超时（{self._timeout_seconds:g}s）: {request_path}"
        )

    def close(self) -> None:
        _write_json(
            self._root / "session_closed.json",
            {
                "session": self._binding,
                "sequence": self._sequence,
                "closed_at": time.time(),
            },
        )
