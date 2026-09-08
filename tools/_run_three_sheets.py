from __future__ import annotations

import datetime
import argparse
import json
import shutil
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import xlrd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import droid
from tools.exception_queue import build_exception_queue
from tools.execution_journal import ExecutionJournal, JournalError
from tools.execution_gate import load_execution_policy
from tools.module_planner import build_module_plan
from tools.page_execution import (
    PageContract,
    ensure_target_page as _ensure_target_page,
    run_independent_entries,
    search_entry_two_way,
)
from tools.profile_feedback import build_profile_feedback
from tools.llm_review_queue import build_review_queue


PACKAGE = "com.hexin.plat.android.AnxinSecurity"
DEVICE = "c923178d"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "国投行情测试用例(1).xls"
APP_PROFILE = PROJECT_ROOT / "apps/guotou/profile.yaml"
OUTPUT = PROJECT_ROOT / "output/2026-09-06-guotou-hk-other-fundflow-full"
SHOTS = OUTPUT / "shots"
SHEETS = ("股指", "沪深京", "板块", "港股", "其他", "看资金")
MARKET_TABS = {
    "股指": (108, 277),
    "沪深京": (324, 277),
    "板块": (540, 277),
    "港股": (756, 277),
    "其他": (972, 277),
}
TOP_QUOTE_SHEETS = tuple(MARKET_TABS)
MAX_SOFT_BACK = 3
PAGE_READY_RETRIES = 5
_EXECUTION_POLICY = load_execution_policy()


def _policy_int(section: str, key: str, default: int) -> int:
    value = _EXECUTION_POLICY.get(section, {})
    if not isinstance(value, dict):
        return default
    try:
        return max(1, int(value.get(key, default)))
    except (TypeError, ValueError):
        return default


MAX_SOFT_BACK = _policy_int("state_reset", "max_soft_back", MAX_SOFT_BACK)
PAGE_READY_RETRIES = _policy_int("page_gate", "page_ready_retries", PAGE_READY_RETRIES)
MAX_ACTION_RETRIES = _policy_int("page_gate", "max_action_retries", 1)


def now() -> str:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds")


def wait_short(seconds: float = 0.55) -> None:
    time.sleep(seconds)


@dataclass
class ModuleSession:
    """Runtime state shared by page groups in one module execution.

    A row is still an independent execution unit.  A page group may reuse its
    navigation state, but this state never carries over a previous row's
    business action, assertion, or evidence.
    """

    active_sheet: str | None = None
    active_page_group_id: str | None = None
    active_page_group_key: str | None = None
    cold_start_count: int = 0
    soft_reset_count: int = 0
    recovery_restart_count: int = 0
    page_group_reuse_count: int = 0
    runtime_replan_count: int = 0


def event(events: list[dict], kind: str, target: str, result: str, detail: str = "") -> None:
    item = {"type": kind, "target": target, "result": result, "timestamp": now()}
    if detail:
        item["detail"] = detail
    events.append(item)


def tap_text(events: list[dict], text: str) -> bool:
    rc = droid.tap_text(text)
    ok = rc == 0
    event(events, "tap", text, "success" if ok else "not_found")
    wait_short()
    return ok


def tap_id(events: list[dict], rid: str) -> bool:
    rc = droid.tap_id(rid)
    ok = rc == 0
    event(events, "tap", f"id:{rid}", "success" if ok else "not_found")
    wait_short()
    return ok


def tap_xy(events: list[dict], x: int, y: int, target: str) -> bool:
    rc, _, err = droid.adb("shell", "input", "tap", str(x), str(y))
    ok = rc == 0
    event(events, "tap", target, "success" if ok else "failed", err.strip())
    wait_short()
    return ok


def key_back(events: list[dict]) -> bool:
    rc, _, err = droid.adb("shell", "input", "keyevent", "KEYCODE_BACK")
    ok = rc == 0
    event(events, "key", "BACK", "success" if ok else "failed", err.strip())
    wait_short()
    return ok


def swipe(events: list[dict], x1: int, y1: int, x2: int, y2: int, target: str) -> bool:
    rc, _, err = droid.adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), "350")
    ok = rc == 0
    event(events, "swipe", target, "success" if ok else "failed", err.strip())
    wait_short()
    return ok


def rotate(events: list[dict], landscape: bool) -> bool:
    rotation = "1" if landscape else "0"
    rc1, _, err1 = droid.adb("-s", DEVICE, "shell", "settings", "put", "system", "accelerometer_rotation", "0")
    rc2, _, err2 = droid.adb("-s", DEVICE, "shell", "settings", "put", "system", "user_rotation", rotation)
    ok = rc1 == 0 and rc2 == 0
    event(events, "orientation", "横屏" if landscape else "竖屏", "success" if ok else "failed", (err1 + err2).strip())
    wait_short(1.0)
    return ok


def launch_market(events: list[dict]) -> bool:
    rc1, _, err1 = droid.adb("-s", DEVICE, "shell", "am", "force-stop", PACKAGE)
    rc2, _, err2 = droid.adb("-s", DEVICE, "shell", "monkey", "-p", PACKAGE, "1")
    wait_short(1.8)
    ok = rc1 == 0 and rc2 == 0
    event(events, "launch", PACKAGE, "success" if ok else "failed", (err1 + err2).strip())
    if not ok:
        return False
    # Prefer the accessibility label.  Keep the known coordinate as a single
    # retry for versions where the bottom bar is not exposed to uiautomator.
    if tap_text(events, "行情") and wait_for_page(is_market_shell, "行情根页面"):
        return True
    if tap_xy(events, 324, 2263, "底部行情（坐标重试）"):
        return wait_for_page(is_market_shell, "行情根页面")
    return False


def screen_elements() -> list[dict]:
    try:
        return droid.parse(droid.dump_xml())
    except Exception:
        return []


def screen_blob(elements: list[dict]) -> str:
    return " ".join((e.get("text") or "") + " " + (e.get("desc") or "") + " " + (e.get("id") or "") for e in elements)


def has_label(elements: list[dict], label: str) -> bool:
    return any(label == e.get("text") or label == e.get("desc") for e in elements)


def has_id(elements: list[dict], resource_id: str) -> bool:
    return any(resource_id == e.get("id") for e in elements)


def has_title(elements: list[dict], title: str) -> bool:
    return any(
        e.get("id") in {"title_bar_middle", "title_bar_middle"}
        and (e.get("text") == title or e.get("desc") == title)
        for e in elements
    )


def is_market_shell(elements: list[dict]) -> bool:
    """Whether the app is on the market module's top-level tab page."""

    return (
        has_id(elements, "title_bar_middle")
        and all(has_label(elements, label) for label in ("沪深京", "港股", "其他"))
    )


def is_fund_flow_root(elements: list[dict]) -> bool:
    """Whether the app is on the 看资金 module root page."""

    return has_id(elements, "titlebar_leftview_text") and has_label(elements, "看资金")


def wait_for_page(predicate, description: str, retries: int = PAGE_READY_RETRIES) -> bool:
    """Poll a page predicate instead of treating an accepted tap as success."""

    for _ in range(retries):
        elements = screen_elements()
        if predicate(elements):
            return True
        wait_short(0.35)
    return False


def selected_label(label: str) -> bool:
    """Read the selected flag from the live UI tree for an exact label."""

    try:
        root = ET.fromstring(droid.dump_xml())
    except Exception:
        return False
    for node in root.iter("node"):
        attrs = node.attrib
        if attrs.get("selected") != "true":
            continue
        if attrs.get("text") == label or attrs.get("content-desc") == label:
            return True
    return False


def wait_for_selected(label: str, retries: int = PAGE_READY_RETRIES) -> bool:
    for _ in range(retries):
        if selected_label(label):
            return True
        wait_short(0.35)
    return False


def current_rotation() -> int | None:
    """Read the effective rotation value instead of trusting the ADB command."""

    try:
        rc, out, _ = droid.adb(
            "-s", DEVICE, "shell", "settings", "get", "system", "user_rotation"
        )
    except Exception:
        return None
    if rc != 0:
        return None
    try:
        return int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def orientation_matches(landscape: bool) -> bool:
    expected = 1 if landscape else 0
    return current_rotation() == expected


def wait_for_orientation(landscape: bool, retries: int = PAGE_READY_RETRIES) -> bool:
    for _ in range(retries):
        if orientation_matches(landscape):
            return True
        wait_short(0.35)
    return False


def is_search_page(elements: list[dict]) -> bool:
    """The search page has a stable, page-specific UI contract."""

    return has_id(elements, "stocksearch") or (
        has_id(elements, "search_edit_layout")
        and has_label(elements, "请输入代码或简拼")
        and has_label(elements, "取消")
    )


def is_stock_detail_page(elements: list[dict]) -> bool:
    """Recognize a stock detail page, not merely a generic content container."""

    if is_search_page(elements):
        return False
    # ``page_queue_nav_bar`` can also exist on a market list page.  Require
    # the detail-specific animation/back controls as well, otherwise a list
    # page containing many stocks is incorrectly treated as a detail page.
    return has_id(elements, "navi_animation_label") and any(
        has_id(elements, resource_id)
        for resource_id in ("backButton", "al_leftbutton", "al_viewfilpper", "navi_title_right")
    )


def is_list_page(elements: list[dict]) -> bool:
    """Recognize a list/table page and explicitly exclude detail/search pages."""

    if is_search_page(elements) or is_stock_detail_page(elements):
        return False
    return any(
        has_id(elements, resource_id)
        for resource_id in ("table", "ggt_table", "dragable_listview", "dragablelistview")
    ) or has_label(elements, "返回")


def is_named_list_page(elements: list[dict], labels: Iterable[str]) -> bool:
    # Home pages reuse the same section labels (for example ``AH股`` and
    # ``沪、深港通``) as their child lists.  A child list has a dedicated
    # title bar and back affordance; require both so the page gate cannot be
    # satisfied by a same-named entry still visible on 港股首页.
    if not is_list_page(elements):
        return False
    if has_label(elements, "返回") and any(has_label(elements, label) for label in labels):
        return True
    # In landscape mode the title/return nodes are omitted by some builds,
    # while the dedicated table header remains stable.  Keep this fallback
    # scoped to table pages so a quote home cannot satisfy a child-list gate.
    return has_id(elements, "table") and has_id(elements, "dragable_listview_header")


def is_market_home(elements: list[dict], sheet_name: str) -> bool:
    """Recognize a Sheet home by its content, not by the top-tab state.

    The app keeps the market tabs visible on the Sheet home, so
    ``is_market_shell`` and a real home page are intentionally allowed to
    overlap.  港股 therefore requires the dedicated ``ganggu_page`` surface
    and the top title “港股”.  The home cards 恒生指数、国企指数 and 沪/深港通
    are useful discovery evidence, but may be above the current scroll
    position and must not be required to be visible on every row.
    """

    if (
        is_search_page(elements)
        or is_stock_detail_page(elements)
        or is_list_page(elements)
        or not (has_title(elements, sheet_name) or (sheet_name == "港股" and has_label(elements, sheet_name)))
    ):
        return False
    if sheet_name == "港股":
        return (
            has_id(elements, "title_bar_middle")
            and has_id(elements, "ganggu_page")
            and has_id(elements, "titlebar_left_layout")
        )
    # All five top-level quote pages expose the same title bar and search
    # affordance.  Some app builds use ``tips_view`` while others expose the
    # right-hand icon as ``new_title_search``; the exact title plus the
    # dedicated page content is the stable contract.
    return (
        has_id(elements, "title_bar_middle")
        and has_id(elements, "titlebar_left_layout")
        and (
            has_id(elements, "tips_view")
            or has_id(elements, "title_bar_right1")
            or has_id(elements, "new_title_search")
        )
    )


def is_fund_flow_tab_page(elements: list[dict], tab_name: str) -> bool:
    return (
        is_fund_flow_root(elements)
        and has_label(elements, tab_name)
        and not is_search_page(elements)
        and (selected_label(tab_name) or has_id(elements, "navi_buttonbar"))
    )


def _case_text(case: dict, keys: Iterable[str] = ("case_name", "entry", "step_name", "action", "precondition")) -> str:
    return " ".join(str(case.get(key, "")) for key in keys)


def _action_lines(case: dict) -> list[str]:
    return [line.strip() for line in str(case.get("action", "")).splitlines() if line.strip()]


def _target_orientation(case: dict) -> bool | None:
    """Infer orientation from the navigation path, then from the precondition.

    A transition row can mention both orientations.  The first operation line
    describes the page that must exist *before* the row action, so it wins over
    the later action such as “将手机竖放”.
    """

    lines = _action_lines(case)
    path_line = lines[0] if lines else ""
    if "横屏" in path_line or "横放" in path_line:
        return True
    if "竖屏" in path_line or "竖放" in path_line:
        return False
    precondition = str(case.get("precondition", ""))
    if "横屏" in precondition and "竖屏" not in precondition:
        return True
    if "竖屏" in precondition and "横屏" not in precondition:
        return False
    return None


def _tap_any_text(
    events: list[dict],
    labels: Iterable[str],
    target: str,
    *,
    scroll: bool = False,
) -> bool:
    candidates = tuple(label for label in labels if label)

    def on_phase(phase: str) -> None:
        details = {
            "current": "先查找当前可见入口",
            "top": "当前可见区域未找到，先回到页面顶部再查找入口",
            "bottom": "顶部仍未找到，再向下查找底部入口",
        }
        if phase != "current":
            event(events, "navigate", target, "attempt", details[phase])

    found = search_entry_two_way(
        candidates,
        lambda label: tap_text(events, label),
        restore_top=(
            lambda: swipe(events, 540, 450, 540, 2050, f"回到{target}所在顶部")
            if scroll
            else False
        ),
        search_bottom=(
            lambda: swipe(events, 540, 1950, 540, 800, f"查找{target}")
            if scroll
            else False
        ),
        on_phase=on_phase,
    )
    if found:
        return True
    event(events, "navigate", target, "failed", "候选页面入口均未找到")
    return False


def _tap_section_more(
    events: list[dict],
    section_label: str,
    target: str,
    *,
    scroll: bool = True,
) -> bool:
    """Tap the ``更多`` control belonging to a named home-page section.

    Several sections expose the same resource id and visible text.  A fixed
    coordinate therefore risks opening the first section (usually 行业板块)
    when the row requested AH股/港股主板/港股创业板.  Resolve the section and
    its nearest right-hand ``更多`` node from the current UI tree instead.
    """

    def attempt(phase: str) -> bool:
        elements = screen_elements()
        labels = [e for e in elements if e.get("text") == section_label or e.get("desc") == section_label]
        more = [e for e in elements if e.get("id") == "more_tv" or e.get("text") == "更多"]
        pairs = [
            (abs(int(m.get("cy", 0)) - int(label.get("cy", 0))), m)
            for label in labels
            for m in more
            if int(m.get("cx", 0)) > int(label.get("cx", 0))
            and abs(int(m.get("cy", 0)) - int(label.get("cy", 0))) <= 140
        ]
        if not pairs:
            return False
        _, node = min(pairs, key=lambda item: item[0])
        return tap_xy(events, int(node["cx"]), int(node["cy"]), f"{section_label}更多")

    if attempt("current"):
        return True
    if scroll:
        event(events, "navigate", target, "attempt", "当前可见区域未找到，先回到页面顶部再查找入口")
        if swipe(events, 540, 450, 540, 2050, f"回到{target}所在顶部") and attempt("top"):
            return True
        event(events, "navigate", target, "attempt", "顶部仍未找到，再向下查找底部入口")
        if swipe(events, 540, 1950, 540, 800, f"查找{target}") and attempt("bottom"):
            return True
    event(events, "navigate", target, "failed", f"未找到“{section_label}”对应的更多按钮")
    return False


def _open_hk_connect_list(events: list[dict], market: str) -> bool:
    """Open the 港股通(沪/深) independent list from the shared connect page."""

    suffix = "沪" if market == "沪" else "深"
    labels = (f"港股通({suffix})", f"港股通（{suffix}）")
    list_predicate = lambda elements: is_named_list_page(elements, labels)
    if list_predicate(screen_elements()):
        return True
    connect_predicate = lambda elements: is_named_list_page(elements, ("沪、深港通", "沪深港通"))
    if not connect_predicate(screen_elements()):
        if not tap_xy(events, 900, 462, "沪、深港通"):
            return False
        if not wait_for_page(connect_predicate, "沪、深港通列表页"):
            return False
    if not _tap_section_more(events, f"港股通({suffix})", f"港股通({suffix})列表页", scroll=True):
        # Some builds expose full-width punctuation in the section label.
        if not _tap_section_more(events, f"港股通（{suffix}）", f"港股通({suffix})列表页", scroll=True):
            return False
    return wait_for_page(list_predicate, f"港股通({suffix})列表页")


def _open_category_list(
    events: list[dict], sheet_name: str, category: tuple[str, tuple[str, ...], tuple[str, ...]]
) -> bool:
    """Open a named child list from a quote home."""

    description, labels, entries = category
    section = next(
        (
            label
            for label in labels
            if label
            in {
                "国内指数",
                "股指期货",
                "其他指数",
                "行业板块",
                "概念板块",
                "涨幅榜",
                "跌幅榜",
                "快速涨幅",
                "换手率",
                "量比",
                "成交额",
                "AH股",
            }
        ),
        None,
    )
    if section and _tap_section_more(events, section, description, scroll=True):
        return True
    return _tap_any_text(events, entries, description, scroll=True)


def _category_info(text: str) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    """Return (description, page-title labels, entry labels) for a route hint."""

    # 股指 has three independent sections on its home page.  Their child
    # pages use the section name as the title and expose the shared table
    # contract, so these entries can use the same list/detail/search flow as
    # the market lists below.
    if "国内指数" in text or "国内股指" in text:
        return "国内指数列表页", ("国内指数",), ("国内指数",)
    if "股指期货" in text:
        return "股指期货列表页", ("股指期货",), ("股指期货",)
    if "其他指数" in text:
        return "其他指数列表页", ("其他指数",), ("其他指数",)

    # 沪深京's expandable ranking sections and AH list are independent child
    # pages.  Keep the labels broad enough for builds that append a suffix.
    for ranking in ("涨幅榜", "跌幅榜", "快速涨幅", "换手率", "量比", "成交额"):
        if ranking in text:
            return f"{ranking}列表页", (ranking,), (ranking,)
    if "AH股列表" in text:
        return "AH股列表页", ("AH股列表", "AH股"), ("AH股列表", "AH股")

    # 板块's two lists use the same section-more affordance as the other
    # quote homes.  Concept/industry wording varies slightly by workbook
    # generation, hence the aliases.
    if "概念板块" in text:
        return "概念板块列表页", ("概念板块", "概念"), ("概念板块", "概念")
    if "港股通（沪）" in text or "港股通(沪)" in text:
        return "港股通(沪)列表页", ("港股通(沪)", "港股通（沪）"), ("港股通(沪)", "港股通（沪）")
    if "港股通（深）" in text or "港股通(深)" in text:
        return "港股通(深)列表页", ("港股通(深)", "港股通（深）"), ("港股通(深)", "港股通（深）")
    if "沪、深港通" in text or "沪深港通" in text:
        return "沪、深港通列表页", ("沪、深港通", "沪深港通"), ("沪、深港通", "沪深港通")
    if "行业板块" in text:
        return "行业板块列表页", ("行业板块", "行业"), ("行业板块", "行业")
    if "AH股" in text:
        return "AH股比价列表页", ("AH股比价", "AH股"), ("AH股更多", "AH股")
    if "港股创业板" in text:
        return "港股创业板列表页", ("港股创业板",), ("港股创业板",)
    if "港股主板" in text:
        return "港股主板列表页", ("港股主板",), ("港股主板",)
    if "全球市场-港股" in text:
        return "全球市场港股列表页", ("港股", "全球市场-港股"), ("港股",)
    if "全球市场-国内期货" in text:
        return "国内期货列表页", ("国内期货",), ("国内期货",)
    if "全球市场-外汇" in text:
        return "外汇列表页", ("外汇",), ("外汇",)
    if "沪深封闭基金" in text:
        return "沪深封闭基金列表页", ("±20%基金", "沪深封闭基金"), ("沪深封闭基金",)
    if "沪深国债逆回购" in text:
        return "沪深国债逆回购列表页", ("沪深债券", "沪深国债逆回购"), ("沪深国债逆回购", "沪深债券")
    if "沪深债券" in text or "深证债券" in text or "上证债券" in text:
        return "沪深债券列表页", ("沪深债券", "深证债券", "上证债券"), ("沪深债券",)
    if "上证A股" in text:
        return "上证A股列表页", ("上证A股",), ("上证A股",)
    return None


def _direct_navigation_action(case: dict) -> bool:
    """Whether the row's own action is the category-entry tap.

    In that case the target page is the module home.  The category click is
    performed by ``execute_action`` and must not be repeated in setup.
    """

    lines = _action_lines(case)
    body = " ".join(lines[1:])
    # These rows start on 港股首页 and use the section's ``更多`` control as
    # the row action.  Their pre-action page is therefore the home page; the
    # corresponding list page is verified after the action by the reviewer.
    if any(
        phrase in body
        for phrase in (
            "点击港股通（沪）更多",
            "点击港股通（深）更多",
            "点击AH股列表的更多",
            "点击港股创业板更多",
            "点击港股主板更多",
        )
    ):
        return True
    if "点击更多按钮" in body and any(
        phrase in _case_text(case)
        for phrase in ("港股创业板", "港股主板")
    ):
        return True
    if any(
        phrase in body
        for phrase in (
            "点击沪、深港通",
            "点击沪深封闭基金",
            "点击上证A股/上证B股",
            "点击深证债券/上证债券",
            "点击沪深债券",
            "切换到“沪深京”tab",
            "切换到“行业”tab",
            "切换到“概念”tab",
            "点击更多按钮",
            "点击国内指数模块",
            "点击股指期货介绍",
            "点击概念主力净流入",
            "点击行业主力净流入",
        )
    ):
        return True
    # Do not confuse a category-entry tap with a tap on a stock row whose
    # label merely starts with the same words (港股通/港股主板列表/AH股列表).
    if any(phrase in body for phrase in ("点击AH股", "点击港股创业板", "点击港股主板", "点击港股")):
        return not any(phrase in body for phrase in ("列表", "任意一只", "任意个股", "股票"))
    return any(phrase in body for phrase in ("点击国内期货", "点击外汇"))


def _target_is_search(case: dict) -> bool:
    lines = _action_lines(case)
    body = " ".join(lines[1:])
    return "搜索" in _case_text(case) and any(
        phrase in body for phrase in ("返回", "取消", "退出")
    )


def _target_is_detail(case: dict) -> bool:
    text = _case_text(case, ("entry", "step_name", "action", "precondition"))
    if any(phrase in text for phrase in ("个股详情", "详情页", "详情界面")):
        return not _target_is_search(case)
    # Some sheets put the expected source page only in the case name.  Do
    # not treat “跳转个股详情” rows as already being on detail: those rows
    # click a list item.  A close/back/order action, however, requires detail
    # as the pre-action page.
    name = str(case.get("case_name", ""))
    body = " ".join(_action_lines(case)[1:])
    if any(phrase in name for phrase in ("个股详情", "详情页", "详情界面")):
        return not any(phrase in body for phrase in ("列表中任意", "任意个股", "任意一只", "点击一只"))
    return False


def _with_orientation(
    description: str,
    base_predicate: Callable[[list[dict]], bool],
    navigate: Callable[[list[dict]], bool],
    landscape: bool | None,
) -> PageContract:
    def predicate(elements: list[dict]) -> bool:
        return base_predicate(elements) and (
            landscape is None or orientation_matches(landscape)
        )

    def navigate_with_orientation(events: list[dict]) -> bool:
        if not base_predicate(screen_elements()):
            if not navigate(events):
                return False
            if not wait_for_page(base_predicate, description):
                return False
        if landscape is not None and not orientation_matches(landscape):
            if not rotate(events, landscape):
                return False
            return wait_for_orientation(landscape)
        return True

    return PageContract(description, predicate, navigate_with_orientation)


def target_page_contract(sheet_name: str, row: int, case: dict) -> PageContract:
    """Build the row's pre-action page contract from the module plan.

    The contract deliberately describes the page before the current row's
    business action.  For example, row 33's “点击表头字段” requires the
    上证A股 list, while a row whose action is “点击上证A股” requires the 其他
    module home.
    """

    text = _case_text(case)
    path_text = " ".join(_action_lines(case)[:1])
    category = _category_info(text)
    landscape = _target_orientation(case)
    lines = _action_lines(case)

    if row == 2:
        # 港股/其他 and 看资金 begin with a top-level tab/entry transition.
        # The other three quote sheets start from their own home because the
        # first row already exercises a child action there.
        action_text = " ".join(_action_lines(case))
        if sheet_name in {"港股", "其他", "看资金"} and (
            "tab" in action_text or "左上角" in action_text or "看资金" in action_text
        ):
            return _with_orientation("行情模块根页面", is_market_shell, lambda events: True, landscape)
        if sheet_name == "板块" and "关闭" in action_text and "搜索" in action_text:
            return _with_orientation(
                "股票搜索页",
                is_search_page,
                lambda events: tap_xy(events, 1005, 156, "板块首页搜索入口"),
                landscape,
            )
        if sheet_name in TOP_QUOTE_SHEETS:
            return _with_orientation(
                f"{sheet_name}模块首页",
                lambda elements: is_market_home(elements, sheet_name),
                lambda events: True,
                landscape,
            )
        return _with_orientation("行情模块根页面", is_market_shell, lambda events: True, landscape)

    if sheet_name == "看资金" and _target_is_search(case):
        # Search-return rows start from the search page.  The search page is
        # reached from the already-reset fund-flow root (or its named tab),
        # and the row's own cancel/back operation runs only after this guard.
        tabs = ("自选", "沪深京", "行业", "概念")
        fund_tab = next((tab for tab in tabs if tab in path_text), None)
        context_predicate = (
            (lambda elements: is_fund_flow_tab_page(elements, fund_tab))
            if fund_tab
            else is_fund_flow_root
        )

        def open_fund_search(events: list[dict]) -> bool:
            if fund_tab and not context_predicate(screen_elements()):
                if not tap_fund_tab(events, fund_tab):
                    return False
                if not wait_for_page(context_predicate, f"看资金-{fund_tab}列表页"):
                    return False
            if tap_text(events, "股票搜索"):
                return True
            if tap_id(events, "search_icon_iv"):
                return True
            return tap_xy(events, 985, 156, "右上角搜索（语义入口重试）")

        return _with_orientation("股票搜索页", is_search_page, open_fund_search, landscape)

    if sheet_name == "看资金" and _target_is_detail(case):
        tabs = ("自选", "沪深京", "行业", "概念")
        fund_tab = next((tab for tab in tabs if tab in path_text), None)
        context_predicate = (
            (lambda elements: is_fund_flow_tab_page(elements, fund_tab))
            if fund_tab
            else is_fund_flow_root
        )

        def open_fund_detail(events: list[dict]) -> bool:
            if fund_tab and not context_predicate(screen_elements()):
                if not tap_fund_tab(events, fund_tab):
                    return False
                if not wait_for_page(context_predicate, f"看资金-{fund_tab}列表页"):
                    return False
            if not first_list_item(events):
                return False
            return wait_for_page(is_stock_detail_page, "个股详情页")

        return _with_orientation("个股详情页", is_stock_detail_page, open_fund_detail, landscape)

    if sheet_name in TOP_QUOTE_SHEETS:
        if _target_is_search(case):
            category_for_search = _category_info(path_text or text)

            def open_search(events: list[dict]) -> bool:
                if category_for_search:
                    _, labels, entries = category_for_search
                    list_predicate = lambda elements: is_named_list_page(elements, labels)
                    if not list_predicate(screen_elements()):
                        if category_for_search[0].startswith("港股通("):
                            opened = _open_hk_connect_list(
                                events,
                                "沪" if "沪" in category_for_search[0] else "深",
                            )
                        elif "AH股" in category_for_search[0]:
                            opened = _tap_section_more(events, "AH股", category_for_search[0])
                        elif "港股创业板" in category_for_search[0]:
                            opened = _tap_section_more(events, "港股创业板", category_for_search[0])
                        elif "港股主板" in category_for_search[0]:
                            opened = _tap_section_more(events, "港股主板", category_for_search[0])
                        else:
                            opened = _open_category_list(events, sheet_name, category_for_search)
                        if not opened:
                            return False
                        if not wait_for_page(list_predicate, category_for_search[0]):
                            return False
                if tap_text(events, "股票搜索"):
                    return True
                if tap_id(events, "search_icon_iv"):
                    return True
                return tap_xy(events, 985, 156, "右上角搜索（语义入口重试）")

            return _with_orientation("股票搜索页", is_search_page, open_search, landscape)

        if _target_is_detail(case):
            category_for_detail = _category_info(path_text or text)

            def open_detail(events: list[dict]) -> bool:
                if not category_for_detail:
                    return False
                _, labels, entries = category_for_detail
                list_predicate = lambda elements: is_named_list_page(elements, labels)
                if not list_predicate(screen_elements()):
                    if category_for_detail[0].startswith("港股通("):
                        opened = _open_hk_connect_list(
                            events,
                            "沪" if "沪" in category_for_detail[0] else "深",
                        )
                    elif "AH股" in category_for_detail[0]:
                        opened = _tap_section_more(events, "AH股", category_for_detail[0])
                    elif "港股创业板" in category_for_detail[0]:
                        opened = _tap_section_more(events, "港股创业板", category_for_detail[0])
                    elif "港股主板" in category_for_detail[0]:
                        opened = _tap_section_more(events, "港股主板", category_for_detail[0])
                    else:
                        opened = _open_category_list(events, sheet_name, category_for_detail)
                    if not opened:
                        return False
                    if not wait_for_page(list_predicate, category_for_detail[0]):
                        return False
                if not first_list_item(events):
                    return False
                return wait_for_page(is_stock_detail_page, "个股详情页")

            return _with_orientation("个股详情页", is_stock_detail_page, open_detail, landscape)

        if _direct_navigation_action(case):
            return _with_orientation(
                f"{sheet_name}模块首页",
                lambda elements: is_market_home(elements, sheet_name),
                lambda events: True,
                landscape,
            )

        if sheet_name == "港股" and "港股主界面" in text:
            return _with_orientation(
                "港股模块首页",
                lambda elements: is_market_home(elements, sheet_name),
                lambda events: True,
                landscape,
            )

        if category:
            description, labels, entries = category
            if description.startswith("港股通("):
                market = "沪" if "沪" in description else "深"
                return _with_orientation(
                    description,
                    lambda elements: is_named_list_page(elements, labels),
                    lambda events: _open_hk_connect_list(events, market),
                    landscape,
                )
            return _with_orientation(
                description,
                lambda elements: is_named_list_page(elements, labels),
                lambda events: _open_category_list(events, sheet_name, category),
                landscape,
            )

        return _with_orientation(
            f"{sheet_name}模块首页",
            lambda elements: is_market_home(elements, sheet_name),
            lambda events: True,
            landscape,
        )

    # 看资金 uses a tab page as the precondition only when the tab is not the
    # row's own transition action.  Otherwise the module root is the target.
    tabs = ("自选", "沪深京", "行业", "概念")
    tab_name = next((tab for tab in tabs if tab in path_text), None)
    if tab_name and not _direct_navigation_action(case):
        return _with_orientation(
            f"看资金-{tab_name}列表页",
            lambda elements: is_fund_flow_tab_page(elements, tab_name),
            lambda events: tap_fund_tab(events, tab_name),
            landscape,
        )
    return _with_orientation("看资金模块根页面", is_fund_flow_root, lambda events: True, landscape)


def ensure_target_page(events: list[dict], contract: PageContract) -> bool:
    """Use the shared fail-closed page gate with this runner's UI adapter."""

    return _ensure_target_page(
        events,
        contract,
        read_state=screen_elements,
        wait_for_page=wait_for_page,
        record_event=event,
    )


def tap_market_tab(events: list[dict], sheet_name: str) -> bool:
    """Select a market sheet and verify that its tab became selected."""

    coords = MARKET_TABS
    x, y = coords[sheet_name]
    if selected_label(sheet_name):
        event(events, "assert", f"顶部{sheet_name}", "success", "目标 Tab 已处于选中状态")
        return True
    for attempt in range(MAX_ACTION_RETRIES + 1):
        if attempt:
            event(events, "retry", f"顶部{sheet_name}", "attempt", "Tab 选中状态未确认，重试一次")
        if not tap_xy(events, x, y, f"顶部{sheet_name}"):
            continue
        if wait_for_selected(sheet_name):
            event(events, "assert", f"顶部{sheet_name}", "success", "目标 Tab 已选中")
            return True
    event(events, "assert", f"顶部{sheet_name}", "failed", "点击命令已发送，但目标 Tab 未进入选中状态")
    return False


def tap_fund_tab(events: list[dict], tab_name: str) -> bool:
    """Tap a 看资金 sub-tab without colliding with the bottom 自选 bar."""

    coords = {"自选": (135, 288), "沪深京": (405, 288), "行业": (675, 288), "概念": (945, 288)}
    if tab_name not in coords:
        return tap_text(events, tab_name)
    x, y = coords[tab_name]
    return tap_xy(events, x, y, f"看资金-{tab_name}tab")


def enter_market_home(events: list[dict], sheet_name: str) -> bool:
    """Enter the selected market Sheet home, not just select its Tab.

    A soft reset normally lands on the market shell.  Its selected flag can
    still say “港股”/“其他”, so ``tap_market_tab`` alone is not a sufficient
    page assertion.  The home-page contract must be confirmed before a row
    action is allowed to run.
    """

    home_predicate = lambda elements: is_market_home(elements, sheet_name)
    if home_predicate(screen_elements()):
        event(events, "assert", f"{sheet_name}模块首页", "success", "目标 Sheet 首页已处于前台")
        return True

    coords = MARKET_TABS
    for attempt in range(MAX_ACTION_RETRIES + 1):
        if attempt:
            event(events, "retry", f"{sheet_name}模块首页", "attempt", "Tab 已选中但首页未确认，重新进入首页")
        # The home title and the top Tab share the same visible text.  A
        # text-only lookup can hit the title bar (as happened on 港股), so
        # use the known top-tab geometry first and verify the home contract.
        x, y = coords[sheet_name]
        if tap_xy(events, x, y, f"顶部{sheet_name}（首页重试）") and wait_for_page(
            home_predicate, f"{sheet_name}模块首页"
        ):
            event(events, "assert", f"{sheet_name}模块首页", "success", "顶部 Tab 入口后首页复核通过")
            return True
        if tap_text(events, sheet_name) and wait_for_page(home_predicate, f"{sheet_name}模块首页"):
            event(events, "assert", f"{sheet_name}模块首页", "success", "语义入口后首页复核通过")
            return True
    event(events, "assert", f"{sheet_name}模块首页", "failed", "Tab 选中但无法确认已进入 Sheet 首页")
    return False


def module_root_visible(sheet_name: str, elements: list[dict]) -> bool:
    if sheet_name in TOP_QUOTE_SHEETS:
        return is_market_shell(elements)
    return is_fund_flow_root(elements)


def soft_reset_to_module_root(events: list[dict], sheet_name: str) -> bool:
    """Restore a module root without killing the app for every row."""

    elements = screen_elements()
    if module_root_visible(sheet_name, elements):
        return True

    # If the current screen is the market shell, 看资金 is one direct entry
    # away; pressing BACK here would leave the app instead of restoring it.
    if sheet_name == "看资金" and is_market_shell(elements):
        if tap_xy(events, 94, 156, "看资金入口（软复位）"):
            return wait_for_page(is_fund_flow_root, "看资金根页面")
        return False

    for attempt in range(MAX_SOFT_BACK):
        if not key_back(events):
            return False
        elements = screen_elements()
        if module_root_visible(sheet_name, elements):
            event(events, "reset", f"{sheet_name}模块根页面", "success", f"返回次数={attempt + 1}")
            return True

    # A home screen can be recovered without a process restart as well.
    if has_label(elements, "行情"):
        if tap_text(events, "行情") and wait_for_page(is_market_shell, "行情根页面"):
            if sheet_name in TOP_QUOTE_SHEETS:
                return True
            if tap_xy(events, 94, 156, "看资金入口（软复位）"):
                return wait_for_page(is_fund_flow_root, "看资金根页面")
    return False


def ensure_module_state(events: list[dict], sheet_name: str, session: ModuleSession) -> bool:
    """Use one cold start per module, then soft-reset between rows."""

    if not rotate(events, False):
        return False

    if session.active_sheet != sheet_name:
        if not launch_market(events):
            return False
        session.active_sheet = sheet_name
        session.cold_start_count += 1
        event(events, "module_init", sheet_name, "success", "模块首次进入，执行一次冷启动")
        return True

    if soft_reset_to_module_root(events, sheet_name):
        session.soft_reset_count += 1
        event(events, "row_reset", sheet_name, "success", "复用进程并恢复到模块根页面")
        return True

    # Unknown or corrupted state: one bounded cold-start recovery.  If that
    # also fails, the row is blocked and no business action is attempted.
    event(events, "recovery", sheet_name, "attempt", "软复位失败，执行一次冷启动恢复")
    if not launch_market(events):
        return False
    session.active_sheet = sheet_name
    session.cold_start_count += 1
    session.recovery_restart_count += 1
    event(events, "recovery", sheet_name, "success", "冷启动恢复完成")
    return True


def ensure_page_group_state(
    events: list[dict],
    sheet_name: str,
    page_group_id: str,
    page_group_key: str,
    session: ModuleSession,
) -> bool:
    """Enter a page group once, then reuse its navigation context.

    Group reuse is deliberately limited to contiguous groups from the plan.
    A group transition restores the module root; a row inside the same group
    goes directly through its own target-page gate instead of repeating the
    module navigation.  This preserves row-level correctness while avoiding
    the old per-row module reset.
    """

    same_group = (
        session.active_sheet == sheet_name
        and session.active_page_group_id == page_group_id
        and session.active_page_group_key == page_group_key
    )
    if same_group:
        session.page_group_reuse_count += 1
        if not rotate(events, False):
            return False
        event(events, "group_reuse", page_group_id, "success", "复用同一导航上下文，仍执行本行目标页校验")
        return True

    if not ensure_module_state(events, sheet_name, session):
        return False
    session.active_page_group_id = page_group_id
    session.active_page_group_key = page_group_key
    event(events, "page_group", page_group_id, "success", f"进入导航分组：{page_group_key}")
    return True


def session_snapshot(session: ModuleSession) -> dict[str, int | str | None]:
    return {
        "policy": "module_cold_start_page_group_reuse_row_execution",
        "active_sheet": session.active_sheet,
        "active_page_group_id": session.active_page_group_id,
        "active_page_group_key": session.active_page_group_key,
        "cold_start_count": session.cold_start_count,
        "soft_reset_count": session.soft_reset_count,
        "recovery_restart_count": session.recovery_restart_count,
        "page_group_reuse_count": session.page_group_reuse_count,
        "runtime_replan_count": session.runtime_replan_count,
    }


def write_runtime_stats(path: Path, session: ModuleSession) -> None:
    path.write_text(json.dumps(session_snapshot(session), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summarize(elements: list[dict]) -> str:
    """Return a compact low-level page observation for audit/debugging.

    This is deliberately *not* the user-facing ``actual`` result.  The latter
    is built by :func:`build_actual` from the real action trace plus readable
    page text, while this field remains available as ``page_observation``.
    """

    labels = []
    for e in elements:
        label = e.get("text") or e.get("desc") or e.get("id")
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= 12:
            break
    if not labels:
        return "未获取到可用的页面观察"
    return "当前页面观察：" + "、".join(labels)


def _readable_page_result(elements: list[dict]) -> str:
    """Describe text that is visible in the post-action page dump.

    Prefer UI text/content descriptions because they map to what a user can
    see in the screenshot.  Resource IDs are only a last-resort diagnostic;
    they must not become the primary actual result.
    """

    labels: list[str] = []
    for element in elements:
        label = str(element.get("text") or element.get("desc") or "").strip()
        if not label or label in labels:
            continue
        labels.append(label)
        if len(labels) >= 16:
            break
    if labels:
        return "截图可见文字/标题：" + "、".join(labels)
    return "截图未提取到可读文字，无法仅凭页面文本确认操作结果"


def _action_step(event_item: dict) -> str | None:
    """Turn one real executor event into a readable step, never Excel action text."""

    kind = str(event_item.get("type") or event_item.get("kind") or "").strip()
    target = str(event_item.get("target") or event_item.get("name") or "").strip()
    result = str(event_item.get("result") or event_item.get("status") or "").strip()
    detail = str(event_item.get("detail") or "").strip()
    if kind == "evidence":
        return None
    if kind == "tap":
        if result == "success":
            return f"点击已识别控件“{target}”"
        if result == "not_found":
            return f"尝试定位控件“{target}”，页面未找到"
        return f"尝试点击“{target}”，执行结果：{result or '未知'}"
    if kind == "swipe":
        return f"滑动页面（{target}）"
    if kind == "key":
        return f"按下按键“{target}”"
    if kind == "orientation":
        return f"调整屏幕方向为“{target}”"
    if kind == "observe":
        return "采集当前页面状态"
    if kind == "executor":
        return f"执行器报告异常：{detail or target or result}"
    if kind == "retry":
        return f"对“{target}”进行一次受限重试"
    if kind == "assert":
        return f"校验页面状态“{target}”：{detail or result}"
    if kind == "navigate":
        return f"执行公共导航“{target}”：{detail or result}"
    if kind == "launch":
        return "启动测试 App"
    if kind == "reset":
        return f"恢复到“{target}”"
    return None


def build_actual(
    action_events: list[dict],
    elements: list[dict],
    *,
    setup_ok: bool,
    action_ok: bool,
    error_detail: str = "",
) -> str:
    """Build the readable actual result shown in the Excel report.

    The steps come only from events actually emitted by the executor.  The
    result section comes only from the post-action screen dump, which is the
    same UI state used for the evidence screenshot.  Excel ``action`` text is
    intentionally not accepted as an input here.
    """

    steps = [_action_step(item) for item in action_events]
    steps = [step for step in steps if step]
    if not steps:
        if not setup_ok:
            steps = [f"本行未执行：目标页面前置校验失败{f'（{error_detail}）' if error_detail else ''}"]
        elif not action_ok:
            steps = [f"本行动作未完成{f'：{error_detail}' if error_detail else ''}"]
        else:
            steps = ["采集执行后页面状态"]

    lines = ["AI执行步骤："]
    lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    lines.append("操作结果：")
    lines.append(_readable_page_result(elements))
    return "\n".join(lines)


def setup_sheet(
    events: list[dict],
    sheet_name: str,
    row: int,
    case: dict,
    session: ModuleSession,
    page_group_id: str | None = None,
    page_group_key: str | None = None,
) -> bool:
    """Prepare one row with bounded page-group navigation reuse.

    The order here is intentionally fixed and shared by every Sheet:

    ``enter/reuse page group -> check target page -> navigate if needed ->
    re-check target page``.  Only a successful target-page assertion allows
    ``execute_action`` to run.  If a reused page is no longer usable, perform
    one bounded runtime replan from the module root before blocking.
    """

    group_id = page_group_id or str(case.get("page_group_id") or f"{sheet_name}-ungrouped")
    group_key = page_group_key or str(case.get("page_group_key") or sheet_name)
    same_group = (
        session.active_sheet == sheet_name
        and session.active_page_group_id == group_id
        and session.active_page_group_key == group_key
    )
    ok = ensure_page_group_state(events, sheet_name, group_id, group_key, session)
    if not ok:
        return False

    top_tab_transition = (
        row == 2
        and sheet_name in {"港股", "其他"}
        and "tab" in " ".join(_action_lines(case)).casefold()
    )
    if not same_group and sheet_name in TOP_QUOTE_SHEETS and not top_tab_transition:
        # Row 2 is the Sheet-switching action itself.  All later rows use the
        # selected Sheet as their public module entry point.  This is done
        # once when a page group starts; rows in the same group reuse it.
        if row != 2:
            ok = enter_market_home(events, sheet_name) and ok
    elif not same_group and row != 2 and sheet_name == "看资金" and not is_fund_flow_root(screen_elements()):
        # The first row of 看资金 must still enter the module before its
        # target-page contract is checked.  Rows in the same page group reuse
        # the group page and therefore do not repeat this tap.
        entered = tap_text(events, "看资金")
        if not entered:
            entered = tap_xy(events, 94, 156, "看资金入口（语义入口重试）")
        ok = entered and wait_for_page(is_fund_flow_root, "看资金根页面") and ok
    if not ok:
        return False

    contract = target_page_contract(sheet_name, row, case)
    if ensure_target_page(events, contract):
        return True

    # A page-group plan is advisory.  A failed target-page check must get one
    # bounded runtime replan from a clean module root before the row is
    # blocked.  This prevents a stale/incorrect plan from blocking a row that
    # is still reachable, while keeping the business action behind the gate.
    session.runtime_replan_count += 1
    event(
        events,
        "replan",
        group_id,
        "attempt",
        "页面组复用后的目标页校验失败，恢复模块根页面并重新规划本行导航",
    )
    if not ensure_module_state(events, sheet_name, session):
        event(events, "replan", group_id, "failed", "运行时重新规划前的模块复位失败")
        return False
    if sheet_name in TOP_QUOTE_SHEETS and not top_tab_transition:
        if not enter_market_home(events, sheet_name):
            event(events, "replan", group_id, "failed", "运行时重新规划后的模块入口失败")
            return False
    elif sheet_name == "看资金" and row != 2 and not is_fund_flow_root(screen_elements()):
        entered = tap_text(events, "看资金") or tap_xy(events, 94, 156, "看资金入口（运行时重规划）")
        if not entered or not wait_for_page(is_fund_flow_root, "看资金根页面（运行时重规划）"):
            event(events, "replan", group_id, "failed", "运行时重新规划后的看资金入口失败")
            return False
    if ensure_target_page(events, contract):
        event(events, "replan", group_id, "success", "运行时重新规划后目标页校验通过")
        return True
    event(events, "replan", group_id, "failed", "运行时重新规划后目标页仍未通过")
    return False


def first_list_item(events: list[dict]) -> bool:
    return tap_xy(events, 540, 1000, "第一条列表记录")


def _restore_market_home_for_entry(events: list[dict], sheet_name: str) -> bool:
    """Restore a market Sheet home before each multi-entry sub-action."""

    if is_market_home(screen_elements(), sheet_name):
        return True
    if not soft_reset_to_module_root(events, sheet_name):
        return False
    return enter_market_home(events, sheet_name)


def _verify_market_entry(label: str) -> bool:
    category = _category_info(label)
    labels = category[1] if category else (label,)
    description = category[0] if category else f"{label}列表页"
    return wait_for_page(lambda elements: is_named_list_page(elements, labels), description)


def execute_action(events: list[dict], sheet_name: str, row: int, case: dict) -> tuple[bool, str, str]:
    case_name = str(case.get("case_name", ""))
    step_name = str(case.get("step_name", ""))
    action = str(case.get("action", ""))
    operation_text = f"{step_name} {action}"
    text = f"{case_name} {operation_text}"
    low = text.casefold()
    attempted = False
    ok = True

    # Orientation is decided from the operation, not from the case title.
    # This prevents “横屏切换竖屏” from matching the wrong branch.
    if "竖放" in operation_text or "切换竖屏" in operation_text:
        attempted = True
        ok = rotate(events, False) and ok
    elif "横放" in operation_text or "切换横屏" in operation_text:
        attempted = True
        ok = rotate(events, True) and ok

    if "搜索" in operation_text and not any(k in operation_text for k in ("退出搜索", "返回搜索", "取消按钮", "取消")):
        attempted = True
        ok = tap_xy(events, 985, 156, "右上角搜索")
    elif (
        ("关闭" in operation_text and "搜索" in operation_text)
        or "关闭按钮" in operation_text
    ):
        attempted = True
        ok = tap_text(events, "关闭")
        if not ok:
            ok = tap_id(events, "search_close") or key_back(events)
    elif "退出搜索" in operation_text or "返回搜索" in operation_text or "左上角的返回" in operation_text or "点击左上角的返回" in operation_text:
        attempted = True
        ok = key_back(events) and ok
    elif any(
        phrase in operation_text
        for phrase in ("页面左上角的返回按钮", "点击页面左上角的返回", "左上角的返回按钮")
    ):
        attempted = True
        ok = key_back(events) and ok
    elif ("取消按钮" in operation_text or "取消" in operation_text) and "取消排序" not in operation_text:
        attempted = True
        ok = tap_text(events, "取消")
        if not ok:
            ok = key_back(events)

    if "滑动" in text:
        attempted = True
        ok = swipe(events, 540, 1900, 540, 850, "上下滑动列表") and ok
        if "左右" in text or "横屏" in text:
            ok = swipe(events, 850, 1400, 150, 1400, "左右滑动列表") and ok

    if "刷新" in text:
        attempted = True
        ok = tap_xy(events, 796, 156, "右上角刷新")

    if "排序" in text or "表头字段" in text:
        attempted = True
        # List headers move with the page and differ between the shared
        # connect screen and each independent market list.  Resolve the
        # visible ``涨幅`` header first; retain the legacy coordinate as one
        # bounded retry for builds that omit header text from the UI tree.
        header_ok = tap_text(events, "涨幅")
        if not header_ok:
            header_ok = tap_xy(events, 758, 403, "表头字段（坐标重试）")
        ok = header_ok and ok

    if "取消排序" in text:
        attempted = True
        ok = tap_xy(events, 104, 403, "取消排序") and ok

    if "切换" in text or "来回" in text:
        tabs = [tab for tab in ("港股通", "沪股通", "深股通", "自选", "沪深京", "行业", "概念") if tab in text]
        if tabs:
            attempted = True
            coords = {"港股通": (180, 277), "沪股通": (540, 277), "深股通": (900, 277), "自选": (135, 288), "沪深京": (405, 288), "行业": (675, 288), "概念": (945, 288)}
            for tab in tabs:
                x, y = coords[tab]
                ok = tap_xy(events, x, y, tab) and ok

    if "切换到港股tab" in low:
        attempted = True
        ok = tap_market_tab(events, "港股") and ok
    if "切换到“其他”" in text or "切换到其他" in text:
        attempted = True
        ok = tap_market_tab(events, "其他") and ok
    for top_sheet in TOP_QUOTE_SHEETS:
        if top_sheet in {"港股", "其他"}:
            continue
        if (
            f"切换到“{top_sheet}”tab" in text
            or f"切换到{top_sheet}tab" in low
            or f"点击“{top_sheet}”tab" in text
        ):
            attempted = True
            ok = tap_market_tab(events, top_sheet) and ok
    if "跳转沪深港通" in text:
        attempted = True
        ok = tap_xy(events, 900, 462, "沪、深港通") and ok
    # For row 2 the entry tap is the row's business action.  For later rows,
    # setup_sheet has already established 看资金 as the target page; repeating
    # the module entry here only creates a useless extra transition and can
    # move the app away from the page being tested.
    if row == 2 and "看资金" in operation_text and any(keyword in operation_text for keyword in ("点击", "进入", "跳转")):
        attempted = True
        ok = tap_xy(events, 94, 156, "看资金入口") and ok
    if "点击恒生指数" in text:
        attempted = True
        ok = tap_xy(events, 180, 405, "恒生指数") and ok
    if "点击国企指数" in text:
        attempted = True
        ok = tap_xy(events, 540, 405, "国企指数") and ok
    if "跳转港股列表" in text:
        attempted = True
        ok = tap_xy(events, 876, 541, "港股") and ok
    if "跳转国内期货列表" in text:
        attempted = True
        ok = tap_xy(events, 204, 541, "国内期货") and ok
    if "跳转外汇列表" in text:
        attempted = True
        ok = tap_xy(events, 540, 541, "外汇") and ok
    if "跳转沪深封闭基金列表" in text:
        attempted = True
        ok = tap_xy(events, 204, 878, "沪深封闭基金") and ok
    if "跳转沪深国债逆回购列表" in text:
        attempted = True
        ok = tap_xy(events, 208, 2104, "通用回购逆回购") and ok

    if "点击港股通（沪）更多" in text or "点击港股通（深）更多" in text:
        attempted = True
        market = "沪" if "（沪）" in text else "深"
        ok = _open_hk_connect_list(events, market) and ok
    elif "点击AH股列表的更多" in text:
        attempted = True
        ok = _tap_section_more(events, "AH股", "AH股比价列表页") and ok
    elif "港股创业板" in text and "更多" in text:
        attempted = True
        ok = _tap_section_more(events, "港股创业板", "港股创业板列表页") and ok
    elif "港股主板" in text and "更多" in text:
        attempted = True
        ok = _tap_section_more(events, "港股主板", "港股主板列表页") and ok
    elif "更多" in text:
        attempted = True
        ok = tap_text(events, "更多") and ok

    if any(keyword in text for keyword in ("任意一只股票", "任意个股", "任意一只", "任意一指数", "任意一个指数", "列表中任意", "点击一只", "点击某条")):
        attempted = True
        ok = first_list_item(events) and ok

    if "点击i标志" in text or "股指期货介绍一行" in text:
        attempted = True
        ok = tap_id(events, "tips_info_iv") or tap_text(events, "股指期货介绍") or ok

    if "涨幅榜、跌幅榜、快速涨幅、换手率、量比、成交额" in text:
        attempted = True
        for label in ("涨幅榜", "跌幅榜", "快速涨幅", "换手率", "量比", "成交额"):
            if has_label(screen_elements(), label):
                ok = tap_text(events, label) and ok

    if "点击个股分时图右上角的X" in text:
        attempted = True
        ok = tap_id(events, "navi_title_right") or key_back(events)

    if "点击上证A股/上证B股" in text:
        attempted = True
        for label in ("上证A股", "上证B股", "深证A股", "深证B股", "中小板", "创业板", "科创版", "新三板", "三板", "风险警示", "退市整理"):
            ok = tap_text(events, label) and ok

    if "点击深证债券/上证债券" in text:
        attempted = True
        for label in ("深证债券", "上证债券", "沪深债券", "可转债"):
            ok = tap_text(events, label) and ok

    if "行业板块、AH股、港股主板、港股创业板" in text:
        attempted = True
        entries = ("行业板块", "AH股", "港股主板", "港股创业板")
        ok = run_independent_entries(
            entries,
            ensure_source_page=lambda: _restore_market_home_for_entry(events, sheet_name),
            execute_entry=lambda label: _tap_any_text(
                events,
                (_category_info(label) or ("", (), (label,)))[2],
                f"{sheet_name}首页-{label}",
                scroll=True,
            ),
            verify_entry=_verify_market_entry,
        ) and ok

    if "点击下方的“下单”" in text or "点击下方的下单" in text:
        attempted = True
        ok = tap_text(events, "下单") and ok

    if "内容删除" in text:
        attempted = True
        deleted = False
        for rid in ("ah_hint_close", "close", "iv_close", "item_right_arrow"):
            if tap_id(events, rid):
                deleted = True
                break
        ok = deleted and ok

    if "内容收起" in text or "内容展开" in text:
        attempted = True
        ok = tap_id(events, "item_right_arrow") and ok

    if not attempted:
        # Observation-only rows are explicit and remain pending until their
        # page facts are checked. Unknown actions are never silently passed.
        if any(keyword in text for keyword in ("查看", "展示", "核对")):
            event(events, "observe", "当前页面与数据区域", "success", "显式观察类步骤，需根据页面事实判定")
            return True, "", "observe"
        detail = f"未识别 Excel 行{row}的操作：{action or case_name}"
        event(events, "executor", f"{sheet_name}!{row}", "failed", detail)
        return False, detail, "unrecognized"

    return ok, "; ".join(e.get("detail", "") for e in events if e.get("result") not in {"success"} and e.get("detail")), "action"


def take_shot(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    rc1, _, _ = droid.adb("-s", DEVICE, "shell", "screencap", "-p", "/sdcard/_three_sheets.png")
    rc2, _, _ = droid.adb("-s", DEVICE, "pull", "/sdcard/_three_sheets.png", str(path))
    return rc1 == 0 and rc2 == 0 and path.exists() and path.stat().st_size > 0


def build_manifest(workbook: xlrd.book.Book) -> dict:
    selected = []
    for sheet_name in SHEETS:
        sheet = workbook.sheet_by_name(sheet_name)
        for row in range(2, sheet.nrows + 1):
            selected.append({
                "case_id": f"{sheet_name}-row-{row:03d}",
                "sheet": sheet_name,
                "row": row,
                "case_name": str(sheet.cell_value(row - 1, 4)),
            })
    return {
        "manifest_version": "1.0",
        "mode": "full",
        "source_file": str(SOURCE.resolve()),
        "source_sheets": list(SHEETS),
        "expected_count": len(selected),
        "selected_cases": selected,
    }


def _load_retest_queue(path: Path) -> list[dict]:
    """Load and validate a single-case retest queue.

    The queue is the execution scope for a retest run.  It is deliberately
    read before any device action so a malformed or duplicated queue cannot
    result in an ambiguous partial run.
    """

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"复测队列不可读取或不是合法 JSON: {path}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
        raise ValueError("复测队列必须是带 cases 列表的 JSON 对象")

    entries: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for index, item in enumerate(document["cases"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"复测队列第 {index} 项不是对象")
        sheet = str(item.get("sheet") or "").strip()
        case_id = str(item.get("case_id") or "").strip()
        try:
            row = int(item.get("row"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"复测队列第 {index} 项缺少合法 row") from exc
        if not sheet or not case_id:
            raise ValueError(f"复测队列第 {index} 项缺少 sheet 或 case_id")
        key = (sheet, row)
        if key in seen:
            raise ValueError(f"复测队列包含重复用例: {sheet}!{row}")
        seen.add(key)
        entries.append(
            {
                "sheet": sheet,
                "row": row,
                "case_id": case_id,
                "retest_order": int(item.get("retest_order") or index),
                "initial_status": item.get("initial_status", ""),
            }
        )
    entries.sort(key=lambda item: (int(item["retest_order"]), item["sheet"], int(item["row"])))
    if not entries:
        raise ValueError("复测队列为空，没有需要执行的用例")
    return entries


def _retest_execution_items(plan: dict, queue_entries: list[dict]) -> list[tuple[dict, dict, dict]]:
    """Resolve queue rows back to the current Excel/module plan.

    Queue records carry the original result for audit, but navigation and
    action definitions must come from the current source workbook and plan.
    This prevents a hand-edited queue from changing the operation executed on
    the device while still allowing a queue to select a sparse set of rows.
    """

    planned: dict[tuple[str, int], tuple[dict, dict, dict]] = {}
    for module in plan.get("modules", []):
        sheet = str(module.get("sheet") or "").strip()
        for page_group in module.get("page_groups") or []:
            for case in page_group.get("cases") or []:
                planned[(sheet, int(case["row"]))] = (module, page_group, case)

    items: list[tuple[dict, dict, dict]] = []
    for entry in queue_entries:
        key = (entry["sheet"], int(entry["row"]))
        resolved = planned.get(key)
        if resolved is None:
            raise ValueError(f"复测队列中的用例不在当前 source/plan 中: {key[0]}!{key[1]}")
        module, page_group, case = resolved
        if str(case.get("case_id") or "") != entry["case_id"]:
            raise ValueError(
                f"复测队列 case_id 与 source 不一致: {key[0]}!{key[1]} "
                f"queue={entry['case_id']} source={case.get('case_id')}"
            )
        items.append((module, page_group, case))
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="模块级规划、Excel 行级执行的 Android 用例执行器")
    parser.add_argument("--source", default=str(SOURCE), help=".xls/.xlsx 用例文件")
    parser.add_argument("--output", default=str(OUTPUT), help="本轮运行目录")
    parser.add_argument("--sheet", action="append", dest="sheets", help="指定 Sheet，可重复；默认执行预设 Sheet")
    parser.add_argument("--resume", action="store_true", help="从 execution_records.jsonl 继续未完成用例")
    parser.add_argument(
        "--retest-queue",
        help="只执行 retest_results.py plan 生成的单用例复测队列；不传时保持全量 Sheet 执行",
    )
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    queue_entries = _load_retest_queue(Path(args.retest_queue).expanduser().resolve()) if args.retest_queue else []
    queue_sheets = tuple(dict.fromkeys(entry["sheet"] for entry in queue_entries))
    if queue_entries and args.sheets:
        unexpected_sheets = sorted(set(queue_sheets).difference(args.sheets))
        if unexpected_sheets:
            raise ValueError(
                "--sheet 未覆盖复测队列中的 Sheet: " + ", ".join(unexpected_sheets)
            )
    selected_sheets = tuple(args.sheets or queue_sheets or SHEETS)
    output.mkdir(parents=True, exist_ok=True)
    shots = output / "shots"
    shots.mkdir(parents=True, exist_ok=True)

    # LLM/module planning happens once. Runtime execution consumes contiguous
    # page groups in source order, while each Excel row remains independent.
    plan = build_module_plan(source, selected_sheets, profile_path=APP_PROFILE)
    manifest = plan["execution_manifest"]
    retest_items = _retest_execution_items(plan, queue_entries) if queue_entries else []
    if queue_entries:
        # Keep the source/module plan available for audit, but narrow the
        # execution manifest to the queue.  This makes journal completeness
        # and downstream review scope reflect the actual retest run.
        manifest["mode"] = "retest"
        manifest["execution_scope"] = "single_case_retest"
        manifest["retest_queue"] = str(Path(args.retest_queue).expanduser().resolve())
        manifest["retest_queue_cases"] = [
            {
                "case_id": entry["case_id"],
                "sheet": entry["sheet"],
                "row": entry["row"],
                "retest_order": entry["retest_order"],
                "initial_status": entry["initial_status"],
            }
            for entry in queue_entries
        ]
        manifest["selected_cases"] = [
            {
                "case_id": case["case_id"],
                "sheet": case["sheet"],
                "row": int(case["row"]),
                "source_order": int(case["source_order"]),
                "case_name": case.get("case_name", ""),
                "page_group_id": page_group.get("page_group_id", ""),
                "page_group_key": page_group.get("page_group_key", ""),
            }
            for _, page_group, case in retest_items
        ]
        manifest["expected_count"] = len(retest_items)
        manifest["retest_expected_count"] = len(retest_items)
    existing_manifest_path = output / "execution_manifest.json"
    existing_run_id = ""
    if args.resume and existing_manifest_path.is_file():
        try:
            existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            existing_mode = str(existing_manifest.get("mode") or "full")
            requested_mode = "retest" if queue_entries else "full"
            if existing_mode != requested_mode:
                raise ValueError(
                    f"不能用 resume 混用执行模式: 已有 {existing_mode}，当前请求 {requested_mode}"
                )
            old_selected = {
                (str(item.get("sheet") or ""), int(item.get("row")), str(item.get("case_id") or ""))
                for item in existing_manifest.get("selected_cases", [])
                if isinstance(item, dict) and item.get("row") not in (None, "")
            }
            new_selected = {
                (str(item.get("sheet") or ""), int(item.get("row")), str(item.get("case_id") or ""))
                for item in manifest.get("selected_cases", [])
                if isinstance(item, dict) and item.get("row") not in (None, "")
            }
            if old_selected and old_selected != new_selected:
                raise ValueError("不能用 resume 更换已冻结的用例范围，请使用新的 output 目录")
            existing_run_id = str(existing_manifest.get("run_id") or "").strip()
        except (OSError, json.JSONDecodeError):
            existing_run_id = ""
    manifest["run_id"] = existing_run_id or f"run-{uuid.uuid4().hex}"
    manifest["started_at"] = now()
    (output / "module_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "execution_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(source, output / f"{source.stem}_source{source.suffix}")

    journal = ExecutionJournal(output, manifest, resume=args.resume)
    records = journal.records
    execution_order = max((int(record.get("execution_order", 0)) for record in records), default=0)
    setup_trace: list[dict] = []
    session = ModuleSession()
    runtime_stats_path = output / "runtime_stats.json"
    write_runtime_stats(runtime_stats_path, session)

    try:
        if queue_entries:
            execution_items = retest_items
        else:
            execution_items = []
            for module in plan["modules"]:
                sheet_name = module["sheet"]
                page_groups = module.get("page_groups") or [
                    {
                        "page_group_id": f"{sheet_name}-ungrouped",
                        "page_group_key": sheet_name,
                        "cases": module.get("cases", []),
                    }
                ]
                execution_items.extend(
                    (module, page_group, case)
                    for page_group in page_groups
                    for case in page_group.get("cases", [])
                )
        for module, page_group, case in execution_items:
                    sheet_name = module["sheet"]
                    page_group_id = str(page_group.get("page_group_id") or f"{sheet_name}-ungrouped")
                    page_group_key = str(page_group.get("page_group_key") or sheet_name)
                    row = int(case["row"])
                    if journal.has_case(sheet_name, row):
                        continue

                    if queue_entries:
                        # A retest queue is explicitly single-case.  Clear
                        # the page-group cache before every row so a row never
                        # inherits target-page state from another retry.
                        session.active_page_group_id = None
                        session.active_page_group_key = None

                    execution_order += 1
                    setup_events: list[dict] = []
                    action_events: list[dict] = []
                    setup_ok = False
                    action_ok = False
                    action_mode = "setup_failed"
                    error_detail = ""
                    page_observation = "未获取到可用的页面观察"
                    observation = ""
                    try:
                        setup_ok = setup_sheet(
                            setup_events,
                            sheet_name,
                            row,
                            case,
                            session,
                            page_group_id=page_group_id,
                            page_group_key=page_group_key,
                        )
                        if setup_ok:
                            action_ok, error_detail, action_mode = execute_action(action_events, sheet_name, row, case)
                        else:
                            failed_setup = next(
                                (
                                    item.get("detail")
                                    for item in reversed(setup_events)
                                    if item.get("result") == "failed" and item.get("detail")
                                ),
                                "公共前置状态建立失败",
                            )
                            error_detail = f"{failed_setup}；未执行本行操作"
                        elements = screen_elements()
                        page_observation = summarize(elements)
                        observation = build_actual(
                            action_events,
                            elements,
                            setup_ok=setup_ok,
                            action_ok=action_ok,
                            error_detail=error_detail,
                        )
                    except Exception as exc:
                        error_detail = str(exc)
                        event(action_events, "executor", f"{sheet_name}!{row}", "failed", error_detail)
                        page_observation = "未获取到可用的页面观察"
                        observation = build_actual(
                            action_events,
                            [],
                            setup_ok=setup_ok,
                            action_ok=False,
                            error_detail=error_detail,
                        )

                    evidence_path = shots / f"{sheet_name}_row_{row:03d}.png"
                    shot_ok = take_shot(evidence_path)
                    if not shot_ok:
                        event(action_events, "evidence", str(evidence_path), "failed", "截图未生成或为空")
                    else:
                        event(action_events, "evidence", str(evidence_path), "success")

                    verification_text = " ".join(
                        str(case.get(key, "")) for key in ("case_name", "entry", "precondition", "action", "expected")
                    )
                    verification_only = any(k in verification_text for k in ("PC", "核对", "数据刷新", "实时", "开市", "时段"))
                    if not setup_ok or not action_ok or not shot_ok:
                        status = "⛔阻塞"
                        blocked_reason = error_detail or "入口、动作或独立证据采集失败"
                    elif action_mode == "observe":
                        status = "🟡待验证"
                        blocked_reason = "该行是显式观察类步骤，需根据页面事实确认预期结果"
                    elif verification_only:
                        status = "⚠️部分通过"
                        blocked_reason = "已实际进入页面并执行操作，但本轮缺少外部基准或所需交易时段，无法完成全部预期核对"
                    else:
                        status = "✅通过"
                        blocked_reason = ""

                    record = {
                        "module": module["module"],
                        "sheet": sheet_name,
                        "row": row,
                        "case_id": case["case_id"],
                        "source_order": int(case["source_order"]),
                        "execution_order": execution_order,
                        "case_name": case.get("case_name", ""),
                        "section": case.get("level_3", ""),
                        "source_priority": case.get("priority", ""),
                        "entry": case.get("entry", ""),
                        "step_name": case.get("step_name", ""),
                        "precondition": case.get("precondition", ""),
                        "action": case.get("action", ""),
                        "parameters": case.get("parameters", ""),
                        "expected": case.get("expected", ""),
                        "navigation_context": case.get("navigation_context", {}),
                        "profile_hints": case.get("profile_hints", []),
                        "page_group_id": page_group_id,
                        "page_group_key": page_group_key,
                        "status": status,
                        "action_mode": action_mode,
                        "actual": observation,
                        "observation": observation,
                        "page_observation": page_observation,
                        "evidence": [str(evidence_path).replace("\\", "/")],
                        "action_trace": action_events,
                        "tested_at": now(),
                    }
                    if blocked_reason:
                        record["blocked_reason"] = blocked_reason
                    persisted = journal.append(record)
                    records.append(persisted)
                    setup_trace.append(
                        {
                            "case_id": case["case_id"],
                            "sheet": sheet_name,
                            "row": row,
                            "page_group_id": page_group_id,
                            "page_group_key": page_group_key,
                            "type": "case_setup",
                            "result": "success" if setup_ok else "failed",
                            "events": setup_events,
                        }
                    )
                    write_runtime_stats(runtime_stats_path, session)
                    print(
                        f"{sheet_name}!{row}: {status} group={page_group_id} "
                        f"setup={setup_ok} action={action_ok} evidence={shot_ok}"
                        f"{' [retest]' if queue_entries else ''}",
                        flush=True,
                    )
    except KeyboardInterrupt:
        write_runtime_stats(runtime_stats_path, session)
        journal.mark_paused(reason="用户暂停执行")
        rotate([], False)
        print(json.dumps({"status": "paused", "records": len(records), "output": str(output)}, ensure_ascii=False))
        return 130
    except JournalError:
        write_runtime_stats(runtime_stats_path, session)
        journal.mark_paused(reason="执行记录持久化失败")
        rotate([], False)
        raise

    setup_trace.append(
        {
            "type": "runtime_policy",
            "scope": "run",
            "policy": "module_cold_start_page_group_reuse_row_execution",
            "stats": session_snapshot(session),
        }
    )
    final_path = journal.finalize(setup_trace=setup_trace)
    if queue_entries:
        # Keep the conventional retest_results.py input name alongside the
        # journal's canonical execution_records.json.
        shutil.copy2(final_path, output / "retest_execution.json")
    exception_queue = build_exception_queue({"cases": records})
    (output / "exception_queue.json").write_text(
        json.dumps(exception_queue, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    profile_feedback = build_profile_feedback(
        {"cases": records},
        run_dir=output,
        app_slug="guotou",
        app_version=str(plan.get("app_profile_context", {}).get("app_version") or "unknown"),
    )
    (output / "profile_feedback.json").write_text(
        json.dumps(profile_feedback, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    execution_document = json.loads(final_path.read_text(encoding="utf-8"))
    review_queue = build_review_queue(
        execution_document,
        run_dir=output,
        source_path=final_path,
    )
    (output / "llm_review_queue.json").write_text(
        json.dumps(review_queue, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rotate([], False)
    print(json.dumps({"manifest": manifest["expected_count"], "records": len(records), "output": str(final_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
