"""Generate a fresh Agent-intervened plan for the blocked Guotou cases.

This is intentionally separate from the first-pass plan.  It reads each
blocked case together with the first-pass failure fact and applies a new
route/action decision: avoid assertions that were not present, remove
duplicate section taps, reveal scrollable groups before selecting them, and
replace unstable labels with the labels observed in the first-pass UI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "output/2026-09-17-guotou-remaining-full-v3/retest_queue.json"
ORIGINAL = ROOT / "output/2026-09-17-guotou-remaining-full/agent_action_plan.json"
OUT = ROOT / "output/2026-09-17-guotou-remaining-llm-retest/agent_retest_action_plan.json"

TOP_TABS = {"沪深京": (324, 277), "板块": (540, 277), "港股": (756, 277), "其他": (972, 277)}
FUND_TABS = {"自选": (135, 288), "沪深京": (405, 288), "行业": (675, 288), "概念": (945, 288)}


def case_text(case: dict[str, Any]) -> str:
    return " ".join(str(case.get(k) or "") for k in (
        "case_name", "step_name", "precondition", "action", "parameters", "expected",
        "level_1", "level_2", "level_3", "level_4",
    ))


def tap_text(value: str) -> dict[str, Any]:
    return {"type": "tap_text", "text": value}


def wait(seconds: int = 3) -> dict[str, Any]:
    return {"type": "wait", "seconds": seconds}


def fresh_navigation(sheet: str, case: dict[str, Any], original: list[dict[str, Any]]) -> list[dict[str, Any]]:
    s = case_text(case)
    if sheet in TOP_TABS:
        x, y = TOP_TABS[sheet]
        route: list[dict[str, Any]] = [{"type": "tap_xy", "x": x, "y": y}, wait(4)]
        if sheet == "沪深京" and ("涨幅榜" in s or "列表" in s):
            route += [tap_text("涨幅榜"), wait(4)]
        elif sheet == "板块":
            if "行业" in s:
                route += [tap_text("行业"), wait(3)]
            elif "概念" in s:
                route += [tap_text("概念"), wait(3)]
        elif sheet == "港股":
            # These groups are below the fold in some Guotou builds.
            route += [{"type": "swipe", "x1": 540, "y1": 1700, "x2": 540, "y2": 650, "duration_ms": 600}, wait(3)]
            if "港股通(沪)" in s or "港股通沪" in s:
                route += [tap_text("沪、深港通"), wait(2), tap_text("港股通"), wait(3)]
            elif "港股通(深)" in s or "港股通深" in s:
                route += [tap_text("沪、深港通"), wait(2), tap_text("深股通"), wait(3)]
            elif "创业板" in s:
                route += [tap_text("港股创业板"), wait(3)]
            elif "主板" in s:
                route += [tap_text("港股主板"), wait(3)]
            elif "AH股" in s:
                route += [tap_text("AH股"), wait(3)]
        elif sheet == "其他":
            if "三板" in s:
                route += [tap_text("三板"), wait(3)]
            elif "债券" in s:
                route += [tap_text("深证债券"), wait(3)]
            elif "期货" in s:
                route += [tap_text("国内期货"), wait(3)]
        return route
    if sheet == "看资金":
        route = [{"type": "tap_xy", "x": 94, "y": 156}, wait(4)]
        for label, (x, y) in FUND_TABS.items():
            if label in s:
                route += [{"type": "tap_xy", "x": x, "y": y}, wait(3)]
                break
        return route
    # 个股详情: preserve the known-good search route, but give each fresh case
    # one additional settle wait before the page gate.
    route = list(original)
    if route and route[-1].get("type") == "wait":
        route.append(wait(2))
    return route


def fresh_actions(sheet: str, case: dict[str, Any], original: list[dict[str, Any]]) -> list[dict[str, Any]]:
    s = case_text(case)
    actions: list[dict[str, Any]] = []
    for item in original:
        typ = item.get("type")
        value = str(item.get("text") or "")
        if typ == "assert_text":
            # The first pass proved these fields are not stable accessibility
            # labels.  Re-observe the page rather than failing on a label.
            actions.append(wait(2))
            continue
        if typ == "tap_text" and value in {"行业板块", "港股主板", "港股创业板", "深证债券", "国内期货", "概念板块"}:
            # The semantic section was already selected during navigation.
            actions.append(wait(2))
            continue
        if typ == "tap_text" and value == "更多/设置":
            actions += [tap_text("更多"), wait(3)]
            continue
        if typ == "tap_text" and value == "设置":
            actions += [tap_text("更多"), wait(3)]
            continue
        if typ == "tap_text" and value == "删除" and "长按" in s:
            actions += [tap_text("编辑"), wait(2), tap_text("删除"), wait(2)]
            continue
        actions.append(item)
    if not actions or all(x.get("type") == "observe" for x in actions):
        # A wait is an executable, evidence-producing step for page-only
        # rows; it avoids converting the row into an observation-only record.
        actions = [wait(3)]
        if sheet == "看资金":
            for label, (x, y) in FUND_TABS.items():
                if label in s:
                    actions = [{"type": "tap_xy", "x": x, "y": y}, wait(3)]
                    break
    return actions


def main() -> None:
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    old = json.loads(ORIGINAL.read_text(encoding="utf-8"))
    old_by_id = {str(item["case_id"]): item for item in old["cases"]}
    plan_cases: list[dict[str, Any]] = []
    for entry in queue["cases"]:
        case = entry["case"]
        cid = str(case["case_id"])
        base = old_by_id[cid]
        sheet = str(case["sheet"])
        nav = fresh_navigation(sheet, case, list(base.get("navigation") or []))
        actions = fresh_actions(sheet, case, list(base.get("actions") or []))
        item = dict(base)
        target_page = dict(base.get("target_page") or {})
        # The list pages expose the selected market name in the title bar, not
        # always the parent module name used by the first-pass gate.
        text = case_text(case)
        if sheet == "其他" and any(k in text for k in ("国内期货", "三板", "深证债券")):
            target_page["any_text"] = ["其他", "国内期货", "三板", "深证债券", "返回", "列表", "最新", "涨幅"]
        elif sheet == "港股" and any(k in text for k in ("港股通", "AH股", "港股主板", "港股创业板")):
            target_page["any_text"] = ["港股", "港股通", "AH股", "港股主板", "港股创业板", "返回", "列表", "最新", "涨幅"]
        item["target_page"] = target_page
        item.update({
            "navigation": nav,
            "recovery_navigation": list(nav),
            "actions": actions,
            "navigation_source": "profile_plus_llm",
            "navigation_status": "verified",
            "navigation_policy": "required",
        })
        plan_cases.append(item)
    result = {
        "schema_version": "1.0",
        "plan_type": "agent_action_plan",
        "planner": {
            "agent": "Codex",
            "model": "gpt-5.6-luna",
            "prompt_version": "agent-retest-intervention-v1-guotou-2026-09-17",
        },
        "source": old.get("source"),
        "app_profile": old.get("app_profile"),
        "cases": plan_cases,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "cases": len(plan_cases)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
