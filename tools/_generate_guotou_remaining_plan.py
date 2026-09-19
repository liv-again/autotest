"""Generate a structured Guotou plan for the six remaining workbook sheets.

This is a planning helper for the current workbook only.  It translates the
normalized case facts into low-level UI primitives; the executor still owns
all page gates, evidence capture, and final status handling.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "output/2026-09-17-guotou-remaining-full/agent_context.json"
OUT = ROOT / "output/2026-09-17-guotou-remaining-full/agent_action_plan.json"

TOP_TABS = {
    "沪深京": (324, 277),
    "板块": (540, 277),
    "港股": (756, 277),
    "其他": (972, 277),
}
FUND_TABS = {"自选": (135, 288), "沪深京": (405, 288), "行业": (675, 288), "概念": (945, 288)}


def text(case: dict[str, Any]) -> str:
    return " ".join(str(case.get(key) or "") for key in (
        "case_name", "step_name", "precondition", "action", "parameters", "expected",
        "level_1", "level_2", "level_3", "level_4",
    ))


def target(kind: str, sheet: str = "", label: str = "") -> dict[str, Any]:
    if kind == "root":
        if sheet == "港股":
            ids = ["title_bar_middle", "ganggu_page"]
        else:
            ids = ["title_bar_middle", "titlebar_left_layout"]
        return {"gate_mode": "composite", "all_ids": ids, "all_text": [sheet],
                "not_ids": ["table", "stocksearch"], "orientation": "portrait",
                "description": f"国投行情{sheet}顶层页"}
    if kind == "fund_root":
        return {"gate_mode": "composite", "all_ids": ["navi_buttonbar"],
                "all_text": ["看资金"], "any_text": ["自选", "沪深京", "行业", "概念"],
                "orientation": "portrait", "description": "看资金页面"}
    if kind == "fund_list":
        return {"gate_mode": "composite", "all_ids": ["navi_buttonbar"],
                "all_text": ["看资金"], "any_text": ["自选", "沪深京", "行业", "概念"],
                "orientation": "portrait", "description": "看资金列表"}
    if kind == "search":
        return {"gate_mode": "composite", "all_ids": ["stocksearch", "search_input_et", "search_cancel_tv"],
                "all_text": ["请输入代码或简拼", "取消"], "not_ids": ["table"],
                "orientation": "portrait", "description": "行情搜索页"}
    if kind == "kline":
        return {"gate_mode": "composite", "all_ids": ["page_queue_nav_bar"],
                "any_ids": ["navi_title_right", "backButton", "al_viewfilpper"],
                "all_text": ["日K"], "any_text": ["周K", "月K", "分钟"],
                "orientation": "portrait", "description": "K线页面"}
    if kind == "detail":
        return {"gate_mode": "composite", "all_ids": ["page_queue_nav_bar", "navi_animation_label"],
                "any_ids": ["backButton", "navi_title", "al_leftbutton", "al_viewfilpper"],
                "orientation": "portrait", "description": "股票或指数详情页"}
    if kind == "watchlist":
        return {"gate_mode": "composite", "all_ids": ["title_bar_middle"],
                "all_text": ["自选"], "any_text": ["自选股", "股票搜索"],
                "orientation": "portrait", "description": "自选列表页"}
    ids = ["hx_page_title_bar"]
    all_text = [label or "返回"]
    any_text = ["返回", "列表", "最新", "涨幅", label] if label else ["返回", "列表", "最新", "涨幅"]
    return {"gate_mode": "composite", "all_ids": ids, "all_text": all_text,
            "any_text": list(dict.fromkeys(any_text)),
            "orientation": "portrait", "description": f"{label or '独立'}列表页"}


def code_and_name(case: dict[str, Any]) -> tuple[str, str]:
    p = str(case.get("parameters") or "")
    m = re.search(r"代码[：:]\s*([A-Za-z0-9]+)", p)
    code = m.group(1) if m else ""
    n = re.search(r"名称[：:]\s*([^；;]+)", p)
    name = n.group(1).strip() if n else ""
    if not code:
        m = re.search(r"输入[“\"]?([A-Za-z0-9+%-]+)", str(case.get("action") or ""))
        code = m.group(1) if m else ""
    return code, name


def source_kind(case: dict[str, Any]) -> str:
    s = text(case)
    context = " ".join(str(case.get(key) or "") for key in (
        "step_name", "precondition", "level_1", "level_2", "level_3", "level_4",
    ))
    action = str(case.get("action") or "")
    level3 = str(case.get("level_3") or "")
    pre = str(case.get("precondition") or "")
    if "自选搜索" in context or ("搜索页" in level3 and case.get("sheet") == "个股详情"):
        return "search"
    if "搜索页" in context:
        return "search"
    if "K线页面" in context and not ("进入K线" in str(case.get("case_name") or "") or "左滑" in action):
        return "kline"
    if any(k in context for k in ("股票分时详情", "股票详情", "行情数据框", "指数详情", "板块详情", "港股股票详情")):
        if "输入" in action and "搜索" in pre:
            return "search"
        return "detail"
    if case.get("sheet") == "看资金":
        return "fund_list" if "列表" in context else "fund_root"
    if "自选列表" in context:
        return "watchlist"
    if "列表" in context and not any(k in action for k in ("点击“", "点击\"")):
        return "list"
    if "列表" in context or "已进入" in pre and "列表" in pre:
        return "list"
    return "root"


def list_route(sheet: str, case: dict[str, Any]) -> list[dict[str, Any]]:
    s = text(case)
    route: list[dict[str, Any]] = []
    if sheet == "沪深京":
        # The UI exposes this right-side control with desc="涨幅榜" while
        # the node text is "更多". One semantic tap opens the list.
        route += [{"type": "tap_text", "text": "涨幅榜"}]
    elif sheet == "板块":
        label = "概念板块" if "概念" in s else "行业板块"
        route += [{"type": "tap_text", "text": label}]
    elif sheet == "港股":
        if "港股通(沪)" in s or "港股通沪" in s:
            route += [{"type": "tap_text", "text": "沪、深港通"}, {"type": "tap_text", "text": "港股通"}]
        elif "港股通(深)" in s or "港股通深" in s:
            route += [{"type": "tap_text", "text": "沪、深港通"}, {"type": "tap_text", "text": "深股通"}]
        elif "AH股" in s:
            route += [{"type": "tap_text", "text": "AH股"}]
        elif "港股创业板" in s:
            route += [{"type": "tap_text", "text": "港股创业板"}]
        elif "港股主板" in s:
            route += [{"type": "tap_text", "text": "港股主板"}]
    elif sheet == "其他":
        label = "三板" if "三板" in s else ("深证债券" if "债券" in s else "国内期货")
        route += [{"type": "tap_text", "text": label}]
    return route


def base_route(sheet: str, kind: str, case: dict[str, Any]) -> list[dict[str, Any]]:
    route: list[dict[str, Any]] = []
    if sheet in TOP_TABS:
        x, y = TOP_TABS[sheet]
        route += [{"type": "tap_xy", "x": x, "y": y}, {"type": "wait", "seconds": 3}]
    elif sheet == "看资金":
        route += [{"type": "tap_xy", "x": 94, "y": 156}, {"type": "wait", "seconds": 3}]
        if kind == "fund_list":
            s = text(case)
            tab = next((name for name in FUND_TABS if name in s), "沪深京")
            x, y = FUND_TABS[tab]
            route += [{"type": "tap_xy", "x": x, "y": y}, {"type": "wait", "seconds": 3}]
    elif sheet == "个股详情":
        if kind == "watchlist" or "自选" in text(case):
            route += [{"type": "tap_text", "text": "自选"}, {"type": "wait", "seconds": 3}]
        else:
            route += [{"type": "tap_xy", "x": 324, "y": 277}, {"type": "wait", "seconds": 3},
                      {"type": "tap_id", "id": "new_title_search"}, {"type": "wait", "seconds": 3}]
            if kind in {"detail", "kline"}:
                code, name = code_and_name(case)
                if not code:
                    code, name = "600000", "浦发银行"
                route += [{"type": "tap_id", "id": "search_input_et"}, {"type": "type_text", "text": code},
                          {"type": "wait", "seconds": 3}]
                if name:
                    route += [{"type": "tap_text", "text": name}, {"type": "wait", "seconds": 4}]
                else:
                    route += [{"type": "tap_id", "id": "dragablelistviewitem"}, {"type": "wait", "seconds": 4}]
                if kind == "kline":
                    route += [{"type": "swipe", "x1": 850, "y1": 1100, "x2": 150, "y2": 1100, "duration_ms": 500},
                              {"type": "wait", "seconds": 4}]
    if kind in {"list", "search"} and sheet in {"沪深京", "板块", "港股", "其他"}:
        route += list_route(sheet, case)
    if kind == "search" and sheet in {"沪深京", "板块", "港股", "其他"}:
        if not ("搜索页" in str(case.get("level_3") or "") or "搜索页" in str(case.get("precondition") or "")):
            route += [{"type": "tap_id", "id": "new_title_search"}, {"type": "wait", "seconds": 3}]
    return route


def field_assertions(case: dict[str, Any]) -> list[dict[str, Any]]:
    p = str(case.get("parameters") or "")
    if "目标标题" in p:
        return [{"type": "assert_text", "text": str(case.get("sheet") or "")}]
    words = []
    allowed = {"最新", "涨幅", "涨跌", "名称", "代码", "领涨股", "总市值", "大单净量", "换手", "5日涨", "10日涨", "20日涨", "总手", "金额", "价格", "数量", "时间", "买一", "买二", "买三", "买四", "买五", "卖一", "卖二", "卖三", "卖四", "卖五", "新闻", "盘口", "公告", "简况", "财务", "日K", "周K", "月K", "分钟"}
    for token in re.split(r"[、,/；; ]+", p.replace("字段：", "").replace("关注", "")):
        token = token.strip("：:")
        if token in allowed or re.fullmatch(r"[A-Za-z0-9+-]{3,}", token or ""):
            words.append(token)
    if not words:
        for token in ("最新", "涨幅", "涨跌"):
            if token in str(case.get("expected") or ""):
                words.append(token)
    return [{"type": "assert_text", "text": w} for w in list(dict.fromkeys(words))[:5]]


def actions_for(case: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    action = str(case.get("action") or "")
    s = text(case)
    expected = str(case.get("expected") or "")
    actions: list[dict[str, Any]] = []
    if "等待5秒" in action or "等待5秒" in str(case.get("parameters") or ""):
        actions += [{"type": "wait", "seconds": 5}]
        actions += field_assertions(case)
        return actions or [{"type": "observe", "target": "刷新后页面与数据区域"}]
    if "向左或向右滑动" in action:
        actions += [{"type": "swipe", "x1": 850, "y1": 1100, "x2": 150, "y2": 1100, "duration_ms": 500},
                    {"type": "swipe", "x1": 150, "y1": 1100, "x2": 850, "y2": 1100, "duration_ms": 500},
                    {"type": "wait", "seconds": 2}]
        return actions
    if "上下滑动" in action or "上下滑动" in s:
        return [{"type": "swipe", "x1": 540, "y1": 1700, "x2": 540, "y2": 800, "duration_ms": 500},
                {"type": "wait", "seconds": 2}]
    if "左滑" in action:
        return [{"type": "swipe", "x1": 850, "y1": 1100, "x2": 150, "y2": 1100, "duration_ms": 500},
                {"type": "wait", "seconds": 4}]
    if "输入" in action:
        code, name = code_and_name(case)
        actions += [{"type": "tap_id", "id": "search_input_et"}]
        if code:
            actions += [{"type": "type_text", "text": code}]
        actions += [{"type": "wait", "seconds": 3}]
        if code:
            actions += [{"type": "assert_text", "text": code}]
        if name:
            actions += [{"type": "assert_text", "text": name}]
            if "并点击" in action or "点击" in action:
                actions += [{"type": "tap_text", "text": name}, {"type": "wait", "seconds": 4}]
        return actions
    if "左上角的返回" in action or "点击左上角返回" in action or "点击页面左上角的返回" in action or "点击“取消”" in action or "点击取消" in action:
        return [{"type": "tap_id", "id": "search_cancel_tv" if "取消" in action else "backButton"}, {"type": "wait", "seconds": 3}]
    if "刷新控件" in action or "刷新" in action:
        return [{"type": "tap_xy", "x": 796, "y": 156}, {"type": "wait", "seconds": 5}]
    if "长按" in action:
        return [{"type": "tap_xy", "x": 540, "y": 550}, {"type": "wait", "seconds": 1}, {"type": "tap_text", "text": "删除"}]
    if "点击右上角搜索" in action or "点击右上角搜索控件" in action or "点击右上角搜索按钮" in action:
        return [{"type": "tap_id", "id": "new_title_search"}, {"type": "wait", "seconds": 3}]
    m = re.search(r"点击[“\"]([^”\"]+)[”\"]", action)
    if m:
        label = m.group(1)
        # For "点击某入口右侧更多", the named entry is the semantic target;
        # tapping a literal second "更多" often repeats the same node and
        # blocks after the page has already opened.
        return [{"type": "tap_text", "text": label}, {"type": "wait", "seconds": 3}]
    for label in ("新闻", "盘口", "公告", "简况", "财务", "涨幅", "领涨股", "大单净量", "自选", "沪深京", "行业", "概念", "日K", "周K", "月K", "分钟", "指标", "设置", "更多", "加自选", "取消自选", "删除自选"):
        if label in action:
            return [{"type": "tap_text", "text": label}, {"type": "wait", "seconds": 3}]
    if "查看" in action or "查看" in s or "核对" in action:
        return field_assertions(case) or [{"type": "observe", "target": "当前页面与数据区域"}]
    if "图中部" in action:
        return [{"type": "tap_xy", "x": 540, "y": 1500}, {"type": "wait", "seconds": 2}]
    if "点击底部指数记录" in action:
        return [{"type": "tap_text", "text": "上证指数"}, {"type": "wait", "seconds": 2}]
    if "点击指数分时图中央" in action:
        return [{"type": "tap_xy", "x": 540, "y": 900}, {"type": "wait", "seconds": 3}]
    return [{"type": "observe", "target": "当前页面与用例目标"}]


def profile_key(sheet: str, kind: str, case: dict[str, Any]) -> str:
    s = text(case)
    if sheet == "沪深京":
        return "quote.hsj_rankings" if "涨幅榜" in s or "沪深A股列表" in s else "quote.hsj_stock_list"
    if sheet == "板块":
        return "quote.sector_lists" if kind in {"list", "search"} else "quote.sector_main"
    if sheet == "港股":
        if "AH股" in s: return "quote.hk_ah_list"
        if "港股创业板" in s: return "quote.hk_board_growth"
        if "港股主板" in s: return "quote.hk_board_main"
        if "沪、深港通" in s or "港股通" in s: return "quote.hk_connect_and_boards"
        return "quote.hk_main"
    if sheet == "其他":
        return "quote.other_market_lists" if kind in {"list", "search"} else "quote.other_main"
    if sheet == "看资金": return "quote.fund_flow_extended"
    if sheet == "个股详情":
        if kind == "watchlist": return "quote.watchlist"
        if kind == "kline": return "quote.kline"
        return "quote.stock_detail_extended"
    return ""


def main() -> None:
    context = json.loads(CONTEXT.read_text(encoding="utf-8"))
    cases = [case for module in context.get("modules", []) for group in module.get("page_groups", []) for case in group.get("cases", [])]
    # Context cases are nested in page groups; preserve source order and remove
    # accidental duplicate copies if a planner context changes shape.
    unique: dict[str, dict[str, Any]] = {str(case["case_id"]): case for case in cases}
    cases = sorted(unique.values(), key=lambda item: (item.get("sheet", ""), int(item.get("row", 0))))
    plan_cases = []
    for case in cases:
        sheet = str(case.get("sheet") or "")
        kind = source_kind(case)
        nav = base_route(sheet, kind, case)
        key = profile_key(sheet, kind, case)
        source = "profile_plus_llm" if key else "llm_inferred"
        item = {
            "case_id": case.get("case_id"), "sheet": sheet, "row": case.get("row"),
            "case_name": case.get("case_name"), "source_order": case.get("source_order"),
            "priority": case.get("priority"), "page_group_id": case.get("page_group_id"),
            "page_group_key": case.get("page_group_key"), "navigation": nav,
            "recovery_navigation": list(nav), "navigation_source": source,
            "navigation_status": "verified" if key else "unverified",
            "navigation_policy": "required" if nav else "if_needed",
            "profile_entry_key": key,
            "target_page": target(kind, sheet, sheet),
            "actions": actions_for(case, kind),
            "expected_observations": [str(case.get("expected") or "")[:300]],
        }
        plan_cases.append(item)
    result = {
        "schema_version": "1.0", "plan_type": "agent_action_plan",
        "planner": {"agent": "Codex", "model": "gpt-5.6-luna", "prompt_version": "agent-actions-v4-guotou-remaining-full-2026-09-17"},
        "source": {"path": context.get("source_file"), "sha256": context.get("source_sha256")},
        "app_profile": {"path": context.get("app_profile", {}).get("path"), "sha256": context.get("profile_sha256")},
        "cases": plan_cases,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "cases": len(plan_cases), "sheets": sorted({c['sheet'] for c in plan_cases})}, ensure_ascii=False))


if __name__ == "__main__":
    main()
