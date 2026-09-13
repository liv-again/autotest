"""Provider-neutral stdio bridge for row-scoped runtime Agent recovery.

The executor sends one JSON request on stdin and requires one JSON recovery
plan on stdout.  The default transport is the Codex Desktop app-server that
ships with the desktop installation.  A generic command transport remains
available only when ``SIXGILL_RUNTIME_AGENT_TRANSPORT=command`` is set
explicitly; the bridge never silently falls back to an npm/CLI executable.
Human-readable diagnostics are sent to stderr only.
"""

from __future__ import annotations

from collections import deque
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.agent_contract import recovery_contract_instructions  # noqa: E402
from tools.agent_recovery import (  # noqa: E402 - path is set for script mode
    AgentRecoveryError,
    normalize_recovery_response,
    validate_recovery_plan,
)


DEFAULT_TRANSPORT = "desktop_app_server"
DEFAULT_AGENT_NAME = "Codex Desktop"
DEFAULT_MODEL_LABEL = "desktop-default"
DEFAULT_COMMAND_MODEL = "gpt-5.5"
DEFAULT_COMMAND_REASONING = "xhigh"
PROMPT_VERSION = "agent-recovery-v1"
APP_SERVER_CLIENT = {
    "name": "sixgill_runtime_recovery",
    "title": "Sixgill Runtime Recovery",
    "version": "1.0.0",
}


def _write_utf8_line(stream: Any, value: Any) -> None:
    """Write a diagnostic line without relying on the Windows code page."""

    text = f"{value}\n"
    binary = getattr(stream, "buffer", None)
    if binary is not None:
        binary.write(text.encode("utf-8", errors="replace"))
        binary.flush()
        return
    stream.write(text)
    flush = getattr(stream, "flush", None)
    if flush:
        flush()


def _protocol_json(value: Mapping[str, Any]) -> str:
    """Serialize the stdio response as ASCII-only JSON.

    The parent process decodes the bridge stdout as UTF-8.  A Windows Python
    child otherwise writes ``sys.stdout`` using the active GBK code page when
    stdout is redirected, which turns Chinese action text into replacement
    characters before the parent can parse the JSON.  JSON Unicode escapes
    keep the wire format independent of the console encoding while
    ``json.loads`` restores the original text at the parent boundary.
    """

    return json.dumps(dict(value), ensure_ascii=True, separators=(",", ":"))


def _write_protocol_response(value: Mapping[str, Any]) -> None:
    """Write one protocol response line using an encoding-safe byte path."""

    payload = _protocol_json(value) + "\n"
    binary = getattr(sys.stdout, "buffer", None)
    if binary is not None:
        binary.write(payload.encode("ascii"))
        binary.flush()
        return
    # StringIO/capture streams used by tests may not expose ``buffer``.
    sys.stdout.write(payload)
    sys.stdout.flush()


def _read_request() -> dict[str, Any]:
    try:
        # The parent executor writes UTF-8 to a pipe, while Windows may expose
        # a locale-dependent TextIOWrapper for the child process.  Read the
        # underlying bytes and decode explicitly so Chinese UI text cannot
        # corrupt the JSON stream before the runtime Agent sees it.
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig")
        request = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"无法读取运行时恢复请求: {exc}") from exc
    if not isinstance(request, dict):
        raise ValueError("运行时恢复请求必须是 JSON 对象")
    return request


def _env_text(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _desktop_codex_binary() -> str:
    """Locate the native Codex binary shipped with the desktop installation.

    Do not use ``shutil.which('codex')`` here.  On Windows that normally finds
    the npm shim, which exposes ``codex exec`` and is not the desktop bridge
    requested by the runtime recovery workflow.
    """

    configured = _env_text("SIXGILL_DESKTOP_CODEX_BIN")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise ValueError(f"SIXGILL_DESKTOP_CODEX_BIN 不存在: {path}")
        return str(path.resolve())

    if os.name != "nt":
        raise ValueError(
            "未配置 SIXGILL_DESKTOP_CODEX_BIN；桌面版 Codex 原生 app-server 路径无法自动确定"
        )

    local_app_data = _env_text("LOCALAPPDATA")
    if not local_app_data:
        raise ValueError("未找到 LOCALAPPDATA，无法定位桌面版 Codex")
    root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    candidates = [path for path in root.rglob("codex.exe") if path.is_file()]
    if not candidates:
        raise ValueError(
            "未找到桌面版 Codex 原生 app-server；请设置 SIXGILL_DESKTOP_CODEX_BIN"
        )
    return str(max(candidates, key=lambda path: path.stat().st_mtime).resolve())


def _command_argv() -> list[str]:
    """Parse an explicitly configured generic command transport."""

    configured = _env_text("SIXGILL_RUNTIME_AGENT_CLI")
    if not configured:
        raise ValueError(
            "command 传输必须显式设置 SIXGILL_RUNTIME_AGENT_CLI；"
            "默认恢复通道是桌面版 Codex app-server"
        )
    try:
        argv = shlex.split(configured, posix=os.name != "nt")
    except ValueError as exc:
        raise ValueError(f"运行时 Agent 命令解析失败: {exc}") from exc
    if os.name == "nt":
        argv = [
            part[1:-1]
            if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}
            else part
            for part in argv
        ]
    if not argv:
        raise ValueError("运行时 Agent 命令不能为空")
    return argv


def _json_from_text(text: str) -> dict[str, Any] | None:
    """Extract the final recovery object from app-server JSONL or CLI output."""

    def is_recovery_candidate(value: Any) -> bool:
        # Event envelopes do not have ``decision``.  Accepting this field
        # lets the normalizer handle harmless schema spelling variants while
        # ignoring thread/turn/usage events.
        return isinstance(value, dict) and "decision" in value

    candidates: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            if event.get("decision") and event.get("plan_type"):
                candidates.append(json.dumps(event, ensure_ascii=False))
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") in {
                "agent_message",
                "agentMessage",
            }:
                message = item.get("text")
                if isinstance(message, str):
                    candidates.append(message)
            if event.get("type") in {"agent_message", "agentMessage"}:
                message = event.get("text")
                if isinstance(message, str):
                    candidates.append(message)
    candidates.append(text.strip())

    decoder = json.JSONDecoder()
    for candidate in reversed(candidates):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            value = None
        if is_recovery_candidate(value):
            return value
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if is_recovery_candidate(value):
                return value
    return None


def _normalize_recovery_response(response: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper kept for callers and bridge unit tests."""

    return normalize_recovery_response(response)


def _prompt(
    request: dict[str, Any],
    *,
    validation_error: str = "",
    agent_name: str | None = None,
    model_label: str | None = None,
) -> str:
    original_prompt = request.get("prompt") or ""
    request_json = json.dumps(request, ensure_ascii=False, indent=2)
    contract = recovery_contract_instructions()
    repair = ""
    if validation_error:
        repair = f"""
上一版响应未通过执行器协议校验，具体错误是：{validation_error}
请只修正上一版 JSON 的协议问题并重新输出完整对象。不要改变已经确认的诊断事实，
不要输出 recover/retry 等别名，也不要输出自然语言动作。
"""
    actual_agent = agent_name or _env_text("SIXGILL_RUNTIME_AGENT_NAME", DEFAULT_AGENT_NAME)
    actual_model = model_label or _env_text("SIXGILL_RUNTIME_AGENT_MODEL", DEFAULT_MODEL_LABEL)
    transport = _env_text("SIXGILL_RUNTIME_AGENT_TRANSPORT", DEFAULT_TRANSPORT)
    return f"""{original_prompt}

你必须只输出一个合法 JSON 对象，不要输出 Markdown、代码围栏、解释文字或其他内容。
不要修改任何文件，不要执行设备操作，不要调用 shell；只根据下面的运行时请求分析并返回恢复计划。
{contract}
本次实际 Agent 名称为 {actual_agent}，模型为 {actual_model}，传输为 {transport}，prompt_version 为 {PROMPT_VERSION}。
{repair}

运行时请求 JSON：
{request_json}
"""


def _recovery_output_schema() -> dict[str, Any]:
    """Keep the app-server structured-output request intentionally simple."""

    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string"},
            "plan_type": {"type": "string"},
            "agent": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "model": {"type": "string"},
                    "prompt_version": {"type": "string"},
                },
                "required": ["name", "model", "prompt_version"],
                "additionalProperties": False,
            },
            "decision": {"type": "string"},
            "reset": {"type": "string"},
            "replay_safety": {"type": "string"},
            "diagnosis": {"type": ["string", "null"]},
            "reason": {"type": "string"},
            # The app-server requires strict object schemas.  Recovery action
            # fields are type-dependent, so represent unused fields as null;
            # the local validator still owns the discriminated-union checks.
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "id": {"type": ["string", "null"]},
                        "text": {"type": ["string", "null"]},
                        "key": {"type": ["string", "null"]},
                        "x": {"type": ["integer", "null"]},
                        "y": {"type": ["integer", "null"]},
                        "x1": {"type": ["integer", "null"]},
                        "y1": {"type": ["integer", "null"]},
                        "x2": {"type": ["integer", "null"]},
                        "y2": {"type": ["integer", "null"]},
                        "duration_ms": {"type": ["integer", "null"]},
                        "orientation": {"type": ["string", "null"]},
                        "seconds": {"type": ["number", "null"]},
                        "target": {"type": ["string", "null"]},
                    },
                    "required": [
                        "type",
                        "id",
                        "text",
                        "key",
                        "x",
                        "y",
                        "x1",
                        "y1",
                        "x2",
                        "y2",
                        "duration_ms",
                        "orientation",
                        "seconds",
                        "target",
                    ],
                    "additionalProperties": False,
                },
            },
            "confidence": {"type": ["number", "null"]},
        },
        "required": [
            "schema_version",
            "plan_type",
            "agent",
            "decision",
            "reset",
            "replay_safety",
            "diagnosis",
            "reason",
            "actions",
            "confidence",
        ],
        "additionalProperties": False,
    }


def _stamp_agent_metadata(
    response: Mapping[str, Any], *, agent_name: str, model_label: str
) -> dict[str, Any]:
    """Make the audit metadata describe the bridge that actually ran."""

    result = dict(response)
    raw_agent = result.get("agent")
    agent = dict(raw_agent) if isinstance(raw_agent, Mapping) else {}
    agent.update(
        {
            "name": agent_name,
            "model": model_label,
            "prompt_version": PROMPT_VERSION,
        }
    )
    result["agent"] = agent
    return result


class _DesktopAppServer:
    """Small JSONL JSON-RPC client for the desktop Codex app-server."""

    def __init__(self, executable: str, *, cwd: str | Path | None = None) -> None:
        argv = [executable, "app-server", "--listen", "stdio://"]
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": str(cwd) if cwd else None,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(argv, **kwargs)
        except OSError as exc:
            raise RuntimeError(f"桌面版 Codex app-server 无法启动: {exc}") from exc
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._pending: deque[dict[str, Any]] = deque()
        self._send_lock = threading.Lock()
        self._next_id = 1
        self._stderr_lines: deque[str] = deque(maxlen=80)
        self._stdout_thread = threading.Thread(
            target=self._read_stdout, name="sixgill-app-server-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name="sixgill-app-server-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        stream = self.process.stdout
        if stream is None:
            self._messages.put({"_sixgill_eof": "stdout unavailable"})
            return
        try:
            for line in stream:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    message = json.loads(stripped)
                except json.JSONDecodeError:
                    message = {"_sixgill_protocol_error": stripped[-2000:]}
                if isinstance(message, dict):
                    self._messages.put(message)
        finally:
            self._messages.put({"_sixgill_eof": "app-server stdout closed"})

    def _read_stderr(self) -> None:
        stream = self.process.stderr
        if stream is None:
            return
        for line in stream:
            stripped = line.strip()
            if stripped:
                self._stderr_lines.append(stripped)

    def _send(self, message: Mapping[str, Any]) -> None:
        if self.process.poll() is not None:
            raise RuntimeError(self._process_error("app-server 已退出"))
        stream = self.process.stdin
        if stream is None:
            raise RuntimeError("桌面版 Codex app-server stdin 不可用")
        payload = json.dumps(dict(message), ensure_ascii=False, separators=(",", ":"))
        with self._send_lock:
            stream.write(payload + "\n")
            stream.flush()

    def send_notification(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self._send({"method": method, "params": dict(params or {})})

    def _process_error(self, prefix: str) -> str:
        stderr = "\n".join(self._stderr_lines)
        if len(stderr) > 4000:
            stderr = stderr[-4000:]
        return f"{prefix}；stderr: {stderr}" if stderr else prefix

    def _next_response_message(self, timeout: float) -> dict[str, Any]:
        try:
            message = self._messages.get(timeout=max(0.01, timeout))
        except queue.Empty as exc:
            raise TimeoutError("等待桌面版 Codex app-server 响应超时") from exc
        if "_sixgill_eof" in message:
            raise RuntimeError(self._process_error(str(message["_sixgill_eof"])))
        if "_sixgill_protocol_error" in message:
            raise RuntimeError(
                f"桌面版 Codex app-server 返回非 JSONL 内容: {message['_sixgill_protocol_error']}"
            )
        return message

    def _next_notification(self, timeout: float) -> dict[str, Any]:
        if self._pending:
            return self._pending.popleft()
        return self._next_response_message(timeout)

    def _respond_to_server_request(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        if request_id is None:
            return
        method = str(message.get("method") or "")
        if method.endswith("requestApproval"):
            # Recovery prompts explicitly forbid commands and file changes.
            # Declining a server-side approval is safer than hanging the turn.
            self._send({"id": request_id, "result": {"decision": "decline"}})
            return
        self._send(
            {
                "id": request_id,
                "error": {"code": -32601, "message": f"unsupported server request: {method}"},
            }
        )

    def request(
        self, method: str, params: Mapping[str, Any] | None = None, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"method": method, "id": request_id, "params": dict(params or {})})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"等待桌面版 Codex app-server 方法 {method} 响应超时")
            message = self._next_response_message(remaining)
            if message.get("id") == request_id:
                if "error" in message:
                    error = message.get("error") or {}
                    detail = error.get("message") if isinstance(error, Mapping) else error
                    raise RuntimeError(f"桌面版 Codex app-server {method} 失败: {detail}")
                result = message.get("result")
                return dict(result) if isinstance(result, Mapping) else {}
            if "method" in message and "id" in message:
                self._respond_to_server_request(message)
            else:
                self._pending.append(message)

    def wait_for_turn(
        self, thread_id: str, turn_id: str | None, *, timeout: float = 600.0
    ) -> str:
        deadline = time.monotonic() + timeout
        delta_parts: list[str] = []
        final_text = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("等待桌面版 Codex turn 完成超时")
            message = self._next_notification(remaining)
            if "method" in message and "id" in message:
                self._respond_to_server_request(message)
                continue
            method = str(message.get("method") or "")
            params = message.get("params")
            params = params if isinstance(params, Mapping) else {}
            event_thread_id = params.get("threadId")
            if event_thread_id and event_thread_id != thread_id:
                continue

            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if not isinstance(delta, str):
                    delta = params.get("text")
                if isinstance(delta, str):
                    delta_parts.append(delta)
                continue

            if method == "item/completed":
                item = params.get("item")
                if isinstance(item, Mapping) and item.get("type") in {
                    "agentMessage",
                    "agent_message",
                }:
                    item_text = item.get("text")
                    if isinstance(item_text, str):
                        final_text = item_text
                continue

            if method == "turn/completed":
                turn = params.get("turn")
                turn = turn if isinstance(turn, Mapping) else {}
                completed_turn_id = turn.get("id")
                if turn_id and completed_turn_id and completed_turn_id != turn_id:
                    continue
                status = str(turn.get("status") or "")
                if status != "completed":
                    error = turn.get("error") or params.get("error")
                    detail = error.get("message") if isinstance(error, Mapping) else error
                    raise RuntimeError(
                        self._process_error(
                            f"桌面版 Codex turn 未完成，status={status or 'unknown'}，error={detail or '未知'}"
                        )
                    )
                if not final_text:
                    items = turn.get("items")
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, Mapping) and item.get("type") in {
                                "agentMessage",
                                "agent_message",
                            }:
                                item_text = item.get("text")
                                if isinstance(item_text, str):
                                    final_text = item_text
                return final_text or "".join(delta_parts)

    def close(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.process.kill()
                    self.process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    pass


def _desktop_default_model(server: _DesktopAppServer) -> str:
    configured = _env_text("SIXGILL_RUNTIME_AGENT_MODEL")
    if configured:
        return configured
    try:
        result = server.request(
            "model/list", {"limit": 100, "includeHidden": False}, timeout=20.0
        )
    except Exception as exc:  # noqa: BLE001 - the server can still use its configured default
        print(f"桌面版 Codex model/list 未成功，继续使用桌面默认模型: {exc}", file=sys.stderr)
        return ""
    models = result.get("data")
    if not isinstance(models, list):
        return ""
    default_model: str = ""
    fallback_model: str = ""
    for item in models:
        if not isinstance(item, Mapping):
            continue
        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id:
            continue
        modalities = item.get("inputModalities")
        supports_image = not isinstance(modalities, list) or "image" in modalities
        if supports_image and not fallback_model:
            fallback_model = model_id
        if item.get("isDefault") and supports_image:
            default_model = model_id
            break
    return default_model or fallback_model


def _run_desktop_agent(request: dict[str, Any]) -> dict[str, Any]:
    executable = _desktop_codex_binary()
    repo_root = Path(__file__).resolve().parents[1]
    agent_name = _env_text("SIXGILL_RUNTIME_AGENT_NAME", DEFAULT_AGENT_NAME) or DEFAULT_AGENT_NAME
    server = _DesktopAppServer(executable, cwd=repo_root)
    thread_id = ""
    model = ""
    validation_error = ""
    try:
        server.request("initialize", {"clientInfo": APP_SERVER_CLIENT}, timeout=30.0)
        server.send_notification("initialized", {})
        model = _desktop_default_model(server)
        model_label = model or DEFAULT_MODEL_LABEL

        thread_params: dict[str, Any] = {
            "cwd": str(repo_root),
            "approvalPolicy": "never",
            # The wire enum is kebab-case even though the app-server docs
            # describe this setting as ``readOnly`` in prose.
            "sandbox": "read-only",
            "serviceName": "sixgill_runtime_recovery",
        }
        if model:
            thread_params["model"] = model
        thread_result = server.request("thread/start", thread_params, timeout=30.0)
        thread = thread_result.get("thread")
        if not isinstance(thread, Mapping) or not thread.get("id"):
            raise RuntimeError("桌面版 Codex app-server 未返回 thread.id")
        thread_id = str(thread["id"])

        for response_attempt in range(1, 3):
            input_items: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": _prompt(
                        request,
                        validation_error=validation_error,
                        agent_name=agent_name,
                        model_label=model_label,
                    ),
                }
            ]
            screenshot = ((request.get("current_state") or {}).get("screenshot") or "").strip()
            if screenshot and Path(screenshot).is_file():
                input_items.append(
                    {"type": "localImage", "path": str(Path(screenshot).resolve())}
                )

            turn_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": input_items,
                "cwd": str(repo_root),
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "access": {"type": "fullAccess"}},
                "summary": "concise",
                "outputSchema": _recovery_output_schema(),
            }
            if model:
                turn_params["model"] = model
            reasoning = _env_text("SIXGILL_RUNTIME_AGENT_REASONING")
            if reasoning:
                turn_params["effort"] = reasoning

            turn_result = server.request("turn/start", turn_params, timeout=30.0)
            turn = turn_result.get("turn")
            turn_id = str(turn.get("id")) if isinstance(turn, Mapping) and turn.get("id") else None
            raw_text = server.wait_for_turn(thread_id, turn_id, timeout=600.0)
            response = _json_from_text(raw_text)
            if response is None:
                validation_error = "桌面版 Codex app-server 未返回可解析的恢复 JSON"
            else:
                stamped = _stamp_agent_metadata(
                    response, agent_name=agent_name, model_label=model_label
                )
                try:
                    return validate_recovery_plan(stamped)
                except (AgentRecoveryError, ValueError, TypeError) as exc:
                    validation_error = str(exc)
            if response_attempt == 1:
                continue
        raise RuntimeError(f"Agent 恢复响应两次均未通过协议校验：{validation_error}")
    finally:
        if thread_id:
            try:
                server.request("thread/delete", {"threadId": thread_id}, timeout=10.0)
            except Exception as exc:  # noqa: BLE001 - cleanup must not hide the result
                print(f"桌面版 Codex 临时恢复线程清理失败: {exc}", file=sys.stderr)
        server.close()


def _run_command_agent(request: dict[str, Any]) -> dict[str, Any]:
    """Run a generic command transport only when explicitly selected."""

    model = _env_text("SIXGILL_RUNTIME_AGENT_MODEL", DEFAULT_COMMAND_MODEL) or DEFAULT_COMMAND_MODEL
    reasoning = _env_text("SIXGILL_RUNTIME_AGENT_REASONING", DEFAULT_COMMAND_REASONING) or DEFAULT_COMMAND_REASONING
    agent_name = _env_text("SIXGILL_RUNTIME_AGENT_NAME", "外部 Agent") or "外部 Agent"
    argv = _command_argv()
    argv.extend(
        [
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--config",
            'approval_policy="never"',
            "--config",
            f'model_reasoning_effort="{reasoning}"',
            "-m",
            model,
            "--json",
        ]
    )
    screenshot = ((request.get("current_state") or {}).get("screenshot") or "").strip()
    if screenshot and Path(screenshot).is_file():
        argv.extend(["-i", screenshot])
    argv.append("-")

    validation_error = ""
    for response_attempt in range(1, 3):
        completed = subprocess.run(
            argv,
            input=_prompt(
                request,
                validation_error=validation_error,
                agent_name=agent_name,
                model_label=model,
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            if len(stderr) > 3000:
                stderr = stderr[-3000:]
            raise RuntimeError(stderr or f"Agent 命令退出码={completed.returncode}")
        response = _json_from_text(completed.stdout or "")
        if response is None:
            validation_error = "Agent 命令未返回可解析的恢复 JSON"
        else:
            try:
                return validate_recovery_plan(response)
            except (AgentRecoveryError, ValueError, TypeError) as exc:
                validation_error = str(exc)
        if response_attempt == 1:
            continue
    raise RuntimeError(f"Agent 恢复响应两次均未通过协议校验：{validation_error}")


def _run_agent(request: dict[str, Any]) -> dict[str, Any]:
    transport = _env_text("SIXGILL_RUNTIME_AGENT_TRANSPORT", DEFAULT_TRANSPORT).casefold()
    if transport in {"desktop_app_server", "desktop", "app_server"}:
        return _run_desktop_agent(request)
    if transport in {"command", "cli", "stdio_command"}:
        return _run_command_agent(request)
    raise ValueError(
        f"不支持的 SIXGILL_RUNTIME_AGENT_TRANSPORT={transport!r}；"
        "允许 desktop_app_server 或显式 command"
    )


def main() -> int:
    try:
        response = _run_agent(_read_request())
    except Exception as exc:  # noqa: BLE001 - bridge must report through stdio
        _write_utf8_line(sys.stderr, str(exc))
        return 1
    _write_protocol_response(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
