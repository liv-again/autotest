"""Plan and merge single-case retests for completed sheets or modules.

The first-pass result remains immutable history.  This module only selects
non-passing cases, creates a one-case-at-a-time queue, and merges the executor
owned second-pass observations back into a new result document.  It does not
operate the device and it never invents an ``actual`` value.

Typical workflow::

    python tools/retest_results.py plan \
        --results first-pass/results.json --scope sheet --scope-name 行情 \
        --out first-pass/retest_queue.json

    # The executor consumes queue ``cases`` one item at a time and writes
    # those case records to retest_execution.json.
    python tools/retest_results.py merge \
        --results first-pass/results.json --plan first-pass/retest_queue.json \
        --retest-results first-pass/retest_execution.json \
        --out first-pass/results.final.json
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.annotate_excel import AnnotationError, normalize_results
from tools.results_quality import status_bucket


class RetestError(ValueError):
    """Raised when a retest plan or merge is ambiguous or incomplete."""


DEFAULT_RETEST_BUCKETS = ("fail", "partial", "blocked", "pending", "other")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _drop_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _drop_none(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _load_document(path: Path) -> Any:
    if not path.exists():
        raise RetestError(f"结果文件不存在: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RetestError(f"结果文件必须使用 UTF-8 编码: {path}") from exc
    try:
        if path.suffix.casefold() in {".yaml", ".yml"}:
            return yaml.safe_load(content)
        return json.loads(content)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise RetestError(f"结果文件不是合法 JSON/YAML: {path}: {exc}") from exc


def _normalize(
    document: Any,
    *,
    strict: bool,
    require_evidence: bool = True,
    duplicate_threshold: int = 3,
) -> list[dict[str, Any]]:
    try:
        return normalize_results(
            document,
            strict=strict,
            require_evidence=require_evidence if strict else None,
            duplicate_threshold=duplicate_threshold,
        )
    except (AnnotationError, TypeError, ValueError) as exc:
        mode = "严格复测结果" if strict else "首轮结果"
        raise RetestError(f"{mode}无法归一化: {exc}") from exc


def _case_key(record: Mapping[str, Any], *, required: bool = True) -> str:
    sheet = _text(record.get("sheet"))
    row = record.get("row")
    case_id = _text(record.get("case_id"))
    case_name = _text(record.get("case_name"))
    if sheet and row is not None:
        return f"{sheet}!{row}"
    if case_id:
        return f"id:{case_id}"
    if case_name:
        return f"name:{case_name}"
    if required:
        raise RetestError("用例缺少 sheet+row、case_id 或 case_name，无法安全建立复测对应关系")
    return ""


def _module_name(record: Mapping[str, Any]) -> str:
    for key in ("module", "module_name", "moduleName", "模块", "scope"):
        value = _text(record.get(key))
        if value:
            return value
    # 没有单独模块字段时，工作表本身就是最小可用模块边界。
    return _text(record.get("sheet"))


def _effective_bucket(record: Mapping[str, Any]) -> str:
    """Use case status, while respecting a non-pass step hidden under a pass."""

    buckets = [status_bucket(record.get("status"))]
    buckets.extend(status_bucket(step.get("status")) for step in record.get("steps") or [])
    for candidate in ("fail", "blocked", "partial", "pending", "other", "skip", "pass"):
        if candidate in buckets:
            return candidate
    return "other"


def _in_scope(record: Mapping[str, Any], scope: str, scope_name: str | None) -> bool:
    if scope == "all":
        return True
    if not scope_name:
        raise RetestError(f"scope={scope} 时必须提供 scope_name")
    if scope == "sheet":
        return _text(record.get("sheet")) == scope_name
    return _module_name(record) == scope_name


def plan_retests(
    document: Any,
    *,
    scope: str = "all",
    scope_name: str | None = None,
    status_buckets: Sequence[str] = DEFAULT_RETEST_BUCKETS,
) -> dict[str, Any]:
    """Create a deterministic queue containing one entry per retest case."""

    if scope not in {"all", "sheet", "module"}:
        raise RetestError(f"不支持的 scope: {scope}")
    allowed = set(status_buckets)
    unknown = allowed.difference({"fail", "partial", "blocked", "pending", "other", "skip", "pass"})
    if unknown:
        raise RetestError(f"不支持的 status bucket: {', '.join(sorted(unknown))}")

    records = _normalize(document, strict=False)
    queue: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if not _in_scope(record, scope, scope_name):
            continue
        bucket = _effective_bucket(record)
        if bucket not in allowed:
            continue
        key = _case_key(record)
        if key in seen:
            raise RetestError(f"复测队列定位重复: {key}")
        seen.add(key)
        queue.append(
            {
                "retest_id": key,
                "retest_order": len(queue) + 1,
                "execution_mode": "single_case",
                "fresh_setup_required": True,
                "sheet": record.get("sheet"),
                "row": record.get("row"),
                "case_id": record.get("case_id"),
                "case_name": record.get("case_name"),
                "module": _module_name(record),
                "initial_bucket": bucket,
                "initial_status": record.get("status"),
                "initial_actual": record.get("actual"),
                "reason": "first_pass_not_pass",
                # The executor receives the original steps/assertions and
                # returns a new case record with fresh evidence.
                "case": copy.deepcopy(record),
            }
        )

    return {
        "schema_version": "2.0",
        "execution_mode": "single_case",
        "scope": {"type": scope, "name": scope_name},
        "status_buckets": list(status_buckets),
        "cases": queue,
    }


def _index_records(records: Sequence[Mapping[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        key = _case_key(record)
        if key in indexed:
            raise RetestError(f"{label}中存在重复用例定位: {key}")
        indexed[key] = dict(record)
    return indexed


def _plan_keys(plan: Mapping[str, Any]) -> list[str]:
    raw_cases = plan.get("cases")
    if not isinstance(raw_cases, list):
        raise RetestError("复测计划必须包含 cases 列表")
    keys: list[str] = []
    for index, item in enumerate(raw_cases, start=1):
        if not isinstance(item, Mapping):
            raise RetestError(f"复测计划第 {index} 项必须是对象")
        key = _text(item.get("retest_id"))
        if not key:
            key = _case_key(item)
        if key in keys:
            raise RetestError(f"复测计划中存在重复 retest_id: {key}")
        keys.append(key)
    return keys


def _attempt_snapshot(record: Mapping[str, Any], *, attempt: int, phase: str) -> dict[str, Any]:
    snapshot = copy.deepcopy(dict(record))
    # Avoid nesting a previous merge indefinitely if a caller repeats the
    # operation.  The current final record still retains the complete history
    # in the outer attempts list.
    snapshot.pop("attempts", None)
    snapshot.pop("retest", None)
    snapshot["attempt"] = attempt
    snapshot["phase"] = phase
    return snapshot


def _setup_trace(retest_record: Mapping[str, Any], retest_document: Any) -> list[Any]:
    trace = retest_record.get("retest_setup_trace")
    if trace is None:
        trace = retest_record.get("setup_trace")
    if trace is None and isinstance(retest_document, Mapping):
        trace = retest_document.get("setup_trace")
    if trace in (None, ""):
        return []
    if not isinstance(trace, list):
        raise RetestError("复测 setup_trace 必须是列表")
    return copy.deepcopy(trace)


def merge_retests(
    initial_document: Any,
    retest_document: Any,
    *,
    plan: Mapping[str, Any] | None = None,
    require_all: bool = True,
    require_evidence: bool = True,
    duplicate_threshold: int = 3,
) -> dict[str, Any]:
    """Merge fresh single-case records and retain both attempts per case."""

    initial_records = _normalize(initial_document, strict=False)
    retest_records = _normalize(
        retest_document,
        strict=True,
        require_evidence=require_evidence,
        duplicate_threshold=duplicate_threshold,
    )
    initial_by_key = _index_records(initial_records, "首轮结果")
    retest_by_key = _index_records(retest_records, "复测结果")

    selected_keys = _plan_keys(plan) if plan is not None else list(retest_by_key)
    missing_from_initial = [key for key in selected_keys if key not in initial_by_key]
    if missing_from_initial:
        raise RetestError(f"复测计划中的用例不在首轮结果中: {', '.join(missing_from_initial)}")
    unexpected = [key for key in retest_by_key if key not in set(selected_keys)]
    if unexpected and plan is not None:
        raise RetestError(f"复测结果包含计划外用例: {', '.join(unexpected)}")
    missing_retests = [key for key in selected_keys if key not in retest_by_key]
    if require_all and missing_retests:
        raise RetestError(f"复测结果不完整，缺少: {', '.join(missing_retests)}")

    merged_cases: list[dict[str, Any]] = []
    selected_set = set(selected_keys)
    for initial in initial_records:
        key = _case_key(initial)
        if key not in selected_set or key not in retest_by_key:
            merged_cases.append(copy.deepcopy(initial))
            continue
        retest = retest_by_key[key]
        final_record = copy.deepcopy(retest)
        final_record["attempts"] = [
            _attempt_snapshot(initial, attempt=1, phase="batch"),
            _attempt_snapshot(retest, attempt=2, phase="single_case_retest"),
        ]
        final_record["retest"] = {
            "retest_of": key,
            "round": 2,
            "mode": "single_case",
            "reason": "first_pass_not_pass",
            "initial_bucket": _effective_bucket(initial),
            "initial_status": initial.get("status"),
            "retest_status": retest.get("status"),
            "setup_trace": _setup_trace(retest, retest_document),
        }
        merged_cases.append(final_record)

    # Re-run the gate on the final visible records before allowing the caller
    # to write them.  Attempt history is metadata and does not replace the
    # final actual/evidence fields checked here.
    _normalize(
        {"cases": merged_cases},
        strict=True,
        require_evidence=require_evidence,
        duplicate_threshold=duplicate_threshold,
    )

    result: dict[str, Any] = {
        "schema_version": "2.0",
        "cases": _drop_none(merged_cases),
        "retest_run": {
            "execution_mode": "single_case",
            "planned": len(selected_keys),
            "completed": len(retest_by_key),
            "require_all": require_all,
        },
    }
    if isinstance(initial_document, Mapping) and initial_document.get("setup_trace") is not None:
        result["setup_trace"] = copy.deepcopy(initial_document["setup_trace"])
    if isinstance(retest_document, Mapping) and retest_document.get("setup_trace") is not None:
        result["retest_setup_trace"] = copy.deepcopy(retest_document["setup_trace"])
    return result


def write_json(document: Mapping[str, Any], out: str | Path) -> Path:
    output = Path(out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="为未通过用例生成逐条复测队列并合并复测结果")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="生成单用例复测队列")
    plan.add_argument("--results", required=True, help="首轮 results.json")
    plan.add_argument("--out", required=True, help="复测队列输出 JSON")
    plan.add_argument("--scope", choices=("all", "sheet", "module"), default="all")
    plan.add_argument("--scope-name", help="sheet 或 module 名称；scope=all 时省略")
    plan.add_argument(
        "--statuses",
        default=",".join(DEFAULT_RETEST_BUCKETS),
        help="需要复测的状态桶，逗号分隔；默认 fail,partial,blocked,pending,other",
    )

    merge = subparsers.add_parser("merge", help="合并逐条复测结果并保留两轮历史")
    merge.add_argument("--results", required=True, help="首轮 results.json")
    merge.add_argument("--plan", help="plan 命令生成的复测队列；提供后会强制校验是否全部完成")
    merge.add_argument("--retest-results", required=True, help="复测执行器输出的结果 JSON/YAML")
    merge.add_argument("--out", required=True, help="最终 results.final.json")
    merge.add_argument("--allow-missing-retest", action="store_true", help="允许计划中的部分用例尚未复测")
    merge.add_argument("--allow-missing-evidence", action="store_true", help="允许复测结果缺少 evidence（不推荐）")
    merge.add_argument("--duplicate-threshold", type=int, default=3)
    return parser


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            if args.scope != "all" and not _text(args.scope_name):
                parser.error("--scope 为 sheet/module 时必须提供 --scope-name")
            statuses = tuple(item.strip() for item in args.statuses.split(",") if item.strip())
            document = plan_retests(
                _load_document(Path(args.results).expanduser().resolve()),
                scope=args.scope,
                scope_name=_text(args.scope_name) or None,
                status_buckets=statuses,
            )
            output = write_json(document, args.out)
            print(json.dumps({"out": str(output), "cases": len(document["cases"])}, ensure_ascii=False))
            return 0

        initial_path = Path(args.results).expanduser().resolve()
        retest_path = Path(args.retest_results).expanduser().resolve()
        plan_document = None
        if args.plan:
            loaded_plan = _load_document(Path(args.plan).expanduser().resolve())
            if not isinstance(loaded_plan, Mapping):
                raise RetestError("复测计划必须是对象")
            plan_document = loaded_plan
        merged = merge_retests(
            _load_document(initial_path),
            _load_document(retest_path),
            plan=plan_document,
            require_all=not args.allow_missing_retest,
            require_evidence=not args.allow_missing_evidence,
            duplicate_threshold=args.duplicate_threshold,
        )
        output = write_json(merged, args.out)
        print(json.dumps({"out": str(output), "cases": len(merged["cases"]), "retested": merged["retest_run"]["completed"]}, ensure_ascii=False))
        return 0
    except (RetestError, OSError, TypeError) as exc:
        print(f"retest_results: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
