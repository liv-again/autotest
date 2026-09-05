"""Build a strict ``results.json`` from executor-owned observations.

This module is intentionally an adapter, not an observation generator.  The
mobile executor must provide the actual page fact for every step.  If the
executor only has a navigation trace or a screenshot, the result should be
marked pending/blocked by the executor rather than being turned into a
default pass here.

Examples
--------

    python tools/build_results.py \
        --input execution_records.json \
        --out results.json

The input may be a list of cases or an object with ``cases``/``results``.  A
top-level ``setup_trace`` is preserved as execution metadata and is never
written into a case or step's ``actual`` field.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

# When invoked as ``python tools/build_results.py``, Python puts ``tools/``
# (rather than the repository root) on sys.path.  Add the root so the shared
# tools package can still be imported; normal module imports are unchanged.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.annotate_excel import normalize_results


class BuildResultsError(ValueError):
    """Raised when executor records cannot form a safe result document."""


def _load_document(path: Path) -> Any:
    if not path.exists():
        raise BuildResultsError(f"执行记录文件不存在: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BuildResultsError(f"执行记录文件必须使用 UTF-8 编码: {path}") from exc
    try:
        if path.suffix.casefold() in {".yaml", ".yml"}:
            return yaml.safe_load(content)
        return json.loads(content)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise BuildResultsError(f"执行记录文件不是合法 JSON/YAML: {path}: {exc}") from exc


def _setup_trace(document: Any, explicit: Sequence[Mapping[str, Any]] | None) -> Any:
    value = explicit
    if value is None and isinstance(document, Mapping):
        value = document.get("setup_trace")
    if value in (None, ""):
        return None
    if not isinstance(value, list):
        raise BuildResultsError("setup_trace 必须是列表")
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise BuildResultsError(f"setup_trace 第 {index} 项必须是对象")
    return value


def _drop_none(value: Any) -> Any:
    """Remove normalization-only nulls before writing the contract document."""

    if isinstance(value, Mapping):
        return {
            key: _drop_none(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def build_results(
    execution_records: Any,
    *,
    setup_trace: Sequence[Mapping[str, Any]] | None = None,
    require_evidence: bool = True,
    duplicate_threshold: int = 3,
) -> dict[str, Any]:
    """Return a validated schema-2 result document.

    ``normalize_results(..., strict=True)`` performs the required field checks
    and inherits case-level evidence to steps when a step has no own evidence.
    It never creates an ``actual`` value.  The shared semantic quality gate
    then rejects generic operation placeholders and suspicious cross-case
    reuse before anything can be written to disk.
    """

    trace = _setup_trace(execution_records, setup_trace)
    try:
        cases = normalize_results(
            execution_records,
            strict=True,
            require_evidence=require_evidence,
            duplicate_threshold=duplicate_threshold,
        )
    except (ValueError, TypeError) as exc:
        raise BuildResultsError(str(exc)) from exc

    document: dict[str, Any] = {
        "schema_version": "2.0",
        "cases": _drop_none(cases),
    }
    if trace is not None:
        document["setup_trace"] = _drop_none(trace)
    return document


def write_results(
    execution_records: Any,
    out: str | Path,
    *,
    setup_trace: Sequence[Mapping[str, Any]] | None = None,
    require_evidence: bool = True,
    duplicate_threshold: int = 3,
) -> dict[str, Any]:
    """Build and atomically write a UTF-8 JSON result document."""

    document = build_results(
        execution_records,
        setup_trace=setup_trace,
        require_evidence=require_evidence,
        duplicate_threshold=duplicate_threshold,
    )
    output = Path(out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return document


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将逐步骤实测记录构建为严格 results.json")
    parser.add_argument("--input", required=True, help="执行器输出的 JSON/YAML 记录")
    parser.add_argument("--out", required=True, help="结果 JSON 输出路径")
    parser.add_argument(
        "--allow-missing-evidence",
        action="store_true",
        help="允许没有 evidence（不推荐；默认会阻止保存）",
    )
    parser.add_argument(
        "--duplicate-threshold",
        type=int,
        default=3,
        help="判定跨用例重复 actual 的最少用例数，默认 3",
    )
    return parser


def _configure_stdio() -> None:
    """Keep Chinese result diagnostics readable on Windows consoles."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        document = _load_document(Path(args.input).expanduser().resolve())
        output = write_results(
            document,
            args.out,
            require_evidence=not args.allow_missing_evidence,
            duplicate_threshold=args.duplicate_threshold,
        )
        print(json.dumps({"out": str(Path(args.out).expanduser().resolve()), "cases": len(output["cases"])}, ensure_ascii=False))
        return 0
    except (BuildResultsError, OSError, TypeError) as exc:
        print(f"build_results: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
