"""Framework-wide execution completeness gates.

The result builder can only prove what the executor recorded.  This module
therefore enforces the boundary between a page-level batch setup and a
case-level execution: every selected case must be present, and every terminal
executed case must carry an action trace, an observation and evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from tools.results_quality import status_bucket


class ExecutionGateError(ValueError):
    """Raised when an execution record cannot be accepted as a full run."""


DEFAULT_EXECUTION_POLICY: dict[str, Any] = {
    "require_manifest": True,
    "fail_on_incomplete": True,
    "require_action_trace": True,
    "require_observation": True,
    "require_evidence": True,
    "require_unique_case_evidence": True,
    # True permits page-group navigation reuse only; the gate still requires
    # one action trace, observation and evidence record per Excel row.
    "allow_page_batching": True,
    "allow_explicit_skip": True,
}


def load_execution_policy() -> dict[str, Any]:
    """Load the repository-wide policy, falling back to safe defaults."""

    path = Path(__file__).with_name("execution_policy.yaml")
    if not path.exists():
        return dict(DEFAULT_EXECUTION_POLICY)
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ExecutionGateError(f"无法读取全局 execution_policy.yaml: {exc}") from exc
    if value in (None, ""):
        return dict(DEFAULT_EXECUTION_POLICY)
    if not isinstance(value, Mapping):
        raise ExecutionGateError("execution_policy.yaml 必须是对象")
    policy = dict(DEFAULT_EXECUTION_POLICY)
    policy.update(value)
    return policy


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _record_keys(record: Mapping[str, Any]) -> set[str]:
    """Return stable aliases used to match a result to a selected case."""

    keys: set[str] = set()
    sheet = _text(record.get("sheet"))
    row = record.get("row")
    case_id = _text(record.get("case_id"))
    case_name = _text(record.get("case_name"))
    if sheet and row not in (None, ""):
        keys.add(f"{sheet}!{row}")
    if case_id:
        keys.add(f"id:{case_id}")
    if case_name:
        keys.add(f"name:{sheet}!{case_name}" if sheet else f"name:{case_name}")
    return keys


def _ref_keys(item: Any) -> set[str]:
    if isinstance(item, Mapping):
        return _record_keys(item)
    text = _text(item)
    if not text:
        return set()
    # The compact manifest format may use either a case id or sheet!row.
    return {text, f"id:{text}"}


def _trace_items(record: Mapping[str, Any]) -> list[Any]:
    items: list[Any] = []
    for key in ("action_trace", "execution_trace", "attempt_trace", "actionTrace", "executionTrace"):
        raw = record.get(key)
        if isinstance(raw, list):
            items.extend(raw)
        elif isinstance(raw, Mapping):
            items.append(raw)
        elif _text(raw):
            items.append(raw)
    for step in record.get("steps") or []:
        if isinstance(step, Mapping):
            items.extend(_trace_items(step))
    return items


def _valid_trace_item(item: Any) -> bool:
    if isinstance(item, Mapping):
        has_action = any(_text(item.get(key)) for key in ("type", "kind", "action", "operation", "name"))
        has_outcome = any(
            key in item and (isinstance(item[key], bool) or _text(item[key]))
            for key in ("result", "outcome", "status", "completed", "timestamp", "at")
        )
        outcome = _text(item.get("result") or item.get("outcome") or item.get("status")).casefold()
        return has_action and has_outcome and outcome not in {"planned", "pending", "skipped"}
    # A bare sentence is indistinguishable from a planned action or an
    # operator's summary.  Hard mode accepts only structured trace events.
    return False


def _has_trace(record: Mapping[str, Any]) -> bool:
    return any(_valid_trace_item(item) for item in _trace_items(record))


def _evidence_values(record: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("evidence_paths", "evidence"):
        raw = record.get(key)
        if isinstance(raw, list):
            values.update(_text(item) for item in raw if _text(item))
        elif _text(raw):
            values.add(_text(raw))
    return values


def _reason(record: Mapping[str, Any]) -> str:
    for key in (
        "blocked_reason",
        "blocker",
        "skip_reason",
        "not_executed_reason",
        "reason",
    ):
        value = _text(record.get(key))
        if value:
            return value
    return ""


def _observation(record: Mapping[str, Any]) -> str:
    value = _text(record.get("observation"))
    if value:
        return value
    value = _text(record.get("actual"))
    if value:
        return value
    for step in record.get("steps") or []:
        if isinstance(step, Mapping) and (_text(step.get("observation")) or _text(step.get("actual"))):
            return _text(step.get("observation")) or _text(step.get("actual"))
    return ""


def _manifest_selected(manifest: Mapping[str, Any]) -> list[Any]:
    for key in ("selected_cases", "selected", "selected_case_refs"):
        value = manifest.get(key)
        if isinstance(value, list):
            return value
    ids = manifest.get("selected_case_ids")
    if isinstance(ids, list):
        return ids
    return []


def validate_execution_contract(
    records: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None,
    *,
    policy: Mapping[str, Any] | None = None,
    require_manifest: bool | None = None,
) -> list[str]:
    """Return all execution-contract violations.

    ``manifest`` describes the cases selected for this run.  It is deliberately
    independent of the Excel file so the same gate works for generated cases,
    API tests and mobile UI sheets.
    """

    settings = load_execution_policy()
    if policy:
        settings.update(policy)
    if require_manifest is None:
        require_manifest = bool(settings.get("require_manifest", True))

    errors: list[str] = []
    selected = _manifest_selected(manifest or {}) if isinstance(manifest, Mapping) else []
    if require_manifest and not isinstance(manifest, Mapping):
        errors.append("缺少 execution_manifest：严格全量执行必须声明本次选中的用例清单")
    if require_manifest and not selected:
        errors.append("execution_manifest.selected_cases 不能为空")

    if isinstance(manifest, Mapping):
        mode = _text(manifest.get("mode"))
        if mode not in {"full", "sample"}:
            errors.append("execution_manifest.mode 必须是 full 或 sample")
        expected = manifest.get("expected_count")
        if expected in (None, ""):
            errors.append("execution_manifest 缺少 expected_count")
        else:
            try:
                expected_number = int(expected)
            except (TypeError, ValueError):
                errors.append("execution_manifest.expected_count 必须是整数")
            else:
                if expected_number != len(selected):
                    errors.append(
                        "execution_manifest.expected_count 与 selected_cases 数量不一致: "
                        f"expected={expected_number}, selected={len(selected)}"
                    )

    selected_keys: list[set[str]] = []
    seen_selected: set[str] = set()
    for index, item in enumerate(selected, start=1):
        keys = _ref_keys(item)
        if not keys:
            errors.append(f"execution_manifest.selected_cases 第 {index} 项缺少 sheet+row 或 case_id")
            continue
        canonical = sorted(keys)[0]
        if canonical in seen_selected:
            errors.append(f"execution_manifest 存在重复用例: {canonical}")
        seen_selected.add(canonical)
        selected_keys.append(keys)

    # The module-plan execution path is intentionally stricter than legacy
    # result documents: it requires both Excel identity and deterministic
    # source/execution order for every row.
    row_scoped_manifest = isinstance(manifest, Mapping) and _text(manifest.get("execution_scope")) == "single_excel_row"
    llm_review_required = bool(isinstance(manifest, Mapping) and manifest.get("llm_review_required"))

    record_keys: list[set[str]] = []
    seen_records: set[str] = set()
    for index, record in enumerate(records, start=1):
        identity = _text(record.get("case_id")) or _text(record.get("case_name")) or f"结果#{index}"
        keys = _record_keys(record)
        if not keys:
            errors.append(f"{identity}: 缺少 sheet+row、case_id 或 case_name，无法确认是否为选中用例")
        else:
            canonical = sorted(keys)[0]
            if canonical in seen_records:
                errors.append(f"结果存在重复用例: {canonical}")
            seen_records.add(canonical)
        record_keys.append(keys)

        bucket = status_bucket(record.get("status"))
        trace = _has_trace(record)
        observation = _observation(record)
        evidence = _evidence_values(record)
        if row_scoped_manifest and settings.get("require_per_row_execution", True):
            if not _text(record.get("sheet")) or record.get("row") in (None, ""):
                errors.append(f"{identity}: 行级执行结果必须带 sheet + row")
            if settings.get("require_source_order", True) and record.get("source_order") in (None, ""):
                errors.append(f"{identity}: 行级执行结果缺少 source_order")
            if settings.get("require_execution_order", True) and record.get("execution_order") in (None, ""):
                errors.append(f"{identity}: 行级执行结果缺少 execution_order")
            if settings.get("allow_observe_only_as_pass", False) is False and bucket == "pass" and record.get("action_mode") == "observe":
                errors.append(f"{identity}: observe-only 结果不能判定为通过")
        if bucket in {"pass", "fail", "partial"}:
            if settings.get("require_action_trace", True) and not trace:
                errors.append(f"{identity}: 缺少真实 action_trace，不能判定为已执行")
            if settings.get("require_observation", True) and not observation:
                errors.append(f"{identity}: 缺少执行后的 observation/actual")
            if settings.get("require_evidence", True) and not evidence:
                errors.append(f"{identity}: 缺少用例级 evidence")
            if llm_review_required and not isinstance(record.get("llm_review"), Mapping):
                errors.append(f"{identity}: 该执行计划要求逐行 LLM 复核，缺少 llm_review")
        elif bucket == "blocked":
            if not _reason(record):
                errors.append(f"{identity}: 阻塞结果必须填写 blocked_reason/blocker/reason")
            if not trace:
                errors.append(f"{identity}: 阻塞结果必须保留至少一条尝试轨迹 attempt_trace/action_trace")
            if settings.get("require_evidence", True) and not evidence:
                errors.append(f"{identity}: 阻塞结果缺少尝试证据")
        elif bucket == "skip":
            if not settings.get("allow_explicit_skip", True):
                errors.append(f"{identity}: 当前执行策略不允许跳过用例")
            elif not _reason(record) and not observation:
                errors.append(f"{identity}: 跳过/不适用必须填写原因")
        elif bucket == "pending":
            # Waiting for data is an explicit execution outcome, not a silent
            # omission.  It still needs an attempted setup/action trail and
            # a reason so it cannot be used as a convenient placeholder.
            if not _reason(record):
                errors.append(f"{identity}: 待数据/待验证必须填写 reason 或 blocker")
            if not trace:
                errors.append(f"{identity}: 待数据/待验证必须保留尝试轨迹")
            if settings.get("require_evidence", True) and not evidence:
                errors.append(f"{identity}: 待数据/待验证缺少尝试证据")
        elif settings.get("fail_on_incomplete", True):
            errors.append(
                f"{identity}: 状态 {record.get('status')!r} 表示未完成执行，不能生成严格最终结果"
            )

    if row_scoped_manifest and settings.get("require_per_row_execution", True) and records:
        if settings.get("require_source_order", True):
            source_orders = [record.get("source_order") for record in records]
            if source_orders != list(range(1, len(records) + 1)):
                errors.append("行级执行结果的 source_order 必须从 1 开始连续，且按 Excel 原始顺序记录")
        if settings.get("require_execution_order", True):
            execution_orders = [record.get("execution_order") for record in records]
            if execution_orders != list(range(1, len(records) + 1)):
                errors.append("行级执行结果的 execution_order 必须从 1 开始连续")

    if selected:
        for index, ref_keys in enumerate(selected_keys, start=1):
            matches = [keys for keys in record_keys if ref_keys.intersection(keys)]
            if not matches:
                errors.append(f"选中用例第 {index} 项没有对应执行结果: {sorted(ref_keys)[0]}")
        for index, keys in enumerate(record_keys, start=1):
            if not any(keys.intersection(ref_keys) for ref_keys in selected_keys):
                identity = _text(records[index - 1].get("case_id")) or _text(records[index - 1].get("case_name")) or f"结果#{index}"
                errors.append(f"{identity}: 结果不在 execution_manifest.selected_cases 中")
        if len(records) != len(selected):
            errors.append(
                "选中用例数与结果记录数不一致: "
                f"selected={len(selected)}, results={len(records)}"
            )

    if settings.get("require_unique_case_evidence", True):
        evidence_owner: dict[str, str] = {}
        for index, record in enumerate(records, start=1):
            if status_bucket(record.get("status")) == "skip":
                continue
            identity = _text(record.get("case_id")) or _text(record.get("case_name")) or f"结果#{index}"
            for evidence in _evidence_values(record):
                previous = evidence_owner.get(evidence)
                if previous and previous != identity:
                    errors.append(
                        f"{identity}: evidence {evidence!r} 与 {previous} 复用；每条用例必须有独立结果证据"
                    )
                else:
                    evidence_owner[evidence] = identity

    return errors


def ensure_execution_contract(
    records: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None,
    *,
    policy: Mapping[str, Any] | None = None,
    require_manifest: bool | None = None,
) -> None:
    errors = validate_execution_contract(
        records,
        manifest,
        policy=policy,
        require_manifest=require_manifest,
    )
    if errors:
        raise ExecutionGateError("; ".join(errors))
