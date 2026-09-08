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


def build_profile_feedback(
    document: Mapping[str, Any] | list[Any],
    *,
    run_dir: str | Path,
    app_slug: str,
    app_version: str,
) -> dict[str, Any]:
    """Return structured page/navigation candidates with row evidence."""

    grouped: "OrderedDict[tuple[str, str], list[dict[str, Any]]]" = OrderedDict()
    for record in _records(document):
        group_id = _text(record.get("page_group_id")) or f"{record.get('sheet')}!{record.get('row')}"
        group_key = _text(record.get("page_group_key")) or _text(record.get("sheet"))
        grouped.setdefault((group_id, group_key), []).append(record)

    run_path = Path(run_dir).expanduser().resolve()
    feedback: list[dict[str, Any]] = []
    for (group_id, group_key), records in grouped.items():
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
        context = next(
            (record.get("navigation_context") for record in records if isinstance(record.get("navigation_context"), dict)),
            {},
        )
        display_path = _text(context.get("display_path")) or group_key.replace("|", " / ")
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
                "confidence": "high" if candidate_status == "verified" else "candidate",
                "suggested_profile_entry": suggested,
            }
        )

    return {
        "schema_version": "1.0",
        "feedback_type": "app_profile_candidates",
        "app_slug": app_slug,
        "app_version": app_version,
        "source": "row_execution_with_page_groups",
        "direct_profile_write": False,
        "llm_instruction": (
            "只根据 evidence 和 observed_page_signatures 审核候选画像；"
            "一次异常不得覆盖正式画像，确认后必须通过 reback_run、schema 和 lint。"
        ),
        "candidate_count": len(feedback),
        "candidates": feedback,
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
    print(json.dumps({"out": str(output), "candidates": result["candidate_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
