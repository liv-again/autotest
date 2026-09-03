"""Quality gates for step-level app self-test results.

The executor is the source of truth for ``actual``.  This module deliberately
does not try to manufacture an observation from an action or an expected
value; it only rejects results that are structurally incomplete or clearly
describe an execution trace instead of a page observation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Mapping, Sequence


class ResultQualityError(ValueError):
    """Raised when result facts are unsafe to pass to the backfiller."""


_GENERIC_ACTUAL_PATTERNS = (
    re.compile(r"^已在.+(?:执行|完成).*(?:截图|证据|留痕|保留).*$", re.IGNORECASE),
    re.compile(r"^(?:已执行|执行完成|已完成).*(?:操作|点击|搜索|滑动|导航|用例).*$", re.IGNORECASE),
    re.compile(r"^当前页面已完成(?:若干|相关|必要)?(?:导航|操作|动作|滑动).*$", re.IGNORECASE),
    re.compile(r"^已到达.+(?:页面|界面)[。.!！]?$", re.IGNORECASE),
    re.compile(r"^(?:截图|证据)(?:已保存|已保留|已生成).*$", re.IGNORECASE),
)

_GENERIC_EXACT = {
    "ok",
    "pass",
    "success",
    "done",
    "completed",
    "通过",
    "成功",
    "完成",
    "已完成",
    "正常",
    "符合预期",
    "操作完成",
    "页面正常",
}

_OBSERVATION_HINTS = (
    "显示",
    "展示",
    "进入",
    "回到",
    "返回",
    "出现",
    "未出现",
    "存在",
    "不存在",
    "可见",
    "不可见",
    "页面",
    "界面",
    "标题",
    "字段",
    "列表",
    "数据",
    "排序",
    "顺序",
    "切换",
    "展开",
    "收起",
    "输入框",
    "按钮",
    "跳转",
    "提示",
    "结果",
    "成功",
    "失败",
    "正常",
    "异常",
    "为空",
    "一致",
    "不一致",
    "正确",
    "错误",
    "无响应",
    "可用",
    "不可用",
    "保存",
)

_OPERATION_PREFIXES = (
    "点击",
    "进入",
    "打开",
    "返回",
    "切换",
    "滑动",
    "搜索",
    "输入",
    "选择",
    "执行",
    "完成",
    "关闭",
    "查看",
    "下拉",
    "上拉",
)


def status_bucket(status: Any) -> str:
    """Return the semantic bucket used for retest selection and rollups."""

    normalized = _compact(status)
    if "部分通过" in normalized or "部分成功" in normalized:
        return "partial"
    if "失败" in normalized or "❌" in normalized or "不通过" in normalized:
        return "fail"
    if "阻塞" in normalized or "⛔" in normalized:
        return "blocked"
    if "待" in normalized or normalized.startswith("🟡") or normalized.startswith("⚠"):
        return "pending"
    if "跳过" in normalized or "⏭" in normalized or normalized.startswith("☑"):
        return "skip"
    if "通过" in normalized or "成功" in normalized or "✅" in normalized or "🟢" in normalized:
        return "pass"
    return "other"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _compact(value: Any) -> str:
    """Normalize only presentation punctuation for comparisons."""

    return re.sub(r"[\s，。！？；：、,.!?;:（）()\[\]{}<>《》\"'“”‘’]+", "", _text(value)).casefold()


def _identity(record: Mapping[str, Any], fallback: str) -> str:
    sheet = _text(record.get("sheet"))
    row = _text(record.get("row"))
    case_id = _text(record.get("case_id"))
    case_name = _text(record.get("case_name"))
    return case_id or case_name or (f"{sheet}!{row}" if sheet or row else fallback)


def actual_issue(actual: Any, *, action: Any = "", expected: Any = "") -> str | None:
    """Return a human-readable reason when ``actual`` is not a page fact."""

    text = _text(actual)
    if not text:
        return "actual 为空"

    compact = _compact(text)
    if compact in {_compact(item) for item in _GENERIC_EXACT}:
        return "actual 是通用占位句，不是页面观察或断言事实"
    for pattern in _GENERIC_ACTUAL_PATTERNS:
        if pattern.match(text):
            return "actual 描述了执行操作/保留截图，未描述当前步骤的页面观察"

    action_text = _text(action)
    if action_text and compact == _compact(action_text):
        return "actual 只复述当前步骤 action，未提供执行后的观察结果"

    # A short operation-only sentence is another common form of the old
    # fallback.  Keep this heuristic narrow so factual Chinese results such as
    # “账号输入成功” and “列表显示为空” remain valid.
    if (
        len(text) <= 40
        and text.startswith(_OPERATION_PREFIXES)
        and not any(marker in text for marker in _OBSERVATION_HINTS)
    ):
        return "actual 只描述动作，没有页面观察或断言结果"

    return None


def _evidence_present(record: Mapping[str, Any]) -> bool:
    evidence = record.get("evidence")
    if evidence:
        return True
    return bool(record.get("evidence_paths"))


def validate_result_records(
    records: Sequence[Mapping[str, Any]],
    *,
    duplicate_threshold: int = 3,
    require_evidence: bool = False,
) -> list[str]:
    """Validate normalized result records and return all quality errors.

    ``records`` should already have the aliases normalized by
    :func:`tools.annotate_excel.normalize_results`.  The function is also
    usable by ``build_results.py`` because its canonical output has the same
    shape.
    """

    errors: list[str] = []
    actuals: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)

    for index, record in enumerate(records, start=1):
        identity = _identity(record, f"结果#{index}")
        steps = list(record.get("steps") or [])
        if steps:
            for step_index, step in enumerate(steps, start=1):
                step_id = _text(step.get("step_id")) or f"S{step_index}"
                location = f"{identity}/{step_id}"
                issue = actual_issue(
                    step.get("actual"),
                    action=step.get("action"),
                    expected=step.get("expected"),
                )
                if issue:
                    errors.append(f"{location}: {issue}")
                if require_evidence and not _evidence_present(step):
                    errors.append(f"{location}: 缺少可追溯 evidence")
                actual = _text(step.get("actual"))
                if actual:
                    actuals[_compact(actual)].append(
                        (
                            identity,
                            step_id,
                            _compact(step.get("action")),
                            _compact(step.get("expected")),
                        )
                    )

            # A top-level actual is not used to write a step row, but reject a
            # supplied generic summary instead of allowing it to be mistaken
            # for the case's factual result later.
            if _text(record.get("actual")):
                issue = actual_issue(record.get("actual"))
                if issue:
                    errors.append(f"{identity}: {issue}")
        else:
            issue = actual_issue(record.get("actual"))
            if issue:
                errors.append(f"{identity}: {issue}")
            if require_evidence and not _evidence_present(record):
                errors.append(f"{identity}: 缺少可追溯 evidence")
            actual = _text(record.get("actual"))
            if actual:
                actuals[_compact(actual)].append(
                    (identity, "", "", "")
                )

    if duplicate_threshold < 2:
        raise ValueError("duplicate_threshold 必须大于等于 2")
    for fingerprint, items in actuals.items():
        if len(items) < duplicate_threshold:
            continue
        case_ids = {item[0] for item in items}
        signatures = {(item[2], item[3]) for item in items}
        if len(case_ids) < 2:
            continue
        # A concrete page fact can legitimately recur when the executor also
        # records the action and expected assertion for each path (for example
        # several back buttons returning to the same home page).  Repetition
        # is suspicious when those facts are absent; generic trace sentences
        # are rejected above regardless of context.
        if all(action or expected for _, _, action, expected in items):
            continue
        examples = ", ".join(f"{case}/{step}".rstrip("/") for case, step, _, _ in items[:5])
        errors.append(
            f"actual {fingerprint!r} 被 {len(case_ids)} 个不同用例复用，疑似通用结果；示例: {examples}"
        )

    return errors


def ensure_result_quality(
    records: Sequence[Mapping[str, Any]],
    *,
    duplicate_threshold: int = 3,
    require_evidence: bool = False,
) -> None:
    """Raise :class:`ResultQualityError` when any quality gate fails."""

    errors = validate_result_records(
        records,
        duplicate_threshold=duplicate_threshold,
        require_evidence=require_evidence,
    )
    if errors:
        raise ResultQualityError("; ".join(errors))
