"""Agent-neutral runtime recovery handoff.

The normal execution path uses a plan generated before the run.  This module
defines the much smaller contract used when the live UI no longer matches that
plan.  The executor sends a JSON request to an externally configured Agent
command through stdin and accepts only a validated, low-level recovery plan on
stdout. Once invoked, the handoff is scoped to the current Excel row and can
contain multiple bounded turns.

Keeping the transport here deliberately provider-neutral means the caller can
use Codex, OpenCode, Trae, Claude, or a local test double without changing the
execution core.
"""

from __future__ import annotations

import json
import locale
import math
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.agent_contract import (
    MAX_RECOVERY_ACTIONS,
    MAX_RECOVERY_REASON_LENGTH,
    RECOVERY_DECISIONS,
    RECOVERY_PLAN_TYPE,
    RECOVERY_REPLAY_SAFETY,
    RECOVERY_RESET_MODES,
    RECOVERY_SCHEMA_VERSION,
    recovery_contract_instructions,
)
from tools.agent_plan import AgentPlanError, validate_action


class AgentRecoveryError(ValueError):
    """Raised when an Agent transport or recovery response is invalid."""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _require_text(value: Any, label: str, *, max_length: int | None = None) -> str:
    result = _text(value)
    if not result:
        raise AgentRecoveryError(f"{label} 不能为空")
    if max_length is not None and len(result) > max_length:
        raise AgentRecoveryError(f"{label} 长度不能超过 {max_length}")
    return result


_COORDINATE_PAIR_RE = re.compile(
    r"^\(?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[,，]\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*\)?$"
)


def _integer_coordinate(value: Any) -> int | None:
    """Convert only an unambiguous integer coordinate; preserve bad values."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except (TypeError, ValueError):
            return None
        return int(parsed) if math.isfinite(parsed) and parsed.is_integer() else None
    return None


def _coordinate_pair(value: Any) -> tuple[int, int] | None:
    """Read a coordinate pair from common, unambiguous Agent spellings."""

    x: Any = None
    y: Any = None
    if isinstance(value, Mapping):
        for key in ("x", "left"):
            if key in value:
                x = value[key]
                break
        for key in ("y", "top"):
            if key in value:
                y = value[key]
                break
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = value
    elif isinstance(value, str):
        match = _COORDINATE_PAIR_RE.fullmatch(value.strip())
        if match:
            x, y = match.groups()
    if x is None or y is None:
        return None
    x_number = _integer_coordinate(x)
    y_number = _integer_coordinate(y)
    if x_number is None or y_number is None:
        return None
    return x_number, y_number


def _first_present(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _normalize_recovery_action(action: Any) -> Any:
    """Canonicalize safe coordinate spellings before strict validation."""

    if not isinstance(action, Mapping):
        return action
    result = dict(action)
    params = result.get("params")
    if isinstance(params, Mapping):
        for key, value in params.items():
            result.setdefault(key, value)
        result.pop("params", None)

    action_type = str(result.get("type") or "").strip()
    if action_type == "swipe":
        start = _first_present(
            result,
            ("from", "start", "start_point", "from_point", "start_coordinate"),
        )
        end = _first_present(
            result,
            ("to", "end", "end_point", "to_point", "end_coordinate"),
        )
        start_pair = _coordinate_pair(start)
        end_pair = _coordinate_pair(end)
        if start_pair and end_pair:
            result.update(
                {
                    "x1": start_pair[0],
                    "y1": start_pair[1],
                    "x2": end_pair[0],
                    "y2": end_pair[1],
                }
            )
        else:
            coordinates = result.get("coordinates")
            if isinstance(coordinates, (list, tuple)) and len(coordinates) == 4:
                values = [_integer_coordinate(item) for item in coordinates]
                if all(item is not None for item in values):
                    result.update(
                        {
                            "x1": values[0],
                            "y1": values[1],
                            "x2": values[2],
                            "y2": values[3],
                        }
                    )
            else:
                # Some Agents emit x1="540,1900" and x2="540,700".
                first_pair = _coordinate_pair(result.get("x1"))
                second_pair = _coordinate_pair(result.get("x2"))
                if first_pair and second_pair:
                    result.update(
                        {
                            "x1": first_pair[0],
                            "y1": first_pair[1],
                            "x2": second_pair[0],
                            "y2": second_pair[1],
                        }
                    )
        for key in (
            "from",
            "start",
            "start_point",
            "from_point",
            "start_coordinate",
            "to",
            "end",
            "end_point",
            "to_point",
            "end_coordinate",
            "coordinates",
        ):
            result.pop(key, None)
        for key in ("x1", "y1", "x2", "y2"):
            if key in result:
                converted = _integer_coordinate(result[key])
                if converted is not None:
                    result[key] = converted
    elif action_type == "tap_xy":
        if "x" not in result or "y" not in result:
            pair = _coordinate_pair(
                _first_present(result, ("coordinate", "point", "position"))
            )
            if pair:
                result.update({"x": pair[0], "y": pair[1]})
        for key in ("x", "y"):
            if key in result:
                converted = _integer_coordinate(result[key])
                if converted is not None:
                    result[key] = converted
    elif action_type == "tap_bbox" and "bbox" in result:
        bbox = result.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            values = [_integer_coordinate(item) for item in bbox]
            if all(item is not None for item in values):
                result.update(
                    {"x1": values[0], "y1": values[1], "x2": values[2], "y2": values[3]}
                )
        result.pop("bbox", None)
    return result


def normalize_recovery_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize provider spelling variants without guessing unsafe actions."""

    normalized = dict(response)
    raw_agent = normalized.get("agent")
    if isinstance(raw_agent, Mapping):
        agent = dict(raw_agent)
    elif raw_agent not in (None, ""):
        agent = {"name": raw_agent}
    else:
        agent = {}
    # These values describe the bridge process that is actually invoking the
    # configured Agent. Filling omitted metadata is safe and keeps providers
    # from failing only because a model omitted bookkeeping fields.
    defaults = {
        "name": os.environ.get("SIXGILL_RUNTIME_AGENT_NAME", "当前配置的 Agent").strip()
        or "当前配置的 Agent",
        "model": os.environ.get("SIXGILL_RUNTIME_AGENT_MODEL", "gpt-5.5").strip()
        or "gpt-5.5",
        "prompt_version": "agent-recovery-v1",
    }
    for key, value in defaults.items():
        if not _text(agent.get(key)):
            agent[key] = value
    normalized["agent"] = agent

    if normalized.get("schema_version") == "agent_runtime_recovery@1.0":
        normalized["schema_version"] = RECOVERY_SCHEMA_VERSION
    if normalized.get("plan_type") == "runtime_recovery":
        normalized["plan_type"] = RECOVERY_PLAN_TYPE

    reset = normalized.get("reset", "none")
    if isinstance(reset, Mapping):
        mode = reset.get("mode") or reset.get("value")
        if mode in RECOVERY_RESET_MODES:
            normalized["reset"] = mode
        elif reset.get("required") is False:
            normalized["reset"] = "none"
        else:
            raise ValueError("Agent 返回的 reset 缺少合法 mode")

    replay_safety = normalized.get("replay_safety", "not_applicable")
    if isinstance(replay_safety, Mapping):
        if replay_safety.get("retry_safe") is True:
            normalized["replay_safety"] = "safe"
        elif replay_safety.get("retry_safe") is False:
            normalized["replay_safety"] = "uncertain"
        else:
            value = replay_safety.get("value")
            if value in RECOVERY_REPLAY_SAFETY:
                normalized["replay_safety"] = value
            else:
                raise ValueError("Agent 返回的 replay_safety 缺少合法 value")

    actions = normalized.get("actions")
    if isinstance(actions, list):
        normalized["actions"] = [_normalize_recovery_action(action) for action in actions]

    decision = str(normalized.get("decision") or "").strip().casefold()
    aliases = {
        "retry": "retry_current_action",
        "retry_current": "retry_current_action",
        "retry_current_step": "retry_current_action",
        "重试": "retry_current_action",
        "restart": "restart_case",
        "restart_current_case": "restart_case",
        "重新执行": "restart_case",
    }
    if decision == "recover":
        # ``recover`` is ambiguous. When the Agent explicitly says replay is
        # safe, use the least-replay interpretation. Otherwise fail closed.
        if normalized.get("replay_safety") == "safe":
            aliases["recover"] = "retry_current_action"
        else:
            normalized["decision"] = "blocked"
            normalized["actions"] = []
            normalized["reason"] = (
                _text(normalized.get("reason"))
                + "；Agent 使用了含义不明确的 recover 且未确认重放安全，已按安全策略阻塞"
            ).strip("；")
    if decision in aliases:
        normalized["decision"] = aliases[decision]

    return normalized


def _command_argv(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        try:
            # ``posix=False`` preserves Windows drive-letter backslashes.  It
            # leaves surrounding quotes in tokens, so strip only one matching
            # pair after tokenisation.
            parts = shlex.split(command, posix=os.name != "nt")
        except ValueError as exc:
            raise AgentRecoveryError(f"Agent 命令解析失败: {exc}") from exc
        if os.name == "nt":
            parts = [
                part[1:-1]
                if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}
                else part
                for part in parts
            ]
    else:
        parts = [str(item) for item in command]
    parts = [item for item in parts if item]
    if not parts:
        raise AgentRecoveryError("Agent 命令不能为空")
    return parts


def _decode_process_output(value: bytes | str | None, *, stream_name: str) -> str:
    """Decode Agent process output without silently inserting U+FFFD.

    The stdio contract is UTF-8.  Windows Python helpers launched without an
    explicit stdio configuration can nevertheless emit the active GBK code
    page, so accept that legacy encoding as a compatibility fallback after a
    strict UTF-8 attempt.  Any other undecodable output fails closed instead
    of allowing replacement characters into the recovery trace.
    """

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not value:
        return ""

    encodings = ["utf-8-sig"]
    preferred = locale.getpreferredencoding(False)
    if preferred and preferred.casefold().replace("-", "") not in {
        "utf8",
        "utf8sig",
    }:
        encodings.append(preferred)
    for encoding in encodings:
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AgentRecoveryError(
        f"Agent {stream_name} 不是合法 UTF-8；已拒绝写入乱码恢复轨迹"
    )


class CommandRecoveryAgent:
    """Invoke a configured Agent command using a strict JSON stdio contract.

    The command receives one JSON request on stdin and must print exactly one
    JSON object to stdout.  Human-readable logs belong on stderr so the
    executor can safely parse the response.
    """

    def __init__(
        self,
        command: str | Sequence[str],
        *,
        timeout_seconds: float = 120.0,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.argv = _command_argv(command)
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise AgentRecoveryError("Agent 超时时间必须是数字") from exc
        if not 1 <= timeout <= 600:
            raise AgentRecoveryError("Agent 超时时间必须在 1..600 秒")
        self.timeout_seconds = timeout
        self.cwd = str(Path(cwd).expanduser().resolve()) if cwd else None
        self.env = dict(env) if env is not None else None

    @property
    def command_label(self) -> str:
        """Return a safe audit label without exposing command arguments."""

        return self.argv[0]

    def __call__(self, request: Mapping[str, Any]) -> dict[str, Any]:
        # Keep the subprocess protocol ASCII-only.  The request contains UI
        # trees and Chinese labels; escaping them here avoids any Windows
        # locale/console encoding ambiguity on the child stdin pipe.
        payload = json.dumps(dict(request), ensure_ascii=True, separators=(",", ":"))
        try:
            completed = subprocess.run(
                self.argv,
                input=(payload + "\n").encode("utf-8"),
                capture_output=True,
                cwd=self.cwd,
                env=self.env,
                text=False,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentRecoveryError(
                f"Agent 调用超时（>{self.timeout_seconds:g}秒）"
            ) from exc
        except OSError as exc:
            raise AgentRecoveryError(f"Agent 命令无法启动: {exc}") from exc

        stderr = _decode_process_output(completed.stderr, stream_name="stderr")
        if completed.returncode != 0:
            stderr = stderr.strip()
            if len(stderr) > 2000:
                stderr = stderr[-2000:]
            detail = stderr or f"进程退出码={completed.returncode}"
            raise AgentRecoveryError(f"Agent 调用失败: {detail}")

        stdout = _decode_process_output(completed.stdout, stream_name="stdout").strip()
        if not stdout:
            raise AgentRecoveryError("Agent 未返回 JSON 恢复计划")
        try:
            response = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise AgentRecoveryError(
                "Agent stdout 不是单个合法 JSON 对象；日志请输出到 stderr"
            ) from exc
        if not isinstance(response, Mapping):
            raise AgentRecoveryError("Agent 恢复响应必须是 JSON 对象")
        return dict(response)


def validate_recovery_plan(plan: Any) -> dict[str, Any]:
    """Validate and normalize an Agent's live recovery response."""

    if not isinstance(plan, Mapping):
        raise AgentRecoveryError("Agent 恢复计划必须是 JSON 对象")
    try:
        plan = normalize_recovery_response(plan)
    except (TypeError, ValueError) as exc:
        raise AgentRecoveryError(str(exc)) from exc
    if _text(plan.get("schema_version")) != RECOVERY_SCHEMA_VERSION:
        raise AgentRecoveryError(
            f"恢复计划 schema_version 必须为 {RECOVERY_SCHEMA_VERSION}"
        )
    if _text(plan.get("plan_type")) != RECOVERY_PLAN_TYPE:
        raise AgentRecoveryError(f"恢复计划 plan_type 必须为 {RECOVERY_PLAN_TYPE}")

    raw_agent = plan.get("agent")
    if isinstance(raw_agent, Mapping):
        agent_name = raw_agent.get("name") or raw_agent.get("agent") or raw_agent.get("provider")
        agent = dict(raw_agent)
    else:
        agent_name = raw_agent
        agent = {}
    agent["name"] = _require_text(agent_name, "recovery.agent.name", max_length=80)
    agent["model"] = _require_text(agent.get("model"), "recovery.agent.model", max_length=160)
    agent["prompt_version"] = _require_text(
        agent.get("prompt_version"), "recovery.agent.prompt_version", max_length=160
    )

    decision = _require_text(plan.get("decision"), "recovery.decision").casefold()
    if decision not in RECOVERY_DECISIONS:
        allowed = ", ".join(sorted(RECOVERY_DECISIONS))
        raise AgentRecoveryError(f"recovery.decision={decision!r} 不受支持；允许: {allowed}")

    reset = _text(plan.get("reset") or "none").casefold()
    if reset not in RECOVERY_RESET_MODES:
        allowed = ", ".join(sorted(RECOVERY_RESET_MODES))
        raise AgentRecoveryError(f"recovery.reset={reset!r} 不受支持；允许: {allowed}")

    replay_safety = _text(plan.get("replay_safety") or "not_applicable").casefold()
    if replay_safety not in RECOVERY_REPLAY_SAFETY:
        allowed = ", ".join(sorted(RECOVERY_REPLAY_SAFETY))
        raise AgentRecoveryError(
            f"recovery.replay_safety={replay_safety!r} 不受支持；允许: {allowed}"
        )

    reason = _require_text(
        plan.get("reason"),
        "recovery.reason",
        max_length=MAX_RECOVERY_REASON_LENGTH,
    )
    diagnosis = _text(plan.get("diagnosis"))
    actions = plan.get("actions", [])
    if not isinstance(actions, list):
        raise AgentRecoveryError("recovery.actions 必须是数组")
    if len(actions) > MAX_RECOVERY_ACTIONS:
        raise AgentRecoveryError(
            f"recovery.actions 最多允许 {MAX_RECOVERY_ACTIONS} 个动作"
        )
    normalized_actions: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        try:
            normalized_actions.append(
                validate_action(action, location=f"recovery.actions[{index}]")
            )
        except AgentPlanError as exc:
            raise AgentRecoveryError(str(exc)) from exc

    if decision != "blocked" and replay_safety != "safe":
        raise AgentRecoveryError(
            "恢复计划要求重试/重跑时，replay_safety 必须明确为 safe；"
            "结果不确定时应返回 blocked"
        )
    if decision == "blocked" and actions:
        raise AgentRecoveryError("blocked 恢复计划不能携带待执行动作")

    confidence = plan.get("confidence")
    normalized_confidence: float | None = None
    if confidence not in (None, ""):
        try:
            normalized_confidence = float(confidence)
        except (TypeError, ValueError) as exc:
            raise AgentRecoveryError("recovery.confidence 必须是数字") from exc
        if not 0 <= normalized_confidence <= 1:
            raise AgentRecoveryError("recovery.confidence 必须在 0..1")

    result: dict[str, Any] = {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "plan_type": RECOVERY_PLAN_TYPE,
        "agent": agent,
        "decision": decision,
        "reset": reset,
        "replay_safety": replay_safety,
        "reason": reason,
        "actions": normalized_actions,
    }
    if diagnosis:
        result["diagnosis"] = diagnosis[:MAX_RECOVERY_REASON_LENGTH]
    if normalized_confidence is not None:
        result["confidence"] = normalized_confidence
    return result


def build_recovery_prompt() -> str:
    """Return the common instruction embedded in every recovery request."""

    return (
        "你是当前选定的 Agent，正在接管一条移动端 Excel 用例的运行时异常。"
        "请根据原始 Excel 用例、原动作计划、当前 UI 树、截图和失败轨迹判断异常。"
        "优先处理临时覆盖层、开屏广告、通知弹窗、页面跑偏、加载超时或 App 崩溃。"
        "进入接管后，你负责这条用例直到它完成或明确阻塞；本次恢复动作成功后，"
        "执行器仍会继续重放原用例剩余动作，若后续又出现新的阻塞点，下一次请求会"
        "带上本次接管历史，你必须继续判断和处理，不能把第一次恢复成功当作整条用例完成。"
        "你只能决定异常恢复和是否安全重试，不能修改原用例预期或绕过目标页门禁。"
        "只能返回结构化 agent_runtime_recovery JSON；actions 只能使用允许的低层动作，"
        "不能返回 shell/ADB/自然语言脚本。若业务动作已经可能产生效果，除非能够明确证明"
        "重试安全，否则 decision 必须为 blocked。reason 必须说明诊断和恢复依据。"
        + "\n\n"
        + recovery_contract_instructions()
    )
