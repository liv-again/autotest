"""Crash-safe, row-scoped execution journal.

The journal is intentionally independent of any Android driver. It lets a
module planner and a deterministic row executor share one persistence and
resume contract without calling an LLM for every case.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping


CN_TZ = timezone(timedelta(hours=8))


class JournalError(ValueError):
    """Raised when a row result cannot be persisted safely."""


def _now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _key(record: Mapping[str, Any]) -> tuple[str, int]:
    sheet = str(record.get("sheet") or "").strip()
    row = record.get("row")
    if not sheet or row in (None, ""):
        raise JournalError("结果必须包含 sheet + row")
    try:
        return sheet, int(row)
    except (TypeError, ValueError) as exc:
        raise JournalError("结果 row 必须是整数") from exc


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class ExecutionJournal:
    """Append one case at a time and maintain a resumable state file."""

    def __init__(self, run_dir: str | Path, manifest: Mapping[str, Any], *, resume: bool = False) -> None:
        self.run_dir = Path(run_dir).expanduser().resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = dict(manifest)
        self.journal_path = self.run_dir / "execution_records.jsonl"
        self.state_path = self.run_dir / "execution_state.json"
        self.final_path = self.run_dir / "execution_records.json"
        self._records = self._load_records()
        self._keys = {_key(record) for record in self._records}
        if self._records and not resume:
            raise JournalError(f"已有执行记录，请使用 resume 或新建运行目录: {self.journal_path}")
        if not resume:
            self._write_state("running")
        elif not self.state_path.exists():
            self._write_state("running")

    def _load_records(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.journal_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JournalError(f"execution_records.jsonl 第 {line_number} 行损坏") from exc
            if not isinstance(value, dict):
                raise JournalError(f"execution_records.jsonl 第 {line_number} 行不是对象")
            records.append(value)
        return records

    @property
    def records(self) -> list[dict[str, Any]]:
        return list(self._records)

    @property
    def completed_count(self) -> int:
        return len(self._records)

    def has_case(self, sheet: str, row: int) -> bool:
        return (str(sheet), int(row)) in self._keys

    def _write_state(self, status: str, *, last_record: Mapping[str, Any] | None = None) -> None:
        state = {
            "status": status,
            "selected_count": int(self.manifest.get("expected_count") or len(self.manifest.get("selected_cases") or [])),
            "completed_count": len(self._records),
            "pending_count": max(
                0,
                int(self.manifest.get("expected_count") or len(self.manifest.get("selected_cases") or [])) - len(self._records),
            ),
            "old_results_used": False,
            "journal_path": self.journal_path.name,
            "updated_at": _now(),
        }
        if last_record:
            state["last_completed"] = {
                "sheet": last_record.get("sheet"),
                "row": last_record.get("row"),
                "case_id": last_record.get("case_id"),
                "execution_order": last_record.get("execution_order"),
            }
        _atomic_json(self.state_path, state)

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(record)
        key = _key(item)
        for field in ("case_id", "source_order", "execution_order", "status"):
            if item.get(field) in (None, ""):
                raise JournalError(f"{key[0]}!{key[1]} 缺少 {field}")
        if key in self._keys:
            raise JournalError(f"重复写入用例: {key[0]}!{key[1]}")
        item["persisted_at"] = _now()
        with self.journal_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._records.append(item)
        self._keys.add(key)
        self._write_state("running", last_record=item)
        return item

    def mark_paused(self, *, reason: str = "") -> None:
        self._write_state("paused", last_record=self._records[-1] if self._records else None)
        if reason:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            state["pause_reason"] = reason
            _atomic_json(self.state_path, state)

    def finalize(self, *, setup_trace: list[Mapping[str, Any]] | None = None) -> Path:
        if len(self._records) != int(self.manifest.get("expected_count") or len(self.manifest.get("selected_cases") or [])):
            raise JournalError("结果记录未覆盖全部选中用例，不能生成正式最终执行记录")
        document = {
            "schema_version": "2.0",
            "execution_manifest": self.manifest,
            "setup_trace": list(setup_trace or []),
            "cases": self._records,
        }
        _atomic_json(self.final_path, document)
        self._write_state("complete", last_record=self._records[-1] if self._records else None)
        return self.final_path

