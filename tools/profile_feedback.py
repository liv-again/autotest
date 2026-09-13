"""Build evidence-backed App profile feedback from a completed run.

The feedback file is intentionally a candidate document.  It does not write
``apps/<app>/profile.yaml`` by itself.  A later LLM review or an explicit
``reback_run`` call can promote a candidate after schema, version, duplicate,
and evidence checks pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping


CN_TZ = timezone(timedelta(hours=8))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _records(document: Mapping[str, Any] | list[Any]) -> list[dict[str, Any]]:
    values = document.get("cases") or document.get("results") or [] if isinstance(document, Mapping) else document
    return [item for item in values if isinstance(item, dict)]


def _candidate_key(app_slug: str, group_key: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", ".", group_key.casefold()).strip(".")
    if not compact:
        compact = hashlib.sha1(group_key.encode("utf-8")).hexdigest()[:12]
    return f"quote.{app_slug}.{compact}"


def _status_is_success(status: Any) -> bool:
    value = _text(status)
    return "✅" in value or "通过" in value or "成功" in value


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _runtime_recovery_attempts(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    runtime_recovery = record.get("runtime_recovery")
    if not isinstance(runtime_recovery, Mapping):
        return []
    attempts = runtime_recovery.get("attempts")
    if not isinstance(attempts, list):
        return []
    return [attempt for attempt in attempts if isinstance(attempt, dict)]


def _successful_runtime_recovery(attempt: Mapping[str, Any]) -> bool:
    return _text(attempt.get("result")).casefold() == "recovered"


def build_profile_feedback(
    document: Mapping[str, Any] | list[Any],
    *,
    run_dir: str | Path,
    app_slug: str,
    app_version: str,
) -> dict[str, Any]:
    """Return navigation candidates and successful runtime-recovery signals.

    Runtime recovery is recorded separately from ordinary navigation evidence:
    a popup being handled successfully is useful App-profile evidence, but a
    single occurrence must remain a candidate and must not silently rewrite
    the formal profile.
    """

    grouped: "OrderedDict[tuple[str, str], list[dict[str, Any]]]" = OrderedDict()
    for record in _records(document):
        group_id = _text(record.get("page_group_id")) or f"{record.get('sheet')}!{record.get('row')}"
        group_key = _text(record.get("page_group_key")) or _text(record.get("sheet"))
        grouped.setdefault((group_id, group_key), []).append(record)

    run_path = Path(run_dir).expanduser().resolve()
    feedback: list[dict[str, Any]] = []
    recovery_candidates: list[dict[str, Any]] = []
    for (group_id, group_key), records in grouped.items():
        context = next(
            (record.get("navigation_context") for record in records if isinstance(record.get("navigation_context"), dict)),
            {},
        )
        display_path = _text(context.get("display_path")) or group_key.replace("|", " / ")
        recovery_trace_file = str(run_path / "runtime_recovery_trace.jsonl").replace("\\", "/")
        for record in records:
            for attempt_index, attempt in enumerate(_runtime_recovery_attempts(record), start=1):
                if not _successful_runtime_recovery(attempt):
                    continue
                record_evidence = [
                    _text(path).replace("\\", "/")
                    for path in (record.get("evidence") or record.get("evidence_paths") or [])
                ]
                recovery_screenshot = _text(attempt.get("screenshot")).replace("\\", "/")
                recovery_evidence = _unique([recovery_screenshot, *record_evidence])
                tested_at = _text(record.get("tested_at"))[:10]
                action_plan_case = record.get("action_plan_case")
                target_page = (
                    action_plan_case.get("target_page")
                    if isinstance(action_plan_case, Mapping)
                    else None
                )
                recovery_candidates.append(
                    {
                        "feedback_id": (
                            f"runtime-recovery:{record.get('case_id') or group_id}:"
                            f"{attempt.get('attempt') or attempt_index}"
                        ),
                        "type": "runtime_recovery_success",
                        "page_group_id": group_id,
                        "page_group_key": group_key,
                        "sheet": record.get("sheet"),
                        "row": record.get("row"),
                        "case_id": record.get("case_id"),
                        "display_path": display_path,
                        "diagnosis": _text(attempt.get("diagnosis")),
                        "reason": _text(attempt.get("reason")),
                        "failure_phase": _text(attempt.get("failure_phase")),
                        "failure_detail": _text(attempt.get("failure_detail")),
                        "decision": _text(attempt.get("decision")),
                        "reset": _text(attempt.get("reset")),
                        "replay_safety": _text(attempt.get("replay_safety")),
                        "takeover_scope": _text(attempt.get("takeover_scope")) or "single_excel_row",
                        "takeover_mode": _text(attempt.get("takeover_mode")) or "until_case_terminal",
                        "recovery_turn": attempt.get("attempt") or attempt_index,
                        "recovery_actions": attempt.get("actions") or [],
                        "recovery_action_trace": attempt.get("recovery_action_trace") or [],
                        "replay_trace": attempt.get("replay_trace") or [],
                        "recovery_screenshot": recovery_screenshot,
                        "screenshot_ok": bool(attempt.get("screenshot_ok")),
                        "runtime_recovery_trace": recovery_trace_file,
                        "page_observation": _text(record.get("page_observation")),
                        "target_page": target_page,
                        "evidence": recovery_evidence,
                        "candidate_status": "unverified",
                        "confidence": "candidate",
                        "suggested_profile_update": {
                            "key": _candidate_key(app_slug, group_key),
                            "path": display_path,
                            "last_observed": tested_at or datetime.now(CN_TZ).date().isoformat(),
                            "app_version": app_version,
                            "evidence_run": str(run_path).replace("\\", "/"),
                            "signal": "runtime_recovery_success",
                            "status": "candidate",
                        },
                    }
                )

        evidence = _unique(
            [
                _text(path).replace("\\", "/")
                for record in records
                for path in (record.get("evidence") or record.get("evidence_paths") or [])
            ]
        )
        usable = [record for record in records if record.get("action_trace") and evidence]
        if not usable:
            continue
        observations = _unique([_text(record.get("page_observation")) for record in usable])[:5]
        statuses = [_text(record.get("status")) for record in usable]
        success_count = sum(1 for status in statuses if _status_is_success(status))
        candidate_status = "verified" if success_count == len(usable) and len(usable) >= 2 else "unverified"
        dates = sorted(_text(record.get("tested_at"))[:10] for record in usable if _text(record.get("tested_at")))
        last_verified = dates[-1] if dates else datetime.now(CN_TZ).date().isoformat()
        suggested = {
            "key": _candidate_key(app_slug, group_key),
            "path": display_path,
            "last_verified": last_verified,
            "app_version": app_version,
            "evidence_run": str(run_path).replace("\\", "/"),
            "status": candidate_status,
        }
        feedback.append(
            {
                "feedback_id": f"{group_id}:{group_key}",
                "type": "navigation_context",
                "page_group_id": group_id,
                "page_group_key": group_key,
                "navigation_context": context,
                "profile_hints": next(
                    (record.get("profile_hints") for record in records if record.get("profile_hints")),
                    [],
                ),
                "display_path": display_path,
                "observed_page_signatures": observations,
                "observed_rows": [
                    {"sheet": record.get("sheet"), "row": record.get("row"), "case_id": record.get("case_id")}
                    for record in usable
                ],
                "evidence": evidence,
                "success_count": success_count,
                "observed_count": len(usable),
                "runtime_recovery_success_count": sum(
                    1
                    for record in records
                    for attempt in _runtime_recovery_attempts(record)
                    if _successful_runtime_recovery(attempt)
                ),
                "confidence": "high" if candidate_status == "verified" else "candidate",
                "suggested_profile_entry": suggested,
            }
        )

    return {
        "schema_version": "1.0",
        "feedback_type": "app_profile_candidates",
        "app_slug": app_slug,
        "app_version": app_version,
        "source": "row_execution_with_page_groups_and_runtime_recovery",
        "direct_profile_write": False,
        "llm_instruction": (
            "只根据 evidence 和 observed_page_signatures 审核候选画像；"
            "runtime_recovery_success 只表示本次异常被处理成功，需结合恢复截图、"
            "恢复动作轨迹和重复运行确认后，才能通过 reback_run、schema 和 lint 更新正式画像。"
        ),
        "candidate_count": len(feedback),
        "recovery_candidate_count": len(recovery_candidates),
        "candidates": feedback,
        "recovery_candidates": recovery_candidates,
    }


def _load(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, (dict, list)):
        raise ValueError("输入结果必须是对象或数组")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="从执行记录生成带证据的画像候选反馈")
    parser.add_argument("--input", required=True, help="execution_records.json 或 results.json")
    parser.add_argument("--out", required=True, help="profile_feedback.json")
    parser.add_argument("--app-slug", required=True)
    parser.add_argument("--app-version", required=True)
    args = parser.parse_args()
    output = Path(args.out).expanduser().resolve()
    result = build_profile_feedback(
        _load(Path(args.input).expanduser().resolve()),
        run_dir=output.parent,
        app_slug=args.app_slug,
        app_version=args.app_version,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(output),
                "candidates": result["candidate_count"],
                "recovery_candidates": result["recovery_candidate_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
