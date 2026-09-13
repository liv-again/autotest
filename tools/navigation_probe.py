"""Build and optionally run a route-level navigation probe.

The probe is deliberately separate from normal regression execution.  It
selects a small set of read-only route canaries, then delegates navigation and
page-gate execution to an app runner with ``--probe``.  The runner skips the
Excel business action, but still captures an independent screenshot and a
row-scoped execution record for every canary.

Examples::

    python tools/navigation_probe.py build \
        --source cases.xlsx \
        --profile apps/guotou/profile.yaml \
        --out runs/navigation-probe/navigation_probe_queue.json

    python tools/navigation_probe.py run \
        --source cases.xlsx \
        --profile apps/guotou/profile.yaml \
        --runner .codex_spreadsheet_work/run_quote_p0.py \
        --action-plan runs/navigation-probe/agent_action_plan.json \
        --output runs/navigation-probe

``--probe`` is opt-in.  A normal invocation of an existing runner is
unchanged, but the runner still requires either an Agent action plan or an
explicit ``--legacy-deterministic`` migration flag.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.module_planner import build_module_plan  # noqa: E402
from tools.app_adapter import AppAdapterError, load_app_adapter, load_app_config  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _case_key(case: dict[str, Any]) -> tuple[str, int]:
    return _text(case.get("sheet")), int(case.get("row"))


def _flatten_cases(plan: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for module in plan.get("modules", []):
        for case in module.get("cases", []):
            if isinstance(case, dict):
                cases.append(case)
    cases.sort(key=lambda item: int(item.get("source_order") or 0))
    return cases


def _priority_rank(case: dict[str, Any]) -> tuple[int, int]:
    priority = _text(case.get("priority")).casefold()
    rank = {"p0": 0, "high": 0, "p1": 1, "medium": 1, "p2": 2, "low": 2}.get(priority, 3)
    return rank, int(case.get("source_order") or 0)


def _route_key(case: dict[str, Any]) -> str:
    """Collapse assertion-only level-4 variants into one route candidate."""

    context = case.get("navigation_context") or {}
    parts = [
        context.get("sheet") or case.get("sheet"),
        context.get("level_1"),
        context.get("level_2"),
        context.get("level_3"),
        context.get("entry"),
    ]
    return "|".join(_text(part).casefold() for part in parts if _text(part))


def _parse_selector(value: str) -> tuple[str, int] | str:
    text = _text(value)
    if "!" in text:
        sheet, row_text = text.rsplit("!", 1)
        try:
            return _text(sheet), int(row_text)
        except ValueError as exc:
            raise ValueError(f"用例选择器行号非法: {value}") from exc
    return text


def _resolve_explicit(cases: Iterable[dict[str, Any]], selectors: Iterable[str]) -> list[dict[str, Any]]:
    by_key = {_case_key(case): case for case in cases}
    by_id = {_text(case.get("case_id")): case for case in cases}
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in selectors:
        selector = _parse_selector(raw)
        case = by_key.get(selector) if isinstance(selector, tuple) else by_id.get(selector)
        if case is None:
            raise ValueError(f"选择器未匹配到 Excel 用例: {raw}")
        key = _case_key(case)
        if key in seen:
            raise ValueError(f"探测队列包含重复用例: {key[0]}!{key[1]}")
        seen.add(key)
        selected.append(case)
    return selected


def _generic_route_canaries(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick one safe-looking row per high-level route for other apps.

    App-specific adapters should normally provide explicit ``--case`` values.
    The generic fallback is intentionally conservative and only deduplicates
    by the first three navigation levels plus entry.
    """

    candidates: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        candidates.setdefault(_route_key(case), []).append(case)
    selected: list[dict[str, Any]] = []
    for route_cases in candidates.values():
        selected.append(sorted(route_cases, key=_priority_rank)[0])
    selected.sort(key=lambda item: int(item.get("source_order") or 0))
    return selected


def select_probe_cases(
    plan: dict[str, Any],
    *,
    app_slug: str = "",
    selectors: Iterable[str] = (),
    route_canaries: Iterable[tuple[str, int]] = (),
) -> list[dict[str, Any]]:
    cases = _flatten_cases(plan)
    if selectors:
        selected = _resolve_explicit(cases, selectors)
    elif route_canaries:
        selected = _resolve_explicit(
            cases,
            [f"{sheet}!{row}" for sheet, row in route_canaries],
        )
    else:
        selected = _generic_route_canaries(cases)

    selected.sort(key=lambda item: int(item.get("source_order") or 0))
    return selected


def build_probe_queue(
    source: str | Path,
    *,
    profile: str | Path | None = None,
    app_slug: str = "",
    selectors: Iterable[str] = (),
) -> dict[str, Any]:
    plan = build_module_plan(source, profile_path=profile)
    resolved_slug = app_slug or _text(plan.get("app_profile_context", {}).get("slug"))
    route_canaries: tuple[tuple[str, int], ...] = ()
    if resolved_slug:
        try:
            config = load_app_config(PROJECT_ROOT, resolved_slug, profile_path=profile)
            adapter = load_app_adapter(config)
            route_canaries = tuple(getattr(adapter, "probe_canaries", ()) or ())
        except AppAdapterError:
            # Queue generation remains usable for a profile-only/new app; the
            # generic selector is safer than inventing app-specific routes.
            route_canaries = ()
    selected = select_probe_cases(
        plan,
        app_slug=resolved_slug,
        selectors=selectors,
        route_canaries=route_canaries,
    )
    if not selected:
        raise ValueError("没有可用于导航探测的用例")

    cases: list[dict[str, Any]] = []
    for order, case in enumerate(selected, start=1):
        cases.append(
            {
                "probe_order": order,
                "retest_order": order,
                "initial_status": "probe",
                "sheet": case.get("sheet"),
                "row": int(case["row"]),
                "case_id": case.get("case_id"),
                "case_name": case.get("case_name", ""),
                "page_group_id": case.get("page_group_id", ""),
                "page_group_key": case.get("page_group_key", ""),
                "route_key": _route_key(case),
            }
        )

    return {
        "schema_version": "2.0",
        "queue_type": "navigation_probe",
        "execution_mode": "navigation_probe",
        "source_file": str(Path(source).expanduser().resolve()),
        "profile_file": str(Path(profile).expanduser().resolve()) if profile else "",
        "app_slug": resolved_slug,
        "selection_strategy": (
            "explicit_selectors"
            if selectors
            else "adapter_route_canaries"
            if route_canaries
            else "generic_route_canaries"
        ),
        "navigation_only": True,
        "business_actions_executed": False,
        "cases": cases,
    }


def _write_json(document: dict[str, Any], path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with open(fd, "w", encoding="utf-8", newline="\n", closefd=True) as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
        Path(temporary_name).replace(target)
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()
    return target


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", required=True, help=".xls/.xlsx 用例文件")
    parser.add_argument("--profile", help="App profile.yaml")
    parser.add_argument("--app", dest="app_slug", help="App slug；不传时从 profile 读取")
    parser.add_argument(
        "--case",
        action="append",
        dest="selectors",
        help="显式指定探测用例，可重复；格式为 Sheet!行号 或 case_id",
    )


def _build_command(args: argparse.Namespace) -> int:
    document = build_probe_queue(
        args.source,
        profile=args.profile,
        app_slug=args.app_slug or "",
        selectors=args.selectors or (),
    )
    output = _write_json(document, args.out)
    print(
        json.dumps(
            {
                "out": str(output),
                "app": document["app_slug"],
                "cases": len(document["cases"]),
                "strategy": document["selection_strategy"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def _run_command(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    queue_path = output / "navigation_probe_queue.json"
    document = build_probe_queue(
        args.source,
        profile=args.profile,
        app_slug=args.app_slug or "",
        selectors=args.selectors or (),
    )
    _write_json(document, queue_path)

    runner = Path(args.runner).expanduser().resolve()
    if not runner.is_file():
        raise FileNotFoundError(f"执行器不存在: {runner}")
    command = [
        sys.executable,
        str(runner),
        "--source",
        str(Path(args.source).expanduser().resolve()),
        "--output",
        str(output),
        "--probe",
        "--probe-queue",
        str(queue_path),
    ]
    if document.get("app_slug"):
        command.extend(["--app", str(document["app_slug"])])
    if args.profile:
        command.extend(["--profile", str(Path(args.profile).expanduser().resolve())])
    if args.action_plan:
        command.extend(["--action-plan", str(Path(args.action_plan).expanduser().resolve())])
    elif args.legacy_deterministic:
        command.append("--legacy-deterministic")
    if args.recovery_agent_command:
        command.extend(["--recovery-agent-command", args.recovery_agent_command])
    if args.recovery_agent_timeout is not None:
        command.extend(["--recovery-agent-timeout", str(args.recovery_agent_timeout)])
    if args.recovery_max_attempts is not None:
        command.extend(["--recovery-max-attempts", str(args.recovery_max_attempts)])
    print(json.dumps({"queue": str(queue_path), "cases": len(document["cases"]), "runner": str(runner)}, ensure_ascii=False))
    return subprocess.run(command, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按导航路径生成并执行可选的 Excel 页面探测")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="只生成导航探测队列，不操作设备")
    _common_arguments(build_parser)
    build_parser.add_argument("--out", required=True, help="导航探测队列 JSON 输出路径")
    build_parser.set_defaults(handler=_build_command)

    run_parser = subparsers.add_parser("run", help="生成队列并调用指定执行器进行导航探测")
    _common_arguments(run_parser)
    run_parser.add_argument("--runner", required=True, help="支持 --probe/--probe-queue 的 App 执行器")
    run_parser.add_argument("--output", required=True, help="导航探测运行目录")
    planner_group = run_parser.add_mutually_exclusive_group(required=True)
    planner_group.add_argument("--action-plan", help="当前 Agent 生成的结构化动作计划")
    planner_group.add_argument(
        "--legacy-deterministic",
        action="store_true",
        help="显式使用旧版固定规则，仅作迁移/诊断",
    )
    run_parser.add_argument(
        "--recovery-agent-command",
        help="转发给执行器的运行时异常恢复 Agent 命令",
    )
    run_parser.add_argument(
        "--recovery-agent-timeout",
        type=float,
        default=None,
        help="转发给执行器的运行时 Agent 超时时间（秒）",
    )
    run_parser.add_argument(
        "--recovery-max-attempts",
        type=int,
        default=None,
        help="转发给执行器的单条用例最大 Agent 恢复次数",
    )
    run_parser.set_defaults(handler=_run_command)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
