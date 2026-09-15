"""Create an evidence-only LLM review of one six-sheet run.

This is intentionally a review artifact generator. It does not call reback and
does not modify apps/guotou/profile.yaml. Navigation candidates may be
approved after repeated evidence; successful runtime-recovery candidates are
kept as hold records until separately confirmed.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any


def _exists(path: str, base_dir: Path | None = None) -> bool:
    candidate = Path(path.replace("/", "\\"))
    if not candidate.is_absolute() and base_dir is not None:
        candidate = base_dir / candidate
    return candidate.is_file()


def _recommended_key(sheet: str, path: str) -> str:
    if sheet == "股指":
        return "quote.guzhi_list" if "国内指数" in path else "quote.index_extended"
    if sheet == "沪深京":
        if "看资金" in path:
            return "quote.fund_flow_extended"
        if any(token in path for token in ("涨幅榜", "跌幅榜", "换手率", "量比", "成交额", "涨停分析")):
            return "quote.hsj_rankings"
        return "quote.hsj_stock_list"
    if sheet == "板块":
        return "quote.sector_lists" if any(token in path for token in ("行业板块", "概念板块", "地域板块", "板块列表")) else "quote.sector_main"
    if sheet == "港股":
        if "AH股" in path:
            return "quote.hk_ah_list"
        if "港股创业板" in path:
            return "quote.hk_board_growth"
        if "港股主板" in path:
            return "quote.hk_board_main"
        if "沪、深港通" in path or "港股通" in path:
            return "quote.hk_connect_and_boards"
        return "quote.hk_main"
    if sheet == "其他":
        return "quote.other_market_lists" if any(token in path for token in ("全球市场", "基金", "个股", "债券")) else "quote.other_main"
    if sheet == "看资金":
        return "quote.fund_flow_extended"
    return ""


def _review_candidate(
    sheet: str,
    candidate: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    evidence = [str(value) for value in candidate.get("evidence", []) if value]
    existing = [_exists(value, base_dir) for value in evidence]
    evidence_complete = bool(evidence) and all(existing)
    signatures = [str(value) for value in candidate.get("observed_page_signatures", []) if value]
    usable_signature = bool(signatures) and not any("未获取" in value for value in signatures)
    success = int(candidate.get("success_count") or 0)
    observed = int(candidate.get("observed_count") or 0)
    repeated_success = success == observed and observed >= 2
    path = str(candidate.get("display_path") or "")
    recommended_key = _recommended_key(sheet, path)

    if not evidence_complete or not usable_signature or success == 0:
        verdict = "reject"
        confidence = 0.98
        reason = "本轮没有足够的成功证据，或截图/页面签名不完整；不支持写入画像。"
    elif repeated_success:
        verdict = "approve_as_update"
        confidence = 0.94
        reason = "至少两条用例成功且证据齐全；建议更新已有画像条目，避免新增哈希键。"
    else:
        verdict = "hold"
        confidence = 0.86
        reason = "有成功证据，但只有单次观察或同组存在失败/阻塞；先保留候选，补充复测后再升级。"

    return verdict, {
        "verdict": verdict,
        "confidence": confidence,
        "evidence_complete": evidence_complete,
        "usable_page_signature": usable_signature,
        "success_count": success,
        "observed_count": observed,
        "recommended_profile_key": recommended_key,
        "eligible_for_reback": verdict == "approve_as_update",
        "reason": reason,
    }


def _review_recovery_candidate(
    candidate: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """Review a successful exception without promoting it automatically."""

    evidence = [str(value) for value in candidate.get("evidence", []) if value]
    evidence_complete = bool(evidence) and all(_exists(value, base_dir) for value in evidence)
    page_observation = str(candidate.get("page_observation") or "").strip()
    actions = candidate.get("recovery_actions") or []
    has_recovery_action = isinstance(actions, list) and bool(actions)

    if not evidence_complete or not page_observation:
        return "reject", {
            "verdict": "reject",
            "confidence": 0.98,
            "candidate_type": "runtime_recovery_success",
            "evidence_complete": evidence_complete,
            "usable_page_signature": bool(page_observation),
            "recovery_action_present": has_recovery_action,
            "eligible_for_reback": False,
            "reason": "异常恢复候选缺少完整截图或页面观察证据；不支持写入画像。",
        }

    return "hold", {
        "verdict": "hold",
        "confidence": 0.9,
        "candidate_type": "runtime_recovery_success",
        "evidence_complete": True,
        "usable_page_signature": True,
        "recovery_action_present": has_recovery_action,
        "eligible_for_reback": False,
        "reason": "异常已成功处理，但单次恢复不能直接改变正式画像；需重复观察并人工确认后再生成 reback 更新。",
    }


def review(root: Path) -> dict[str, Any]:
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    review_date = date.today().isoformat()
    agent_name = os.environ.get("SIXGILL_REVIEW_AGENT_NAME", "configured-agent")
    agent_model = os.environ.get("SIXGILL_REVIEW_MODEL", "configured-model")
    prompt_version = os.environ.get("SIXGILL_REVIEW_PROMPT_VERSION", "profile-candidate-review-v1")
    aggregate = {
        "approve_as_update": 0,
        "hold": 0,
        "reject": 0,
        "candidate_count": 0,
        "missing_evidence_paths": 0,
        "recovery_candidate_count": 0,
        "recovery_hold": 0,
        "recovery_reject": 0,
    }
    sheets: dict[str, Any] = {}
    approved: list[dict[str, Any]] = []

    for sheet, meta in summary["sheets"].items():
        run_dir = Path(meta["run_dir"])
        source = json.loads((run_dir / "profile_feedback.json").read_text(encoding="utf-8"))
        reviewed = dict(source)
        reviewed["llm_review"] = {
            "agent": agent_name,
            "model": agent_model,
            "prompt_version": prompt_version,
            "reviewed_at": review_date,
            "scope": "evidence-only navigation and runtime-recovery candidates",
        }
        reviewed_candidates = []
        counts = {"approve_as_update": 0, "hold": 0, "reject": 0}
        missing_paths = 0
        for candidate in source.get("candidates", []):
            item = dict(candidate)
            verdict, decision = _review_candidate(sheet, candidate, base_dir=run_dir)
            item["llm_review"] = decision
            reviewed_candidates.append(item)
            if verdict == "approve_as_update":
                approved.append(
                    {
                        "sheet": sheet,
                        "candidate_index": len(reviewed_candidates),
                        "profile_key": decision["recommended_profile_key"],
                        "observed_path": candidate.get("display_path"),
                        "observed_rows": candidate.get("observed_rows", []),
                        "success_count": candidate.get("success_count", 0),
                        "observed_count": candidate.get("observed_count", 0),
                        "evidence": candidate.get("evidence", []),
                        "evidence_run": candidate.get("suggested_profile_entry", {}).get("evidence_run"),
                        "proposed_merge": {
                            "last_verified": candidate.get("suggested_profile_entry", {}).get("last_verified"),
                            "app_version": candidate.get("suggested_profile_entry", {}).get("app_version"),
                            "evidence_run": candidate.get("suggested_profile_entry", {}).get("evidence_run"),
                            "status": "preserve_existing",
                            "path": "preserve_existing",
                        },
                    }
                )
            counts[verdict] += 1
            missing_paths += sum(
                not _exists(str(value), run_dir)
                for value in candidate.get("evidence", [])
                if value
            )
        reviewed["candidates"] = reviewed_candidates
        reviewed_recovery_candidates = []
        recovery_counts = {"approve_as_update": 0, "hold": 0, "reject": 0}
        recovery_missing_paths = 0
        for candidate in source.get("recovery_candidates", []):
            item = dict(candidate)
            verdict, decision = _review_recovery_candidate(candidate, base_dir=run_dir)
            item["llm_review"] = decision
            reviewed_recovery_candidates.append(item)
            recovery_counts[verdict] += 1
            recovery_missing_paths += sum(
                not _exists(str(value), run_dir)
                for value in candidate.get("evidence", [])
                if value
            )
        reviewed["recovery_candidates"] = reviewed_recovery_candidates
        reviewed["recovery_candidate_count"] = len(reviewed_recovery_candidates)
        reviewed["review_summary"] = {
            **counts,
            "candidate_count": len(reviewed_candidates),
            "missing_evidence_paths": missing_paths,
            "recovery_candidate_count": len(reviewed_recovery_candidates),
            "recovery_hold": recovery_counts["hold"],
            "recovery_reject": recovery_counts["reject"],
            "recovery_missing_evidence_paths": recovery_missing_paths,
            "reback_run": "not_run",
        }
        out = run_dir / "profile_feedback.reviewed.json"
        out.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sheets[sheet] = {
            "run_dir": str(run_dir),
            "candidate_count": len(reviewed_candidates),
            **counts,
            "missing_evidence_paths": missing_paths,
            "recovery_candidate_count": len(reviewed_recovery_candidates),
            "recovery_hold": recovery_counts["hold"],
            "recovery_reject": recovery_counts["reject"],
            "recovery_missing_evidence_paths": recovery_missing_paths,
            "reviewed_file": str(out),
        }
        for key in counts:
            aggregate[key] += counts[key]
        aggregate["candidate_count"] += len(reviewed_candidates)
        aggregate["missing_evidence_paths"] += missing_paths
        aggregate["recovery_candidate_count"] += len(reviewed_recovery_candidates)
        aggregate["recovery_hold"] += recovery_counts["hold"]
        aggregate["recovery_reject"] += recovery_counts["reject"]
        aggregate["missing_evidence_paths"] += recovery_missing_paths

    result = {
        "schema_version": "1.0",
        "run_root": str(root.resolve()),
        "source": summary.get("source"),
        "app": summary.get("app"),
        "review": {
            "agent": agent_name,
            "model": agent_model,
            "prompt_version": prompt_version,
            "reviewed_at": review_date,
            "basis": "仅依据 profile_feedback 中的导航候选和 runtime_recovery_success 的诊断、恢复动作、页面观察、轨迹和 evidence 审核",
            "profile_write": "not_run",
        },
        "aggregate": aggregate,
        "sheets": sheets,
    }
    (root / "profile_review_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    plan = {
        "schema_version": "1.0",
        "run_root": str(root.resolve()),
        "app": summary.get("app"),
        "source": summary.get("source"),
        "status": "preview_only",
        "profile_write": "not_run",
        "instruction": "以下是审核通过后建议合并到已有画像键的内容；runtime_recovery_success 默认只保留为 hold 候选，必须重复观察并再次确认，写回前需通过 reback/schema/lint。",
        "approved_updates": approved,
    }
    (root / "profile_reback_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in approved:
        grouped.setdefault(item["profile_key"], []).append(item)
    md: list[str] = [
        "# 画像反哺预览（未写回）",
        "",
        f"- 候选更新数：{len(approved)}",
        "- 写回状态：未执行 reback",
        "- 处理原则：只补充已有画像键的验证时间和证据引用，保留现有画像 path/status，避免新增哈希键",
        "",
    ]
    for key, items in grouped.items():
        md.append(f"## {key}")
        md.append("")
        md.append(f"共 {len(items)} 条证据候选。")
        md.append("")
        for item in items:
            rows = ", ".join(f"{row.get('sheet')}第{row.get('row')}行" for row in item.get("observed_rows", []))
            md.append(f"- **{item.get('sheet')}**：{item.get('observed_path')}（{item.get('success_count')}/{item.get('observed_count')} 成功，{len(item.get('evidence', []))} 张证据，{rows}）")
        md.append("")
    (root / "profile_reback_plan.md").write_text("\n".join(md), encoding="utf-8")
    return result


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "output/2026-09-08-guotou-six-sheets")
    print(json.dumps(review(root), ensure_ascii=False, indent=2))
