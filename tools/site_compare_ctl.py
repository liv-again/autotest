"""State controller for the new/old site comparison workflow.

This module deliberately does not execute a test, talk to ADB, or call an
LLM.  It owns only the durable workflow facts around two independent sixgill
runs: the source file, fixed output directories, completeness gates, and the
allowed phase transitions.

The command is intentionally usable as a small JSON protocol from Codex::

    python tools/site_compare_ctl.py status --json
    python tools/site_compare_ctl.py init --app zhongyuan --source cases.xlsx
    python tools/site_compare_ctl.py finish-new
    python tools/site_compare_ctl.py start-old
    python tools/site_compare_ctl.py finish-old
    python tools/site_compare_ctl.py compare-inputs --json
    python tools/site_compare_ctl.py complete --comparison-file runs/site_compare/comparison/comparison.json

``app`` is persisted because selecting an App is part of the execution
context.  ``source`` may be omitted from ``init`` when the selected App has a
``site_compare_source`` or existing ``default_source`` in its app.yaml.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.app_adapter import AppAdapterError, load_app_config
from tools.execution_gate import ExecutionGateError, ensure_execution_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = PROJECT_ROOT / "runs" / "site_compare"
STATE_PATH = WORKFLOW_ROOT / "state.json"
STATE_SCHEMA_PATH = Path(__file__).with_name("site_compare_state.schema.json")

PHASES = {
    "RUNNING_NEW",
    "WAITING_FOR_OLD_SITE",
    "RUNNING_OLD",
    "READY_TO_COMPARE",
    "COMPLETED",
}

RESULT_CANDIDATES = (
    # These names reflect the existing sixgill review/retest pipeline.  The
    # first existing file wins; no new result format is introduced here.
    "results.final.semantic.annotate.json",
    "results.final.semantic.json",
    "results.final.annotate.json",
    "results.final.json",
    "results.reviewed.json",
    "results.json",
)

COMPARISON_STATUSES = {"MATCH", "MISMATCH", "PENDING"}
APP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class SiteCompareError(ValueError):
    """A safe, user-facing controller error."""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _json_load(path: Path, *, label: str) -> Any:
    if not path.is_file():
        raise SiteCompareError(f"{label} 不存在: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteCompareError(f"{label} 不是可读取的合法 JSON: {path}") from exc


def sha256_file(path: Path) -> str:
    if not path.is_file():
        raise SiteCompareError(f"文件不存在，无法计算 SHA256: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise SiteCompareError(f"无法读取文件计算 SHA256: {path}") from exc
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SiteCompareError("selected cases 不能规范化为 JSON") from exc


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _manifest_selected(manifest: Mapping[str, Any]) -> list[Any]:
    for key in ("selected_cases", "selected", "selected_case_refs", "selected_case_ids"):
        value = manifest.get(key)
        if isinstance(value, list):
            return value
    return []


def canonical_selected_cases(manifest: Mapping[str, Any]) -> list[Any]:
    selected = _manifest_selected(manifest)
    if not selected:
        raise SiteCompareError("execution_manifest 缺少非空 selected_cases/selected_case_refs/selected_case_ids")
    # Copy through JSON so callers cannot mutate the loaded manifest while a
    # hash is being computed.
    try:
        return json.loads(_canonical_json(selected))
    except json.JSONDecodeError as exc:  # pragma: no cover - guarded above
        raise SiteCompareError("selected cases 不是合法 JSON 值") from exc


def selected_cases_sha256(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(canonical_selected_cases(manifest)).encode("utf-8")).hexdigest()


def _row_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _identity_aliases(item: Any) -> set[str]:
    """Return aliases using sixgill's existing sheet/row and case-id keys."""

    aliases: set[str] = set()
    if isinstance(item, Mapping):
        sheet = _text(item.get("sheet") or item.get("sheet_name") or item.get("sheetName"))
        row = _row_value(item.get("row") if item.get("row") not in (None, "") else item.get("source_row"))
        case_id = _text(item.get("case_id") or item.get("caseId") or item.get("tc_id") or item.get("tcId"))
        case_name = _text(item.get("case_name") or item.get("caseName"))
        if sheet and row is not None:
            aliases.update({f"{sheet}!{row}", f"{sheet}:{row}"})
        if case_id:
            aliases.update({case_id, f"id:{case_id}"})
        if case_name:
            aliases.update({case_name, f"name:{sheet}!{case_name}" if sheet else f"name:{case_name}"})
        return aliases

    raw = _text(item)
    if not raw:
        return aliases
    aliases.update({raw, f"id:{raw}"})
    # The comparison example uses ``Sheet:15`` while the repository's result
    # identity is ``Sheet!15``.  Accept that presentation spelling only when
    # the suffix is an integer.
    if ":" in raw:
        sheet, row = raw.rsplit(":", 1)
        if sheet and _row_value(row) is not None:
            aliases.update({f"{sheet}!{int(row)}", f"{sheet}:{int(row)}"})
    return aliases


def _canonical_identity(item: Any) -> str:
    aliases = _identity_aliases(item)
    if not aliases:
        raise SiteCompareError(f"用例缺少可识别身份: {item!r}")
    for alias in sorted(aliases):
        if alias.startswith("id:"):
            continue
        if "!" in alias:
            return alias
    return sorted(aliases)[0]


def _records_from_document(document: Any, *, label: str) -> list[dict[str, Any]]:
    if isinstance(document, Mapping):
        records = document.get("cases")
        if records is None:
            records = document.get("results")
    else:
        records = document
    if not isinstance(records, list) or not all(isinstance(item, Mapping) for item in records):
        raise SiteCompareError(f"{label} 必须包含 cases/results 对象列表")
    return [dict(item) for item in records]


def _validate_scope(selected: Sequence[Any], records: Sequence[Mapping[str, Any]], *, label: str) -> None:
    selected_aliases = [_identity_aliases(item) for item in selected]
    if any(not aliases for aliases in selected_aliases):
        raise SiteCompareError(f"{label} 的 selected_cases 存在缺少身份的项")
    selected_keys = [_canonical_identity(item) for item in selected]
    if len(selected_keys) != len(set(selected_keys)):
        raise SiteCompareError(f"{label} 的 selected_cases 存在重复用例")

    record_aliases = [_identity_aliases(item) for item in records]
    if any(not aliases for aliases in record_aliases):
        raise SiteCompareError(f"{label} 的结果存在缺少 sheet+row/case_id 的记录")
    record_keys = [_canonical_identity(item) for item in records]
    if len(record_keys) != len(set(record_keys)):
        raise SiteCompareError(f"{label} 的结果存在重复用例")

    for index, aliases in enumerate(selected_aliases, start=1):
        if not any(aliases.intersection(candidate) for candidate in record_aliases):
            raise SiteCompareError(f"{label} 选中用例第 {index} 项没有对应结果")
    for index, aliases in enumerate(record_aliases, start=1):
        if not any(aliases.intersection(candidate) for candidate in selected_aliases):
            raise SiteCompareError(f"{label} 结果第 {index} 项不在 selected_cases 中")


def _status_next_action(phase: str) -> str:
    return {
        "RUNNING_NEW": "RUN_NEW_SITE",
        "WAITING_FOR_OLD_SITE": "WAIT_FOR_USER_OLD_SITE_CONFIRMATION",
        "RUNNING_OLD": "RUN_OLD_SITE",
        "READY_TO_COMPARE": "COMPARE_NEW_AND_OLD",
        "COMPLETED": "DONE",
    }[phase]


class SiteCompareController:
    """Deterministic controller rooted at one sixgill project."""

    def __init__(self, project_root: str | Path = PROJECT_ROOT) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.workflow_root = self.project_root / "runs" / "site_compare"
        self.state_path = self.workflow_root / "state.json"

    def _resolve_inside_root(self, value: str | Path, *, label: str) -> Path:
        if value is None:
            raise SiteCompareError(f"{label} 未提供")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve()
        if not _within(resolved, self.project_root):
            raise SiteCompareError(f"{label} 必须位于项目目录内: {value}")
        return resolved

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root).as_posix()

    def _state_path(self, value: str, *, label: str) -> Path:
        path = self._resolve_inside_root(value, label=label)
        if not _within(path, self.workflow_root):
            raise SiteCompareError(f"{label} 必须位于 Site Compare 固定目录内: {value}")
        return path

    def validate_state_schema(self, state: Any) -> None:
        if not isinstance(state, Mapping):
            raise SiteCompareError("state.json 顶层必须是 JSON 对象")
        required = {
            "version",
            "phase",
            "source",
            "source_sha256",
            "app",
            "new_run",
            "old_run",
            "comparison_dir",
            "new_manifest_sha256",
            "selected_cases_sha256",
            "comparison_file",
        }
        allowed = required
        missing = sorted(required.difference(state))
        unknown = sorted(set(state).difference(allowed))
        if missing:
            raise SiteCompareError(f"state.json 缺少字段: {', '.join(missing)}")
        if unknown:
            raise SiteCompareError(f"state.json 存在未知字段: {', '.join(unknown)}")
        if state.get("version") != 1:
            raise SiteCompareError("state.version 必须为 1")
        phase = state.get("phase")
        if phase not in PHASES:
            raise SiteCompareError(f"state.phase 非法: {phase!r}")
        app = state.get("app")
        if app is not None and (not isinstance(app, str) or not APP_RE.fullmatch(app)):
            raise SiteCompareError("state.app 必须是合法 App slug 或 null")
        for key in ("source", "new_run", "old_run", "comparison_dir"):
            if not isinstance(state.get(key), str) or not state[key].strip():
                raise SiteCompareError(f"state.{key} 必须是非空字符串")
        source_hash = state.get("source_sha256")
        if not isinstance(source_hash, str) or not HEX64_RE.fullmatch(source_hash):
            raise SiteCompareError("state.source_sha256 必须是 64 位小写 SHA256")
        for key in ("new_manifest_sha256", "selected_cases_sha256"):
            value = state.get(key)
            if value is not None and (not isinstance(value, str) or not HEX64_RE.fullmatch(value)):
                raise SiteCompareError(f"state.{key} 必须是 64 位小写 SHA256 或 null")
        comparison_file = state.get("comparison_file")
        if comparison_file is not None and (not isinstance(comparison_file, str) or not comparison_file.strip()):
            raise SiteCompareError("state.comparison_file 必须是非空字符串或 null")
        for key in ("new_run", "old_run", "comparison_dir"):
            self._state_path(state[key], label=f"state.{key}")
        self._resolve_inside_root(state["source"], label="state.source")
        if comparison_file is not None:
            comparison_path = self._state_path(comparison_file, label="state.comparison_file")
            comparison_dir = self._state_path(state["comparison_dir"], label="state.comparison_dir")
            if not _within(comparison_path, comparison_dir):
                raise SiteCompareError("state.comparison_file 必须位于 comparison_dir 内")

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            raise SiteCompareError("NO_ACTIVE_WORKFLOW")
        state = _json_load(self.state_path, label="state.json")
        self.validate_state_schema(state)
        return dict(state)

    def atomic_write_state(self, state: Mapping[str, Any]) -> None:
        self.validate_state_schema(state)
        self.workflow_root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=self.workflow_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.state_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _empty_fixed_dirs(self) -> None:
        for directory in (
            self.workflow_root / "new",
            self.workflow_root / "old",
            self.workflow_root / "comparison",
        ):
            if directory.exists() and not directory.is_dir():
                raise SiteCompareError(f"固定输出路径不是目录: {directory}")
            if directory.is_dir() and any(directory.iterdir()):
                raise SiteCompareError(f"固定输出目录已有内容，拒绝混用旧 Run: {directory}")

    def _source_from_app(self, app: str | None) -> Path | None:
        if not app:
            return None
        try:
            config = load_app_config(self.project_root, app)
        except (AppAdapterError, OSError, ValueError) as exc:
            raise SiteCompareError(f"无法读取 App 配置 {app!r}: {exc}") from exc
        configured = config.document.get("site_compare_source")
        if configured:
            candidate = Path(str(configured)).expanduser()
            if not candidate.is_absolute():
                candidate = config.app_dir / candidate
            return candidate.resolve()
        return config.default_source

    def init(self, *, source: str | Path | None = None, app: str | None = None) -> dict[str, Any]:
        if self.state_path.exists():
            raise SiteCompareError("已有 Site Compare 工作流，禁止覆盖；请先处理现有 state.json")
        if app is not None and not APP_RE.fullmatch(app):
            raise SiteCompareError(f"非法 App slug: {app!r}")
        source_value = source if source is not None else self._source_from_app(app)
        resolved_source = self._resolve_inside_root(source_value, label="source") if source_value is not None else None
        if resolved_source is None:
            raise SiteCompareError("未找到 source；请传 --source，或在 app.yaml 配置 site_compare_source/default_source")
        if not resolved_source.is_file():
            raise SiteCompareError(f"source 不存在或不是文件: {resolved_source}")
        self._empty_fixed_dirs()
        for name in ("new", "old", "comparison"):
            (self.workflow_root / name).mkdir(parents=True, exist_ok=True)
        state = {
            "version": 1,
            "phase": "RUNNING_NEW",
            "source": self._relative(resolved_source),
            "source_sha256": sha256_file(resolved_source),
            "app": app,
            "new_run": self._relative(self.workflow_root / "new"),
            "old_run": self._relative(self.workflow_root / "old"),
            "comparison_dir": self._relative(self.workflow_root / "comparison"),
            "new_manifest_sha256": None,
            "selected_cases_sha256": None,
            "comparison_file": None,
        }
        self.atomic_write_state(state)
        return self._status_from_state(state, next_action="RUN_NEW_SITE") | {
            "output": state["new_run"],
        }

    def _status_from_state(self, state: Mapping[str, Any], *, next_action: str | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "phase": state["phase"],
            "app": state.get("app"),
            "source": state["source"],
            "new_run": state["new_run"],
            "old_run": state["old_run"],
            "comparison_dir": state["comparison_dir"],
            "comparison_file": state.get("comparison_file"),
            "next_action": next_action or _status_next_action(state["phase"]),
        }

    def status(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"ok": True, "phase": "NO_ACTIVE_WORKFLOW", "next_action": "INIT"}
        state = self.load_state()
        return self._status_from_state(state)

    def _require_phase(self, state: Mapping[str, Any], expected: str) -> None:
        if state["phase"] != expected:
            raise SiteCompareError(f"当前 phase={state['phase']!r}，要求 {expected!r}")

    def resolve_final_results(self, run_dir: str | Path) -> Path:
        directory = self._resolve_inside_root(run_dir, label="run_dir")
        for name in RESULT_CANDIDATES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        names = ", ".join(RESULT_CANDIDATES)
        raise SiteCompareError(f"Run 缺少正式最终结果；已检查: {names}: {directory}")

    def _validate_run_complete(self, run_dir: Path, *, label: str) -> dict[str, Any]:
        manifest_path = run_dir / "execution_manifest.json"
        records_path = run_dir / "execution_records.json"
        if not manifest_path.is_file():
            raise SiteCompareError(f"{label} 缺少 execution_manifest.json")
        if not records_path.is_file():
            raise SiteCompareError(f"{label} 缺少 execution_records.json")
        manifest = _json_load(manifest_path, label=f"{label} execution_manifest.json")
        if not isinstance(manifest, Mapping):
            raise SiteCompareError(f"{label} execution_manifest.json 顶层必须是对象")
        selected = _manifest_selected(manifest)
        expected = manifest.get("expected_count")
        if not isinstance(expected, int) or isinstance(expected, bool):
            raise SiteCompareError(f"{label} expected_count 必须是整数")
        if expected != len(selected):
            raise SiteCompareError(f"{label} expected_count 与 selected cases 数量不一致")
        if expected <= 0:
            raise SiteCompareError(f"{label} selected cases 不能为空")

        records_document = _json_load(records_path, label=f"{label} execution_records.json")
        records = _records_from_document(records_document, label=f"{label} execution_records.json")
        if len(records) != expected:
            raise SiteCompareError(f"{label} execution_records 数量不完整: expected={expected}, actual={len(records)}")
        _validate_scope(selected, records, label=label)

        result_path = self.resolve_final_results(run_dir)
        result_document = _json_load(result_path, label=f"{label} 最终结果")
        result_records = _records_from_document(result_document, label=f"{label} 最终结果")
        if len(result_records) != expected:
            raise SiteCompareError(f"{label} 最终结果数量不完整: expected={expected}, actual={len(result_records)}")
        _validate_scope(selected, result_records, label=f"{label} 最终结果")
        try:
            ensure_execution_contract(result_records, manifest)
        except (ExecutionGateError, ValueError) as exc:
            raise SiteCompareError(f"{label} 未通过现有 execution quality gate: {exc}") from exc
        return {
            "run_dir": self._relative(run_dir),
            "manifest": manifest,
            "manifest_path": self._relative(manifest_path),
            "records_path": self._relative(records_path),
            "results_path": self._relative(result_path),
            "selected_cases_sha256": selected_cases_sha256(manifest),
            "manifest_sha256": sha256_file(manifest_path),
        }

    def finish_new(self) -> dict[str, Any]:
        state = self.load_state()
        self._require_phase(state, "RUNNING_NEW")
        run_dir = self._state_path(state["new_run"], label="new_run")
        info = self._validate_run_complete(run_dir, label="New Run")
        state["new_manifest_sha256"] = info["manifest_sha256"]
        state["selected_cases_sha256"] = info["selected_cases_sha256"]
        state["phase"] = "WAITING_FOR_OLD_SITE"
        self.atomic_write_state(state)
        return self._status_from_state(state, next_action="STOP_AND_ASK_USER_TO_SWITCH_SITE")

    def start_old(self) -> dict[str, Any]:
        state = self.load_state()
        self._require_phase(state, "WAITING_FOR_OLD_SITE")
        source = self._resolve_inside_root(state["source"], label="state.source")
        actual_hash = sha256_file(source)
        if actual_hash != state["source_sha256"]:
            raise SiteCompareError("Source changed after new-site run. Old-site comparison cannot continue.")
        old_run = self._state_path(state["old_run"], label="old_run")
        if old_run.exists() and any(old_run.iterdir()):
            raise SiteCompareError(f"Old Run 输出目录已有内容，拒绝覆盖: {old_run}")
        old_run.mkdir(parents=True, exist_ok=True)
        state["phase"] = "RUNNING_OLD"
        self.atomic_write_state(state)
        return self._status_from_state(state, next_action="RUN_OLD_SITE") | {
            "output": state["old_run"],
        }

    def finish_old(self) -> dict[str, Any]:
        state = self.load_state()
        self._require_phase(state, "RUNNING_OLD")
        old_run = self._state_path(state["old_run"], label="old_run")
        info = self._validate_run_complete(old_run, label="Old Run")
        if info["selected_cases_sha256"] != state["selected_cases_sha256"]:
            raise SiteCompareError("New / Old selected cases 不一致，禁止进入 Compare")
        state["phase"] = "READY_TO_COMPARE"
        self.atomic_write_state(state)
        return self._status_from_state(state, next_action="COMPARE_NEW_AND_OLD")

    def compare_inputs(self) -> dict[str, Any]:
        state = self.load_state()
        self._require_phase(state, "READY_TO_COMPARE")
        new_run = self._state_path(state["new_run"], label="new_run")
        old_run = self._state_path(state["old_run"], label="old_run")
        new_results = self.resolve_final_results(new_run)
        old_results = self.resolve_final_results(old_run)
        comparison_output = self._state_path(
            f"{state['comparison_dir']}/comparison.json",
            label="comparison_output",
        )
        return {
            "ok": True,
            "phase": state["phase"],
            "app": state.get("app"),
            "source": state["source"],
            "new_results": self._relative(new_results),
            "new_records": self._relative(new_run / "execution_records.json"),
            "old_results": self._relative(old_results),
            "old_records": self._relative(old_run / "execution_records.json"),
            "comparison_output": self._relative(comparison_output),
            "next_action": "COMPARE_NEW_AND_OLD",
        }

    def _validate_comparison(self, comparison_file: Path, state: Mapping[str, Any]) -> None:
        document = _json_load(comparison_file, label="comparison.json")
        if not isinstance(document, Mapping):
            raise SiteCompareError("comparison.json 顶层必须是对象")
        if document.get("version") != 1:
            raise SiteCompareError("comparison.json version 必须为 1")
        cases = document.get("cases")
        if not isinstance(cases, list) or not all(isinstance(item, Mapping) for item in cases):
            raise SiteCompareError("comparison.json 必须包含 cases 对象列表")
        for index, item in enumerate(cases, start=1):
            key = item.get("case_key")
            status = item.get("status")
            if not isinstance(key, str) or not key.strip():
                raise SiteCompareError(f"comparison cases[{index}] 缺少 case_key")
            if status not in COMPARISON_STATUSES:
                raise SiteCompareError(f"comparison cases[{index}] status 非法: {status!r}")
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                raise SiteCompareError(f"comparison cases[{index}] 缺少 reason")

        new_manifest = _json_load(self._state_path(state["new_run"], label="new_run") / "execution_manifest.json", label="New manifest")
        old_manifest = _json_load(self._state_path(state["old_run"], label="old_run") / "execution_manifest.json", label="Old manifest")
        new_selected = _manifest_selected(new_manifest) if isinstance(new_manifest, Mapping) else []
        expected_items = new_selected
        if selected_cases_sha256(old_manifest) != state["selected_cases_sha256"]:
            raise SiteCompareError("Old manifest 与 state.selected_cases_sha256 不一致")
        if len(cases) != len(expected_items):
            raise SiteCompareError(f"comparison 缺 Case 或存在多余 Case: expected={len(expected_items)}, actual={len(cases)}")
        expected_aliases = [_identity_aliases(item) for item in expected_items]
        seen: set[str] = set()
        for item in cases:
            aliases = _identity_aliases(item["case_key"])
            matches = [index for index, candidate in enumerate(expected_aliases) if aliases.intersection(candidate)]
            if not matches:
                raise SiteCompareError(f"comparison 包含未知 Case: {item['case_key']}")
            identity = _canonical_identity(expected_items[matches[0]])
            if identity in seen:
                raise SiteCompareError(f"comparison 存在重复 Case: {item['case_key']}")
            seen.add(identity)
        if len(seen) != len(expected_items):
            raise SiteCompareError("comparison 未覆盖全部 New / Old Case")

    def complete(self, comparison_file: str | Path) -> dict[str, Any]:
        state = self.load_state()
        self._require_phase(state, "READY_TO_COMPARE")
        comparison_dir = self._state_path(state["comparison_dir"], label="comparison_dir")
        path = self._state_path(comparison_file, label="comparison_file")
        if not _within(path, comparison_dir):
            raise SiteCompareError("comparison-file 必须位于 controller 返回的 comparison_dir 内")
        self._validate_comparison(path, state)
        state["comparison_file"] = self._relative(path)
        state["phase"] = "COMPLETED"
        self.atomic_write_state(state)
        return self._status_from_state(state, next_action="DONE")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="sixgill 新旧站点数据对比状态控制器")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="创建 RUNNING_NEW 工作流")
    init.add_argument("--source", help="Excel 用例文件；未提供时从 App 的 site_compare_source/default_source 解析")
    init.add_argument("--app", help="sixgill App slug；会持久化到 state.json，实际运行建议必填")

    status = sub.add_parser("status", help="读取当前工作流事实")
    status.add_argument("--json", action="store_true", help="兼容协议文档；输出始终为 JSON")
    sub.add_parser("finish-new", help="校验 New Run 并进入等待切站")
    sub.add_parser("start-old", help="校验 source 并开始 Old Run")
    sub.add_parser("finish-old", help="校验 Old Run 与 New scope 并进入 Compare")
    compare_inputs = sub.add_parser("compare-inputs", help="返回逐 Case 比较所需文件")
    compare_inputs.add_argument("--json", action="store_true", help="兼容协议文档；输出始终为 JSON")
    complete = sub.add_parser("complete", help="校验 comparison.json 并完成工作流")
    complete.add_argument("--comparison-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    controller = SiteCompareController()
    try:
        if args.command == "init":
            result = controller.init(source=args.source, app=args.app)
        elif args.command == "status":
            result = controller.status()
        elif args.command == "finish-new":
            result = controller.finish_new()
        elif args.command == "start-old":
            result = controller.start_old()
        elif args.command == "finish-old":
            result = controller.finish_old()
        elif args.command == "compare-inputs":
            result = controller.compare_inputs()
        elif args.command == "complete":
            result = controller.complete(args.comparison_file)
        else:  # pragma: no cover - argparse enforces choices
            raise SiteCompareError(f"未知命令: {args.command}")
    except (SiteCompareError, OSError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
