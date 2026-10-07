"""Mode-aware workflow coordinator for sixgill runs.

This module composes existing execution, review, retest, comparison and probe
tools.  It intentionally does not change the mobile Runner's execution logic.
Human/Agent work is represented as an explicit waiting phase in workflow state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_ROOT = PROJECT_ROOT / "runs" / "workflows"
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))


class WorkflowError(ValueError):
    """Raised when a workflow transition or required artifact is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowError(f"必需产物不存在: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"无法读取 JSON 产物 {path}: {exc}") from exc


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise WorkflowError(f"阶段产物缺失: {resolved}")
    return {"path": str(resolved), "sha256": _sha256(resolved)}


def _verify_frozen_inputs(state: Mapping[str, Any]) -> None:
    context = state["context"]
    source = Path(context["source"])
    if not source.is_file() or _sha256(source) != context.get("source_sha256"):
        raise WorkflowError("工作流启动后用例文件发生变化或已丢失；拒绝继续使用旧结果")
    profile_value = context.get("profile")
    if profile_value:
        profile = Path(profile_value)
        if not profile.is_file() or _sha256(profile) != context.get("profile_sha256"):
            raise WorkflowError("工作流启动后 Profile 发生变化或已丢失；拒绝混用画像版本")


def _append_event(state: dict[str, Any], event: str, **details: Any) -> None:
    state.setdefault("history", []).append({"at": _now(), "event": event, **details})
    state["updated_at"] = _now()


def _save(state: dict[str, Any]) -> None:
    if state.get("mode") not in {"standard", "site_compare", "navigation_probe"}:
        raise WorkflowError("workflow state 的 mode 非法")
    if state.get("status") not in {
        "running",
        "waiting",
        "waiting_for_user",
        "waiting_for_agent",
        "blocked",
        "complete",
    }:
        raise WorkflowError("workflow state 的 status 非法")
    if not isinstance(state.get("history"), list) or not isinstance(state.get("context"), Mapping):
        raise WorkflowError("workflow state 缺少合法的 history/context")
    if state.get("status") == "complete":
        artifacts = state.get("artifacts")
        if state["mode"] == "navigation_probe":
            required = {"queue", "execution", "report"}
            if not isinstance(artifacts, Mapping) or not required.issubset(artifacts):
                raise WorkflowError("导航探测缺少队列、执行记录或报告，不能标记 complete")
        elif state["mode"] == "standard":
            round_state = state.get("rounds", {}).get("standard", {})
            if not round_state.get("completed") or not all(
                round_state.get(key) for key in ("final_results", "workbook", "report")
            ):
                raise WorkflowError("标准流程缺少最终结果、回填工作簿或报告，不能标记 complete")
        else:
            if not isinstance(artifacts, Mapping) or not {
                "comparison", "comparison_excel", "report"
            }.issubset(artifacts):
                raise WorkflowError("站点对比缺少 comparison、对比结果 Excel 或报告，不能标记 complete")
            if not all(
                state.get("rounds", {}).get(name, {}).get("completed")
                for name in ("new", "old")
            ):
                raise WorkflowError("新旧站点标准流程均未完成，不能标记 site_compare complete")
            if (state.get("site_compare_status") or {}).get("phase") != "COMPLETED":
                raise WorkflowError("Site Compare Controller 尚未完成，不能标记 workflow complete")
    _write_json(_state_path(str(state["workflow_id"])), state)


def _state_path(workflow_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,95}", workflow_id):
        raise WorkflowError(f"非法 workflow id: {workflow_id!r}")
    return WORKFLOW_ROOT / workflow_id / "workflow_state.json"


def _project_output(value: str | Path, *, label: str) -> Path:
    output = Path(value).expanduser().resolve()
    try:
        output.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise WorkflowError(f"{label} 必须位于项目目录内: {output}") from exc
    return output


def _load_state(workflow_id: str) -> dict[str, Any]:
    value = _json(_state_path(workflow_id))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise WorkflowError("workflow_state.json 格式或版本无效")
    if value.get("workflow_id") != workflow_id:
        raise WorkflowError("workflow_state.json 的 ID 与请求 ID 不一致")
    if value.get("mode") not in {"standard", "site_compare", "navigation_probe"}:
        raise WorkflowError("workflow_state.json 的 mode 无效")
    if not isinstance(value.get("context"), Mapping) or not isinstance(value.get("history"), list):
        raise WorkflowError("workflow_state.json 缺少 context/history")
    return value


def _run(command: Sequence[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            capture_output=capture,
        )
    except OSError as exc:
        raise WorkflowError(f"无法启动命令 {command[0]!r}: {exc}") from exc


def _run_checked(state: dict[str, Any], stage: str, command: Sequence[str]) -> None:
    origin = state.get("transition_origin") or state.get("phase")
    state["phase"] = f"RUNNING_{stage.upper()}"
    state["status"] = "running"
    _append_event(state, "stage_started", stage=stage, command=list(command))
    _save(state)
    result = _run(command)
    if result.returncode != 0:
        state["phase"] = "BLOCKED"
        state["status"] = "blocked"
        state["blocked_retry_phase"] = origin
        state["blocked_stage"] = stage
        state["last_error"] = f"阶段 {stage} 命令退出码 {result.returncode}"
        _append_event(state, "stage_failed", stage=stage, exit_code=result.returncode)
        _save(state)
        raise WorkflowError(state["last_error"])
    _append_event(state, "stage_command_succeeded", stage=stage)
    _save(state)


def _tool(name: str) -> Path:
    return PROJECT_ROOT / "tools" / name


def _python_tool(name: str, *args: str) -> list[str]:
    return [sys.executable, str(_tool(name)), *map(str, args)]


def _case_list(document: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(document, Mapping):
        raise WorkflowError(f"{label} 顶层必须是 JSON 对象")
    cases = document.get("cases")
    if not isinstance(cases, list):
        raise WorkflowError(f"{label} 缺少 cases 列表")
    if any(not isinstance(case, Mapping) for case in cases):
        raise WorkflowError(f"{label}.cases 中含有非对象记录")
    return [dict(case) for case in cases]


def _runner_args(
    state: dict[str, Any],
    *,
    output: Path,
    action_plan: str | None,
    retest_queue: Path | None = None,
    llm_retest: bool = False,
    resume: bool = False,
) -> list[str]:
    context = state["context"]
    runner = Path(context["runner"])
    command = [
        sys.executable,
        str(runner),
        "--app",
        str(context["app"]),
        "--source",
        str(context["source"]),
        "--output",
        str(output),
        "--no-auto-retest-blocked",
    ]
    if context.get("profile"):
        command.extend(["--profile", str(context["profile"])])
    if context.get("device"):
        command.extend(["--device", str(context["device"])])
    for sheet in context.get("sheets", []):
        command.extend(["--sheet", str(sheet)])
    if retest_queue is not None:
        command.extend(["--retest-queue", str(retest_queue)])
        if llm_retest:
            command.append("--llm-retest")
        elif action_plan:
            command.extend(["--action-plan", str(action_plan)])
        else:
            raise WorkflowError("复测需要 --retest-action-plan 或 --llm-retest")
    elif action_plan:
        command.extend(["--action-plan", str(action_plan)])
    else:
        raise WorkflowError("标准执行需要有效的 action plan")
    if resume:
        command.append("--resume")
    return command


def _runner_artifacts(output: Path, *, retest: bool = False) -> tuple[Path, Path]:
    records = output / ("retest_execution.json" if retest else "execution_records.json")
    review_queue = output / "llm_review_queue.json"
    for path in (records, review_queue, output / "execution_manifest.json"):
        if not path.is_file():
            raise WorkflowError(f"Runner 成功退出但缺少必需产物: {path}")
    _case_list(_json(records), label=records.name)
    return records, review_queue


def _manifest_requires_llm_review(document: Any) -> bool:
    if not isinstance(document, Mapping):
        return False
    manifest = document.get("execution_manifest")
    if isinstance(manifest, Mapping) and manifest.get("llm_review_required"):
        return True
    return bool(document.get("llm_review_required"))


def _retest_requires_review(document: Any, first_pass_document: Any = None) -> bool:
    if _manifest_requires_llm_review(document) or _manifest_requires_llm_review(
        first_pass_document
    ):
        return True
    cases = _case_list(document, label="retest_execution.json")
    for case in cases:
        status = str(case.get("status") or "")
        is_pending = "待验证" in status or "待数据" in status
        has_reason = any(
            case.get(field)
            for field in ("reason", "blocker", "blocked_reason")
        )
        if is_pending and not has_reason:
            return True
    return False


def _record_retest_completion(
    state: dict[str, Any], records: Path, review_queue: Path
) -> bool:
    """Persist retest outputs and wait for required review before merging."""

    round_name = str(state.get("active_round") or "")
    round_state = state.get("rounds", {}).get(round_name, {})
    document = _json(records)
    output = Path(round_state["output"])
    first_pass_document = _json(output / "results.json")
    cases = _case_list(document, label=records.name)
    round_state["retest_execution"] = _artifact(records)
    round_state["retest_review_queue"] = _artifact(review_queue)
    round_state.pop("pending_run", None)
    _append_event(state, "retest_execution_complete", round=round_name, records=str(records))

    if _retest_requires_review(document, first_pass_document):
        round_state["stage"] = "retest_review"
        state["phase"] = "WAITING_FOR_RETEST_REVIEW"
        state["status"] = "waiting"
        state["next_action"] = (
            f"复测结果要求逐条 LLM 复核；请读取 {review_queue} 生成 llm_reviews.json，"
            f"再执行 workflow.py continue --id {state['workflow_id']} --reviews <retest llm_reviews.json>"
        )
        _append_event(state, "retest_review_required", round=round_name, cases=len(cases))
        state.pop("transition_origin", None)
        _save(state)
        return True

    round_state["stage"] = "retest_merge"
    state["phase"] = "WAITING_FOR_RETEST_MERGE"
    state["status"] = "waiting"
    state["transition_origin"] = "WAITING_FOR_RETEST_MERGE"
    state["next_action"] = "自动校验并合并复测结果，然后完成严格回填和报告"
    _save(state)
    return False


def _site_status(state: dict[str, Any]) -> dict[str, Any]:
    """Read the authoritative Site Compare controller state before transitions."""
    result = _run(_python_tool("site_compare_ctl.py", "status", "--json"), capture=True)
    if result.returncode != 0:
        raise WorkflowError(f"site_compare_ctl status 失败: {result.stdout}{result.stderr}")
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"site_compare_ctl status 返回非法 JSON: {exc}") from exc
    state["site_compare_status"] = status
    _save(state)
    return status


def _site_action(
    state: dict[str, Any],
    expected_phase: str,
    *arguments: str,
) -> dict[str, Any]:
    status = _site_status(state)
    if status.get("phase") != expected_phase:
        raise WorkflowError(
            f"站点对比阶段不匹配: 当前 {status.get('phase')!r}，要求 {expected_phase!r}"
        )
    result = _run(_python_tool("site_compare_ctl.py", *arguments), capture=True)
    if result.returncode != 0:
        raise WorkflowError(f"site_compare_ctl {' '.join(arguments)} 失败: {result.stdout}{result.stderr}")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"site_compare_ctl 返回非法 JSON: {exc}") from exc
    if not response.get("ok", False):
        raise WorkflowError(str(response.get("error") or "site_compare_ctl 操作失败"))
    state["site_compare_status"] = response
    _save(state)
    return response


def _start_round(
    state: dict[str, Any],
    name: str,
    output: Path,
    *,
    action_plan: str | None,
    resume: bool = False,
) -> None:
    round_state = state.setdefault("rounds", {}).setdefault(name, {})
    round_state.update(
        {
            "output": str(output.resolve()),
            "action_plan": str(Path(action_plan).resolve()) if action_plan else None,
            "action_plan_sha256": _sha256(Path(action_plan).resolve()) if action_plan else None,
            "stage": "first_pass",
            "completed": False,
        }
    )
    state["active_round"] = name
    state["phase"] = "RUNNING_EXECUTION"
    state["status"] = "running"
    _save(state)
    if output.exists() and any(output.iterdir()) and not resume:
        raise WorkflowError(f"拒绝复用非空输出目录: {output}")
    output.mkdir(parents=True, exist_ok=True)
    command = _runner_args(state, output=output, action_plan=action_plan, resume=resume)
    round_state["pending_run"] = "first_pass"
    _save(state)
    result = _run(command)
    if result.returncode != 0:
        round_state["pending_run"] = "first_pass"
        state["phase"] = "BLOCKED_EXECUTION"
        state["status"] = "blocked"
        state["last_error"] = f"首轮 Runner 退出码 {result.returncode}；可用 continue --resume-execution 续跑"
        _append_event(state, "runner_failed", round=name, exit_code=result.returncode)
        _save(state)
        raise WorkflowError(state["last_error"])
    records, queue = _runner_artifacts(output)
    round_state["first_pass_records"] = _artifact(records)
    round_state["review_queue"] = _artifact(queue)
    round_state.pop("pending_run", None)
    round_state["stage"] = "first_pass_review"
    state["phase"] = "WAITING_FOR_REVIEW"
    state["status"] = "waiting"
    state["next_action"] = f"为 {name} 轮生成 llm_reviews.json，再执行 workflow.py continue --id {state['workflow_id']} --reviews <reviews.json>"
    _append_event(state, "first_pass_complete", round=name, records=str(records), review_queue=str(queue))
    _save(state)


def _new_state(mode: str, context: Mapping[str, Any]) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    workflow_id = f"{stamp}-{mode}-{uuid.uuid4().hex[:8]}"
    state = {
        "schema_version": 1,
        "workflow_id": workflow_id,
        "mode": mode,
        "status": "running",
        "phase": "INITIALIZING",
        "created_at": _now(),
        "updated_at": _now(),
        "context": dict(context),
        "rounds": {},
        "history": [],
    }
    _state_path(workflow_id).parent.mkdir(parents=True, exist_ok=True)
    _append_event(state, "workflow_created", mode=mode)
    _save(state)
    return state


def _start_standard(state: dict[str, Any], action_plan: str | None) -> None:
    output = Path(state["context"]["output"])
    _start_round(state, "standard", output, action_plan=action_plan)


def _start_site_compare(state: dict[str, Any], action_plan: str) -> None:
    status = _site_status(state)
    if status.get("phase") != "NO_ACTIVE_WORKFLOW":
        raise WorkflowError(
            "已有站点对比流程。请先处理 runs/site_compare/state.json 对应流程，不能覆盖或另起一轮。"
        )
    context = state["context"]
    response = _site_action(
        state,
        "NO_ACTIVE_WORKFLOW",
        "init",
        "--app",
        str(context["app"]),
        "--source",
        str(context["source"]),
    )
    output = (PROJECT_ROOT / str(response["output"])).resolve()
    _start_round(state, "new", output, action_plan=action_plan)


def _start_navigation_probe(state: dict[str, Any], action_plan: str | None) -> None:
    context = state["context"]
    output = Path(context["output"])
    if output.exists() and any(output.iterdir()):
        raise WorkflowError(f"拒绝复用非空探测目录: {output}")
    output.mkdir(parents=True, exist_ok=True)
    command = _python_tool(
        "navigation_probe.py",
        "run",
        "--source",
        str(context["source"]),
        "--runner",
        str(context["runner"]),
        "--output",
        str(output),
        "--app",
        str(context["app"]),
    )
    if context.get("device"):
        command.extend(["--device", str(context["device"])])
    if context.get("profile"):
        command.extend(["--profile", str(context["profile"])])
    for selector in context.get("selectors", []):
        command.extend(["--case", str(selector)])
    if action_plan:
        command.extend(["--action-plan", str(action_plan)])
    else:
        raise WorkflowError("导航探测需要经过校验的 --action-plan")
    _run_checked(state, "navigation_probe", command)
    execution_path = output / "navigation_probe_execution.json"
    queue_path = output / "navigation_probe_queue.json"
    execution = _json(execution_path)
    queue = _json(queue_path)
    manifest = execution.get("execution_manifest") if isinstance(execution, Mapping) else None
    cases = _case_list(execution, label="navigation_probe_execution.json")
    expected = manifest.get("expected_count") if isinstance(manifest, Mapping) else None
    if not isinstance(manifest, Mapping) or manifest.get("execution_scope") != "navigation_probe":
        raise WorkflowError("探测执行记录缺少 execution_scope=navigation_probe")
    if expected != len(cases) or expected != len(queue.get("cases", [])):
        raise WorkflowError("导航探测队列、manifest 与执行记录数量不一致")
    if queue.get("business_actions_executed") is not False:
        raise WorkflowError("导航探测队列未明确声明 business_actions_executed=false")
    report = output / "navigation_probe_report.md"
    statuses = Counter(str(case.get("status") or "未标记") for case in cases)
    report.write_text(
        "# 导航探测报告\n\n"
        f"- App：{context['app']}\n- 用例文件：{context['source']}\n"
        f"- 探测用例：{expected}\n- 业务动作：未执行\n"
        "- 状态统计：" + "、".join(f"{key} {value}" for key, value in sorted(statuses.items())) + "\n",
        encoding="utf-8",
    )
    state["artifacts"] = {
        "queue": _artifact(queue_path),
        "execution": _artifact(execution_path),
        "report": _artifact(report),
    }
    state["phase"] = "COMPLETED"
    state["status"] = "complete"
    state["next_action"] = "DONE"
    _append_event(state, "navigation_probe_complete", cases=expected)
    _save(state)


def _review_and_plan(state: dict[str, Any], review_path: str) -> None:
    round_name = str(state.get("active_round") or "")
    if not round_name:
        raise WorkflowError("当前工作流没有活动执行轮次")
    round_state = state["rounds"][round_name]
    output = Path(round_state["output"])
    state["transition_origin"] = "WAITING_FOR_REVIEW"
    _save(state)
    stage = round_state.get("stage")
    if stage == "first_pass_review":
        records = output / "execution_records.json"
        review_queue = output / "llm_review_queue.json"
        reviewed = output / "results.reviewed.json"
        results = output / "results.json"
        _run_checked(
            state,
            "llm_review_merge",
            _python_tool(
                "llm_review_results.py",
                "merge",
                "--input",
                str(records),
                "--queue",
                str(review_queue),
                "--reviews",
                str(Path(review_path).resolve()),
                "--out",
                str(reviewed),
            ),
        )
        _run_checked(
            state,
            "build_reviewed_results",
            _python_tool("build_results.py", "--input", str(reviewed), "--out", str(results)),
        )
        queue_path = output / "retest_queue.json"
        _run_checked(
            state,
            "plan_retest",
            _python_tool(
                "retest_results.py",
                "plan",
                "--results",
                str(reviewed),
                "--statuses",
                "blocked,pending,fail,partial",
                "--out",
                str(queue_path),
            ),
        )
        queue = _json(queue_path)
        queue_cases = queue.get("cases") if isinstance(queue, Mapping) else None
        if not isinstance(queue_cases, list):
            raise WorkflowError("retest_results.py 输出缺少 cases 列表")
        round_state["review"] = _artifact(Path(review_path).resolve())
        round_state["reviewed_results"] = _artifact(reviewed)
        round_state["results"] = _artifact(results)
        round_state["retest_queue"] = _artifact(queue_path)
        round_state["retest_count"] = len(queue_cases)
        if queue_cases:
            round_state["stage"] = "retest_plan"
            state["phase"] = "WAITING_FOR_RETEST_PLAN"
            state["status"] = "waiting"
            state.pop("transition_origin", None)
            state.pop("blocked_retry_phase", None)
            state.pop("blocked_stage", None)
            state["next_action"] = (
                "生成复测 action plan 后执行 workflow.py continue --id "
                f"{state['workflow_id']} --retest-action-plan <plan.json>"
            )
            _append_event(state, "retest_required", round=round_name, cases=len(queue_cases))
            _save(state)
            return
        _finalize_round(state, round_name, results)
        return

    raise WorkflowError(f"当前阶段不接受复核结果: {stage!r}")


def _review_retest(state: dict[str, Any], review_path: str) -> None:
    round_name = str(state.get("active_round") or "")
    if not round_name:
        raise WorkflowError("当前工作流没有活动执行轮次")
    round_state = state["rounds"][round_name]
    if round_state.get("stage") != "retest_review":
        raise WorkflowError("当前工作流不在等待复测复核阶段")

    output = Path(round_state["output"])
    retest_output = output / "retest"
    records = retest_output / "retest_execution.json"
    review_queue = retest_output / "llm_review_queue.json"
    reviewed = retest_output / "results.reviewed.json"
    review = Path(review_path).resolve()
    if not review.is_file():
        raise WorkflowError(f"复测 LLM 复核文件不存在: {review}")

    state["transition_origin"] = "WAITING_FOR_RETEST_REVIEW"
    _save(state)
    _run_checked(
        state,
        "retest_llm_review_merge",
        _python_tool(
            "llm_review_results.py",
            "merge",
            "--input",
            str(records),
            "--queue",
            str(review_queue),
            "--reviews",
            str(review),
            "--out",
            str(reviewed),
        ),
    )
    reviewed_document = _json(reviewed)
    if not isinstance(reviewed_document, Mapping) or reviewed_document.get("reviewed") is not True:
        raise WorkflowError("复测复核合并结果缺少 reviewed=true")
    if reviewed_document.get("review_scope") != "single_excel_row":
        raise WorkflowError("复测复核合并结果缺少 review_scope=single_excel_row")
    _case_list(reviewed_document, label=reviewed.name)

    round_state["retest_review"] = _artifact(review)
    round_state["retest_reviewed_results"] = _artifact(reviewed)
    round_state["stage"] = "retest_merge"
    state["phase"] = "WAITING_FOR_RETEST_MERGE"
    state["status"] = "waiting"
    state["transition_origin"] = "WAITING_FOR_RETEST_MERGE"
    state["next_action"] = "自动校验并合并复测结果，然后完成严格回填和报告"
    state.pop("blocked_retry_phase", None)
    state.pop("blocked_stage", None)
    _append_event(state, "retest_review_complete", round=round_name, review=str(review))
    _save(state)
    _merge_retest(state)


def _merge_retest(state: dict[str, Any]) -> None:
    round_name = str(state.get("active_round") or "")
    round_state = state.get("rounds", {}).get(round_name, {})
    if round_state.get("stage") != "retest_merge":
        raise WorkflowError("当前工作流不在复测结果合并阶段")
    output = Path(round_state["output"])
    final_results = output / "results.final.json"
    raw_retest_results = output / "retest" / "retest_execution.json"
    retest_results = output / "retest" / "results.reviewed.json"
    raw_document = _json(raw_retest_results)
    first_pass_document = _json(output / "results.json")
    if _retest_requires_review(raw_document, first_pass_document):
        if not retest_results.is_file():
            review_queue = output / "retest" / "llm_review_queue.json"
            if not review_queue.is_file():
                raise WorkflowError(f"复测要求 LLM 复核，但缺少复核队列: {review_queue}")
            _record_retest_completion(state, raw_retest_results, review_queue)
            return
        reviewed_document = _json(retest_results)
        if (
            not isinstance(reviewed_document, Mapping)
            or reviewed_document.get("reviewed") is not True
            or reviewed_document.get("review_scope") != "single_excel_row"
        ):
            raise WorkflowError("复测清单要求 LLM 复核，但 results.reviewed.json 未通过复核标记校验")
    else:
        retest_results = raw_retest_results
    state["transition_origin"] = "WAITING_FOR_RETEST_MERGE"
    _run_checked(
        state,
        "merge_retest_results",
        _python_tool(
            "retest_results.py",
            "merge",
            "--results",
            str(output / "results.json"),
            "--plan",
            str(output / "retest_queue.json"),
            "--retest-results",
            str(retest_results),
            "--out",
            str(final_results),
        ),
    )
    round_state["retest_results"] = _artifact(final_results)
    _finalize_round(state, round_name, final_results)


def _finalize_round(state: dict[str, Any], round_name: str, result_path: Path) -> None:
    round_state = state["rounds"][round_name]
    output = Path(round_state["output"])
    final_results = output / "results.final.json"
    if result_path.resolve() != final_results.resolve():
        shutil.copy2(result_path, final_results)
    workbook = output / "cases_AI自测结果.xlsx"
    annotation_source = Path(state["context"]["source"])
    if annotation_source.suffix.casefold() == ".xls":
        converted_source = output / "source_for_annotation.xlsx"
        _run_checked(
            state,
            "convert_legacy_excel",
            _python_tool(
                "xls_to_xlsx.py",
                "--src",
                str(annotation_source),
                "--out",
                str(converted_source),
            ),
        )
        annotation_source = converted_source
    _run_checked(
        state,
        "annotate_excel",
        _python_tool(
            "annotate_excel.py",
            "--src",
            str(annotation_source),
            "--results",
            str(final_results),
            "--out",
            str(workbook),
            "--evidence-root",
            str(output),
            "--strict",
        ),
    )
    document = _json(final_results)
    cases = _case_list(document, label=final_results.name)
    statuses = Counter(str(case.get("status") or "未标记") for case in cases)
    report = output / "workflow_report.md"
    status_lines = "\n".join(f"- {key}：{value}" for key, value in sorted(statuses.items()))
    retest_count = int(round_state.get("retest_count") or 0)
    report.write_text(
        "# 标准执行报告\n\n"
        f"- App：{state['context']['app']}\n- 用例文件：{state['context']['source']}\n"
        f"- 执行轮次：{round_name}\n- 最终用例数：{len(cases)}\n"
        f"- 计划复测数：{retest_count}\n- 结果状态：\n{status_lines or '- 无'}\n"
        f"- 最终结果：{final_results}\n- 回填工作簿：{workbook}\n",
        encoding="utf-8",
    )
    round_state["final_results"] = _artifact(final_results)
    round_state["workbook"] = _artifact(workbook)
    round_state["report"] = _artifact(report)
    round_state["completed"] = True
    round_state["stage"] = "complete"
    _append_event(
        state,
        "standard_round_complete",
        round=round_name,
        cases=len(cases),
        retest_count=retest_count,
    )
    if state["mode"] == "standard":
        state["phase"] = "COMPLETED"
        state["status"] = "complete"
        state["next_action"] = "DONE"
        state.pop("last_error", None)
        state.pop("transition_origin", None)
        state.pop("blocked_retry_phase", None)
        state.pop("blocked_stage", None)
        _save(state)
    elif round_name == "new":
        site_phase = _site_status(state).get("phase")
        if site_phase == "RUNNING_NEW":
            response = _site_action(state, "RUNNING_NEW", "finish-new")
        elif site_phase == "WAITING_FOR_OLD_SITE":
            response = state.get("site_compare_status") or _site_status(state)
        else:
            raise WorkflowError(f"新站点轮次完成后发现不可恢复的站点阶段: {site_phase!r}")
        state["phase"] = "WAITING_FOR_OLD_SITE"
        state["status"] = "waiting_for_user"
        state["next_action"] = "切换到旧站点后，执行 continue --confirm-old-site --action-plan <old-site-plan.json>"
        state["site_compare_status"] = response
        state.pop("transition_origin", None)
        state.pop("blocked_retry_phase", None)
        state.pop("blocked_stage", None)
        _save(state)
    elif round_name == "old":
        site_phase = _site_status(state).get("phase")
        if site_phase == "RUNNING_OLD":
            response = _site_action(state, "RUNNING_OLD", "finish-old")
        elif site_phase == "READY_TO_COMPARE":
            response = state.get("site_compare_status") or _site_status(state)
        else:
            raise WorkflowError(f"旧站点轮次完成后发现不可恢复的站点阶段: {site_phase!r}")
        inputs = _site_action(state, "READY_TO_COMPARE", "compare-inputs", "--json")
        state["phase"] = "WAITING_FOR_COMPARISON"
        state["status"] = "waiting_for_agent"
        state["compare_inputs"] = inputs
        state["site_compare_status"] = response
        state.pop("transition_origin", None)
        state.pop("blocked_retry_phase", None)
        state.pop("blocked_stage", None)
        state["next_action"] = (
            "根据 compare_inputs 中的新旧最终结果及证据生成 comparison.json；提交后工作流会自动生成对比结果 Excel，之后执行 "
            f"workflow.py continue --id {state['workflow_id']} --comparison-file <comparison.json>"
        )
        _save(state)


def _start_retest(state: dict[str, Any], *, action_plan: str | None, llm_retest: bool) -> None:
    round_name = str(state.get("active_round") or "")
    round_state = state["rounds"][round_name]
    if round_state.get("stage") != "retest_plan":
        raise WorkflowError("当前工作流不在等待复测计划阶段")
    output = Path(round_state["output"])
    retest_output = output / "retest"
    queue = output / "retest_queue.json"
    retest_output.mkdir(parents=True, exist_ok=True)
    round_state["stage"] = "retest_execution"
    round_state["pending_run"] = "retest"
    round_state["retest_action_plan"] = str(Path(action_plan).resolve()) if action_plan else None
    round_state["retest_action_plan_sha256"] = (
        _sha256(Path(action_plan).resolve()) if action_plan else None
    )
    round_state["llm_retest"] = bool(llm_retest)
    state["transition_origin"] = "WAITING_FOR_RETEST_PLAN"
    state["phase"] = "RUNNING_RETEST"
    state["status"] = "running"
    _save(state)
    command = _runner_args(
        state,
        output=retest_output,
        action_plan=action_plan,
        retest_queue=queue,
        llm_retest=llm_retest,
    )
    result = _run(command)
    if result.returncode != 0:
        round_state["stage"] = "retest_execution"
        round_state["pending_run"] = "retest"
        state["phase"] = "BLOCKED_EXECUTION"
        state["status"] = "blocked"
        state["last_error"] = f"复测 Runner 退出码 {result.returncode}；可用 continue --resume-execution 续跑"
        _append_event(state, "retest_runner_failed", round=round_name, exit_code=result.returncode)
        _save(state)
        raise WorkflowError(state["last_error"])
    records, review_queue = _runner_artifacts(retest_output, retest=True)
    if _record_retest_completion(state, records, review_queue):
        return
    _merge_retest(state)


def _resume_execution(state: dict[str, Any]) -> None:
    _verify_frozen_inputs(state)
    round_name = str(state.get("active_round") or "")
    if not round_name:
        raise WorkflowError("没有可恢复的执行轮次")
    round_state = state["rounds"][round_name]
    pending = round_state.get("pending_run")
    output = Path(round_state["output"])
    if pending == "first_pass":
        action_plan = round_state.get("action_plan")
        if action_plan and _sha256(Path(action_plan)) != round_state.get("action_plan_sha256"):
            raise WorkflowError("续跑前 action plan 已变化；拒绝与原 Runner 会话混用")
        command = _runner_args(
            state,
            output=output,
            action_plan=action_plan,
            resume=True,
        )
        result = _run(command)
        if result.returncode != 0:
            raise WorkflowError(f"Runner 续跑失败，退出码 {result.returncode}")
        records, queue = _runner_artifacts(output)
        round_state["first_pass_records"] = _artifact(records)
        round_state["review_queue"] = _artifact(queue)
        round_state.pop("pending_run", None)
        round_state["stage"] = "first_pass_review"
        state["phase"] = "WAITING_FOR_REVIEW"
        state["status"] = "waiting"
        state["next_action"] = f"提交 {round_name} 轮复核文件"
        state.pop("last_error", None)
        state.pop("blocked_retry_phase", None)
        state.pop("blocked_stage", None)
        _save(state)
        return
    if pending == "retest":
        action_plan = round_state.get("retest_action_plan")
        if action_plan and _sha256(Path(action_plan)) != round_state.get("retest_action_plan_sha256"):
            raise WorkflowError("复测续跑前 action plan 已变化；拒绝与原 Runner 会话混用")
        queue = output / "retest_queue.json"
        retest_output = output / "retest"
        command = _runner_args(
            state,
            output=retest_output,
            action_plan=action_plan,
            retest_queue=queue,
            llm_retest=bool(round_state.get("llm_retest")),
            resume=True,
        )
        result = _run(command)
        if result.returncode != 0:
            raise WorkflowError(f"复测 Runner 续跑失败，退出码 {result.returncode}")
        records, review_queue = _runner_artifacts(retest_output, retest=True)
        state.pop("last_error", None)
        state.pop("blocked_retry_phase", None)
        state.pop("blocked_stage", None)
        if _record_retest_completion(state, records, review_queue):
            return
        _merge_retest(state)
        return
    raise WorkflowError("当前没有可恢复的 Runner 子进程阶段")


def _site_comparison_excel_records(
    cases: Sequence[Mapping[str, Any]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Map the validated site comparison into the existing Excel annotator schema."""

    status_labels = {
        "MATCH": ("✅通过", "新旧站点目标页面的可见数据一致。"),
        "MISMATCH": ("❌不通过", "新旧站点目标页面存在差异。"),
        "PENDING": ("🟡待验证", "当前证据不足以完成同页数据比较。"),
    }
    records: list[dict[str, Any]] = []
    for index, item in enumerate(cases, start=1):
        raw_status = str(item.get("status") or "")
        if raw_status not in status_labels:
            raise WorkflowError(f"comparison cases[{index}] 状态无法回填 Excel: {raw_status!r}")
        label, outcome = status_labels[raw_status]
        sheet_name = item.get("sheet")
        row_number = item.get("row")
        case_key = str(item.get("case_key") or "")
        if (not sheet_name or row_number in (None, "")) and case_key:
            match = re.fullmatch(r"(.+)[!:](\d+)", case_key)
            if match:
                sheet_name = sheet_name or match.group(1)
                row_number = row_number or int(match.group(2))

        reason = str(item.get("reason") or "").strip()
        actual = (
            "AI执行步骤：对照新、旧站点相同用例的页面截图与可见数据。\n"
            f"操作结果：{outcome}\n"
            f"判断理由：判定为{label}。{reason}"
        )
        evidence: list[str] = []
        for evidence_field in ("new_evidence_used", "old_evidence_used"):
            raw_items = item.get(evidence_field) or []
            if isinstance(raw_items, (str, Path)):
                raw_items = [raw_items]
            elif isinstance(raw_items, Mapping):
                raw_items = [raw_items]
            if not isinstance(raw_items, list):
                continue
            for raw_evidence in raw_items:
                if isinstance(raw_evidence, Mapping):
                    raw_evidence = raw_evidence.get("path") or raw_evidence.get("file") or raw_evidence.get("uri")
                if not raw_evidence:
                    continue
                evidence_path = Path(str(raw_evidence)).expanduser()
                if not evidence_path.is_absolute():
                    evidence_path = (PROJECT_ROOT / evidence_path).resolve()
                rendered_path = str(evidence_path)
                if rendered_path not in evidence:
                    evidence.append(rendered_path)

        record: dict[str, Any] = {
            "case_id": item.get("case_id") or None,
            "case_name": item.get("case_name") or None,
            "status": label,
            "actual": actual,
            "evidence": evidence,
            "tested_at": generated_at,
        }
        if sheet_name:
            record["sheet"] = str(sheet_name)
        if row_number not in (None, ""):
            record["row"] = int(row_number)
        if not record["case_id"] and not record.get("row"):
            raise WorkflowError(
                f"comparison cases[{index}] 缺少可用于 Excel 定位的 case_id 或 sheet/row"
            )
        records.append(record)

    return {"cases": records}


def _write_site_comparison_excel(
    *,
    source_path: Path,
    comparison_dir: Path,
    cases: Sequence[Mapping[str, Any]],
) -> tuple[Path, list[str]]:
    comparison_excel = comparison_dir / f"{source_path.stem}_对比结果.xlsx"
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    result_document = _site_comparison_excel_records(
        cases,
        generated_at=generated_at,
    )
    comparison_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=".site-comparison-results.", suffix=".json", dir=comparison_dir
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(result_document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        command = _python_tool(
            "annotate_excel.py",
            "--src",
            str(source_path),
            "--results",
            str(temporary_path),
            "--out",
            str(comparison_excel),
        )
        result = _run(command, capture=True)
        if result.returncode != 0:
            raise WorkflowError(
                "站点对比结果 Excel 生成失败: "
                f"{result.stdout.strip()} {result.stderr.strip()}".strip()
            )
        try:
            annotation_report = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"Excel 回填器返回非法报告: {exc}") from exc
        matched = annotation_report.get("matched")
        unmatched = annotation_report.get("unmatched")
        if not isinstance(matched, list) or len(matched) != len(cases) or unmatched:
            raise WorkflowError(
                "站点对比 Excel 未完整回填所有用例: "
                f"expected={len(cases)}, matched={len(matched) if isinstance(matched, list) else 'invalid'}, "
                f"unmatched={len(unmatched) if isinstance(unmatched, list) else 'invalid'}"
            )
        warnings = annotation_report.get("warnings")
        return comparison_excel, [str(item) for item in warnings] if isinstance(warnings, list) else []
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _continue_site_compare(state: dict[str, Any], args: argparse.Namespace) -> None:
    phase = state.get("phase")
    if phase == "WAITING_FOR_OLD_SITE":
        if not args.confirm_old_site:
            raise WorkflowError("请先手动切换旧站点，再显式传入 --confirm-old-site")
        if not args.action_plan:
            raise WorkflowError("旧站点首轮执行需要 --action-plan")
        response = _site_action(state, "WAITING_FOR_OLD_SITE", "start-old")
        output = (PROJECT_ROOT / str(response["output"])).resolve()
        _start_round(state, "old", output, action_plan=args.action_plan)
        return
    if phase == "WAITING_FOR_COMPARISON":
        if not args.comparison_file:
            raise WorkflowError("比较阶段需要 --comparison-file <comparison.json>")
        comparison = str(Path(args.comparison_file).resolve())
        current = _site_status(state)
        if current.get("phase") == "READY_TO_COMPARE":
            response = _site_action(
                state,
                "READY_TO_COMPARE",
                "complete",
                "--comparison-file",
                comparison,
            )
        elif current.get("phase") == "COMPLETED":
            completed_comparison = Path(str(current.get("comparison_file") or ""))
            if not completed_comparison.is_absolute():
                completed_comparison = PROJECT_ROOT / completed_comparison
            if completed_comparison.resolve() != Path(comparison).resolve():
                raise WorkflowError("Site Compare Controller 已用另一份 comparison.json 完成")
            response = current
        else:
            raise WorkflowError(f"比较文件提交时站点控制器处于非法阶段: {current.get('phase')!r}")
        document = _json(Path(comparison))
        cases = _case_list(document, label="comparison.json")
        counts = Counter(str(case.get("status") or "未知") for case in cases)
        comparison_dir = PROJECT_ROOT / str(response["comparison_dir"])
        source_path = Path(state["context"]["source"]).resolve()
        comparison_excel, excel_warnings = _write_site_comparison_excel(
            source_path=source_path,
            comparison_dir=comparison_dir,
            cases=cases,
        )
        report = comparison_dir / "site_compare_report.md"
        report.write_text(
            "# 站点对比报告\n\n"
            f"- App：{state['context']['app']}\n- 用例文件：{state['context']['source']}\n"
            f"- 比较用例：{len(cases)}\n"
            "- 比较状态：\n"
            + "\n".join(f"  - {key}：{value}" for key, value in sorted(counts.items()))
            + f"\n- comparison.json：{comparison}\n"
            + f"- 对比结果 Excel：{comparison_excel}\n"
            + (
                "- Excel 回填提示：\n" + "\n".join(f"  - {warning}" for warning in excel_warnings) + "\n"
                if excel_warnings
                else ""
            ),
            encoding="utf-8",
        )
        state["artifacts"] = {
            "comparison": _artifact(Path(comparison)),
            "comparison_excel": _artifact(comparison_excel),
            "report": _artifact(report),
        }
        state["phase"] = "COMPLETED"
        state["status"] = "complete"
        state["next_action"] = "DONE"
        state["site_compare_status"] = response
        _append_event(state, "site_compare_complete", cases=len(cases))
        _save(state)
        return
    raise WorkflowError(f"站点对比当前阶段不能执行该操作: {phase!r}")


def _continue(state: dict[str, Any], args: argparse.Namespace) -> None:
    if state.get("status") == "complete":
        raise WorkflowError("该工作流已经完成")
    if args.resume_execution:
        if state.get("phase") != "BLOCKED_EXECUTION":
            raise WorkflowError("--resume-execution 仅适用于 Runner 中断/失败后的 BLOCKED_EXECUTION")
        _resume_execution(state)
        return
    if state.get("phase") == "BLOCKED":
        retry_phase = state.get("blocked_retry_phase")
        if retry_phase not in {
            "WAITING_FOR_REVIEW",
            "WAITING_FOR_RETEST_PLAN",
            "WAITING_FOR_RETEST_REVIEW",
            "WAITING_FOR_RETEST_MERGE",
        }:
            raise WorkflowError(
                "该阻塞阶段无法自动重试；请查看 workflow_state.json 的 last_error 并处理产物后重新启动相应阶段"
            )
        state["phase"] = retry_phase
        state["status"] = "waiting"
    if state.get("mode") == "site_compare" and state.get("phase") in {
        "WAITING_FOR_OLD_SITE",
        "WAITING_FOR_COMPARISON",
    }:
        _continue_site_compare(state, args)
        return
    if state.get("phase") == "WAITING_FOR_RETEST_MERGE":
        _merge_retest(state)
        return
    if state.get("phase") == "WAITING_FOR_RETEST_REVIEW":
        if not args.reviews:
            raise WorkflowError("当前阶段需要 --reviews <retest llm_reviews.json>")
        _review_retest(state, args.reviews)
        return
    if state.get("phase") == "WAITING_FOR_REVIEW":
        if state.get("mode") == "site_compare":
            round_name = str(state.get("active_round") or "")
            round_state = state.get("rounds", {}).get(round_name, {})
            if round_state.get("completed") and round_state.get("stage") == "complete":
                _finalize_round(
                    state,
                    round_name,
                    Path(round_state["final_results"]["path"]),
                )
                return
        if not args.reviews:
            raise WorkflowError("当前阶段需要 --reviews <llm_reviews.json>")
        _review_and_plan(state, args.reviews)
        return
    if state.get("phase") == "WAITING_FOR_RETEST_PLAN":
        if not args.retest_action_plan and not args.llm_retest:
            raise WorkflowError("当前阶段需要 --retest-action-plan <plan.json> 或 --llm-retest")
        round_state = state["rounds"][state["active_round"]]
        round_state["retest_action_plan"] = (
            str(Path(args.retest_action_plan).resolve()) if args.retest_action_plan else None
        )
        round_state["llm_retest"] = bool(args.llm_retest)
        _start_retest(
            state,
            action_plan=round_state["retest_action_plan"],
            llm_retest=bool(args.llm_retest),
        )
        return
    raise WorkflowError(f"当前阶段不能继续: {state.get('phase')!r}")


def _public_status(state: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "ok": True,
        "workflow_id": state.get("workflow_id"),
        "mode": state.get("mode"),
        "status": state.get("status"),
        "phase": state.get("phase"),
        "active_round": state.get("active_round"),
        "next_action": state.get("next_action"),
        "state_file": str(_state_path(str(state["workflow_id"]))),
    }
    if state.get("mode") == "site_compare" and state.get("site_compare_status"):
        result["site_compare"] = state["site_compare_status"]
    if state.get("last_error"):
        result["last_error"] = state["last_error"]
    return result


def _start(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        raise WorkflowError(f"用例文件不存在: {source}")
    if args.mode == "site_compare":
        try:
            source.relative_to(PROJECT_ROOT)
        except ValueError as exc:
            raise WorkflowError("site_compare 的 source 必须位于 sixgill 项目目录内") from exc
        if args.output:
            raise WorkflowError("site_compare 使用 Site Compare Controller 管理的固定输出目录")
    if args.mode != "navigation_probe" and args.selectors:
        raise WorkflowError("--case 仅适用于 navigation_probe 模式")
    runner = Path(args.runner).expanduser().resolve()
    if not runner.is_file():
        raise WorkflowError(f"Runner 不存在: {runner}")
    action_plan = Path(args.action_plan).expanduser().resolve() if args.action_plan else None
    if action_plan and not action_plan.is_file():
        raise WorkflowError(f"action plan 不存在: {action_plan}")
    if not action_plan:
        raise WorkflowError("完整工作流必须提供经过校验的 --action-plan")
    if args.mode == "navigation_probe" and args.sheets:
        raise WorkflowError("导航探测使用 --case Sheet!行号 选择用例，不接受 --sheet")
    profile = Path(args.profile).expanduser().resolve() if args.profile else None
    if profile and not profile.is_file():
        raise WorkflowError(f"Profile 不存在: {profile}")
    context: dict[str, Any] = {
        "app": args.app,
        "source": str(source),
        "runner": str(runner),
        "profile": str(profile) if profile else None,
        "device": args.device,
        "sheets": list(args.sheets or []),
        "source_sha256": _sha256(source),
        "profile_sha256": _sha256(profile) if profile else None,
    }
    state = _new_state(args.mode, context)
    try:
        if args.mode == "standard":
            output = _project_output(args.output, label="standard 输出目录") if args.output else (
                _state_path(state["workflow_id"]).parent / "standard"
            )
            state["context"]["output"] = str(output)
            _save(state)
            _start_standard(state, str(action_plan) if action_plan else None)
        elif args.mode == "site_compare":
            _start_site_compare(state, str(action_plan))
        elif args.mode == "navigation_probe":
            output = _project_output(args.output, label="navigation_probe 输出目录") if args.output else (
                _state_path(state["workflow_id"]).parent / "navigation_probe"
            )
            state["context"]["output"] = str(output)
            state["context"]["selectors"] = list(args.selectors or [])
            _save(state)
            _start_navigation_probe(state, str(action_plan) if action_plan else None)
        else:  # pragma: no cover - argparse choices enforce supported modes
            raise WorkflowError(f"不支持的 workflow mode: {args.mode}")
    except Exception:
        if state.get("status") not in {"blocked", "complete"}:
            state["status"] = "blocked"
            if state.get("phase", "").startswith("RUNNING_"):
                active = state.get("rounds", {}).get(state.get("active_round"), {})
                state["phase"] = (
                    "BLOCKED_EXECUTION"
                    if active.get("pending_run") in {"first_pass", "retest"}
                    else "BLOCKED"
                )
            _save(state)
        raise
    return _public_status(state)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按指定模式完整编排 sixgill 执行流程")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="启动 standard/site_compare/navigation_probe 工作流")
    start.add_argument("--mode", required=True, choices=("standard", "site_compare", "navigation_probe"))
    start.add_argument("--app", required=True)
    start.add_argument("--source", required=True)
    start.add_argument("--runner", default=str(_tool("_run_three_sheets.py")))
    start.add_argument("--profile")
    start.add_argument("--device")
    start.add_argument("--sheet", action="append", dest="sheets")
    start.add_argument("--output", help="standard/navigation_probe 输出目录；site_compare 使用受控固定目录")
    start.add_argument("--action-plan", required=True, help="本轮 Agent 生成并校验的 agent_action_plan.json")
    start.add_argument("--case", action="append", dest="selectors", help="导航探测用例 selector，可重复")

    status = sub.add_parser("status", help="查询工作流状态")
    status.add_argument("--id", required=True)

    resume = sub.add_parser("continue", help="提交当前等待阶段所需输入并推进工作流")
    resume.add_argument("--id", required=True)
    resume.add_argument("--reviews", help="首轮或复测阶段对应的 llm_reviews.json")
    resume.add_argument("--retest-action-plan", help="复测队列的结构化 action plan")
    resume.add_argument("--llm-retest", action="store_true", help="让 Runner 使用其配置的 LLM 复测会话")
    resume.add_argument("--confirm-old-site", action="store_true", help="已手动切换到旧站点的明确确认")
    resume.add_argument("--action-plan", help="旧站点首轮使用的 action plan")
    resume.add_argument("--comparison-file", help="站点逐用例 comparison.json")
    resume.add_argument("--resume-execution", action="store_true", help="续跑中断的 Runner 阶段")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            result = _start(args)
        elif args.command == "status":
            result = _public_status(_load_state(args.id))
        else:
            state = _load_state(args.id)
            try:
                _verify_frozen_inputs(state)
                _continue(state, args)
            except Exception as exc:
                if state.get("status") != "complete" and state.get("phase") not in {
                    "WAITING_FOR_REVIEW",
                    "WAITING_FOR_RETEST_PLAN",
                    "WAITING_FOR_RETEST_REVIEW",
                    "WAITING_FOR_OLD_SITE",
                    "WAITING_FOR_COMPARISON",
                    "BLOCKED_EXECUTION",
                    "BLOCKED",
                }:
                    origin = state.get("transition_origin")
                    active = state.get("rounds", {}).get(state.get("active_round"), {})
                    if not origin and active.get("stage") == "first_pass_review":
                        origin = "WAITING_FOR_REVIEW"
                    state["blocked_retry_phase"] = origin
                    state["phase"] = (
                        "BLOCKED_EXECUTION"
                        if active.get("pending_run") in {"first_pass", "retest"}
                        else "BLOCKED"
                    )
                    state["status"] = "blocked"
                state["last_error"] = str(exc)
                _save(state)
                raise
            result = _public_status(state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (WorkflowError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
