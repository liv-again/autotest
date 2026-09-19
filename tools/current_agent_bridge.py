"""Inspect and answer the current-Agent Case Plan file bridge."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 必须是对象: {path}")
    return value


def _safe_response_path(root: Path, value: str) -> Path:
    target = Path(value).expanduser().resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"响应路径不在 bridge 目录内: {target}") from exc
    return target


def inspect_bridge(root: Path, *, full: bool = False) -> int:
    current_path = root / "CURRENT_REQUEST.json"
    if not current_path.is_file():
        print(json.dumps({"status": "idle", "bridge_dir": str(root)}, ensure_ascii=False))
        return 0
    current = _load(current_path)
    request_path = Path(str(current.get("request_path") or ""))
    result: dict[str, Any] = {"current": current}
    if request_path.is_file():
        request = _load(request_path)
        result["request"] = request if full else {
            "request_id": request.get("request_id"),
            "request_type": request.get("request_type"),
            "session_context_path": request.get("session_context_path"),
            "response_path": request.get("response_path"),
            "payload": request.get("payload"),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def submit_plan(root: Path, request_id: str, plan_path: Path) -> int:
    current = _load(root / "CURRENT_REQUEST.json")
    if str(current.get("request_id") or "") != request_id:
        raise ValueError(
            f"当前请求不是 {request_id}，实际为 {current.get('request_id')!r}"
        )
    request_path = Path(str(current.get("request_path") or ""))
    request = _load(request_path)
    if str(request.get("request_id") or "") != request_id:
        raise ValueError("请求文件 request_id 与 CURRENT_REQUEST.json 不一致")
    plan = _load(plan_path)
    response_path = _safe_response_path(root, str(current.get("response_path") or ""))
    temporary = response_path.with_name(f".{response_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(response_path)
    print(json.dumps({"status": "submitted", "request_id": request_id, "response_path": str(response_path)}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="当前 Codex Agent 的 Case Plan 文件桥接工具")
    parser.add_argument("command", choices=("inspect", "submit"))
    parser.add_argument("--bridge-dir", required=True)
    parser.add_argument("--request-id")
    parser.add_argument("--plan")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.bridge_dir).expanduser().resolve()
    if args.command == "inspect":
        return inspect_bridge(root, full=args.full)
    if not args.request_id or not args.plan:
        parser.error("submit 必须同时提供 --request-id 和 --plan")
    return submit_plan(root, args.request_id, Path(args.plan).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
