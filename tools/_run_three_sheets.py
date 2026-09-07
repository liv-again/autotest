from __future__ import annotations

import datetime
import argparse
import json
import shutil
import sys
import time
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


PACKAGE = "com.hexin.plat.android.AnxinSecurity"
DEVICE = "c923178d"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "国投行情测试用例(1).xls"
OUTPUT = PROJECT_ROOT / "output/2026-09-06-guotou-hk-other-fundflow-full"
SHOTS = OUTPUT / "shots"
SHEETS = ("港股", "其他", "看资金")
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
    """Runtime state shared by rows in one module execution.

    A row is still an independent execution unit.  This state only controls
    how the app is restored to a known module root; it never carries over a
    previous row's business action, assertion, or evidence.
    """

    active_sheet: str | None = None
    cold_start_count: int = 0
    soft_reset_count: int = 0
    recovery_restart_count: int = 0


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
        for resource_id in ("table", "ggt_table", "hx_page_title_bar")
    ) or has_label(elements, "返回")


def is_named_list_page(elements: list[dict], labels: Iterable[str]) -> bool:
    return is_list_page(elements) and any(has_label(elements, label) for label in labels)


def is_market_home(elements: list[dict], sheet_name: str) -> bool:
    """Recognize a Sheet home by its content, not by the top-tab state.

    The app keeps the market tabs visible on the Sheet home, so
    ``is_market_shell`` and a real home page are intentionally allowed to
    overlap.  港股 therefore requires the dedicated ``ganggu_page`` surface
    and the top title “港股”.  The home cards 恒生指数、国企指数 and 沪/深港通
    are useful discovery evidence, but may be above the current scroll
    position and must not be required to be visible on every row.
    """

    if is_search_page(elements) or is_stock_detail_page(elements) or not has_label(elements, sheet_name):
        return False
    if sheet_name == "港股":
        return (
            has_id(elements, "title_bar_middle")
            and has_id(elements, "ganggu_page")
            and has_id(elements, "titlebar_left_layout")
        )
    return (
        has_id(elements, "titlebar_left_layout")
        and (has_id(elements, "tips_view") or has_id(elements, "title_bar_right1"))
    )


def is_fund_flow_tab_page(elements: list[dict], tab_name: str) -> bool:
    return (
        is_fund_flow_root(elements)
        and has_label(elements, tab_name)
        and selected_label(tab_name)
        and not is_search_page(elements)
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


def _category_info(text: str) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    """Return (description, page-title labels, entry labels) for a route hint."""

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
        base = is_market_shell
        return _with_orientation("行情模块根页面", base, lambda events: True, landscape)

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
                if not _tap_any_text(events, (fund_tab,), f"看资金-{fund_tab}tab"):
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
                if not _tap_any_text(events, (fund_tab,), f"看资金-{fund_tab}tab"):
                    return False
                if not wait_for_page(context_predicate, f"看资金-{fund_tab}列表页"):
                    return False
            if not first_list_item(events):
                return False
            return wait_for_page(is_stock_detail_page, "个股详情页")

        return _with_orientation("个股详情页", is_stock_detail_page, open_fund_detail, landscape)

    if sheet_name in {"港股", "其他"}:
        if _target_is_search(case):
            category_for_search = _category_info(path_text or text)

            def open_search(events: list[dict]) -> bool:
                if category_for_search:
                    _, labels, entries = category_for_search
                    list_predicate = lambda elements: is_named_list_page(elements, labels)
                    if not list_predicate(screen_elements()):
                        if not _tap_any_text(events, entries, category_for_search[0], scroll=True):
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
                    if not _tap_any_text(events, entries, category_for_detail[0], scroll=True):
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
            return _with_orientation(
                description,
                lambda elements: is_named_list_page(elements, labels),
                lambda events: _tap_any_text(events, entries, description, scroll=True),
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
            lambda events: _tap_any_text(events, (tab_name,), f"看资金-{tab_name}tab"),
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

    coords = {"港股": (756, 277), "其他": (972, 277)}
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

    coords = {"港股": (756, 277), "其他": (972, 277)}
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
    if sheet_name in {"港股", "其他"}:
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
            if sheet_name in {"港股", "其他"}:
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


def session_snapshot(session: ModuleSession) -> dict[str, int | str | None]:
    return {
        "policy": "module_cold_start_row_soft_reset",
        "active_sheet": session.active_sheet,
        "cold_start_count": session.cold_start_count,
        "soft_reset_count": session.soft_reset_count,
        "recovery_restart_count": session.recovery_restart_count,
    }


def write_runtime_stats(path: Path, session: ModuleSession) -> None:
    path.write_text(json.dumps(session_snapshot(session), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summarize(elements: list[dict]) -> str:
    """Return only post-action page facts for the result ``actual`` field.

    The executed action already belongs in ``action_trace``.  Repeating the
    Excel action description here makes a command acknowledgement look like a
    page assertion and can hide a wrong-page screenshot.
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


def setup_sheet(
    events: list[dict],
    sheet_name: str,
    row: int,
    case: dict,
    session: ModuleSession,
) -> bool:
    """Prepare one row without cold-starting the app for every row.

    The order here is intentionally fixed and shared by every Sheet:

    ``reset module state -> check target page -> navigate if needed ->
    re-check target page``.  Only a successful target-page assertion allows
    ``execute_action`` to run.
    """

    ok = ensure_module_state(events, sheet_name, session)
    if not ok:
        return False

    if sheet_name in {"港股", "其他"}:
        # Row 2 is the Sheet-switching action itself.  All later rows use the
        # selected Sheet as their public module entry point.
        if row != 2:
            ok = enter_market_home(events, sheet_name) and ok
    elif row != 2 and not is_fund_flow_root(screen_elements()):
        # The first row of 看资金 must still enter the module before its
        # target-page contract is checked.  Subsequent rows already reset to
        # the module root and therefore do not repeat this tap.
        entered = tap_text(events, "看资金")
        if not entered:
            entered = tap_xy(events, 94, 156, "看资金入口（语义入口重试）")
        ok = entered and wait_for_page(is_fund_flow_root, "看资金根页面") and ok
    if not ok:
        return False

    contract = target_page_contract(sheet_name, row, case)
    return ensure_target_page(events, contract)


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
    elif "退出搜索" in operation_text or "返回搜索" in operation_text or "左上角的返回" in operation_text or "点击左上角的返回" in operation_text:
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
        ok = tap_xy(events, 758, 403, "表头字段") and ok

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

    if "更多" in text:
        attempted = True
        ok = tap_xy(events, 954, 691, "更多") and ok

    if any(keyword in text for keyword in ("任意一只股票", "任意个股", "任意一只", "列表中任意", "点击一只", "点击某条")):
        attempted = True
        ok = first_list_item(events) and ok

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="模块级规划、Excel 行级执行的 Android 用例执行器")
    parser.add_argument("--source", default=str(SOURCE), help=".xls/.xlsx 用例文件")
    parser.add_argument("--output", default=str(OUTPUT), help="本轮运行目录")
    parser.add_argument("--sheet", action="append", dest="sheets", help="指定 Sheet，可重复；默认执行预设 Sheet")
    parser.add_argument("--resume", action="store_true", help="从 execution_records.jsonl 继续未完成用例")
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    selected_sheets = tuple(args.sheets or SHEETS)
    output.mkdir(parents=True, exist_ok=True)
    shots = output / "shots"
    shots.mkdir(parents=True, exist_ok=True)

    # LLM/module planning happens once. Runtime execution consumes this plan
    # one case at a time and does not re-parse the workbook per row.
    plan = build_module_plan(source, selected_sheets)
    manifest = plan["execution_manifest"]
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
        for module in plan["modules"]:
            sheet_name = module["sheet"]
            for case in module["cases"]:
                row = int(case["row"])
                if journal.has_case(sheet_name, row):
                    continue

                execution_order += 1
                setup_events: list[dict] = []
                action_events: list[dict] = []
                setup_ok = False
                action_ok = False
                action_mode = "setup_failed"
                error_detail = ""
                try:
                    setup_ok = setup_sheet(setup_events, sheet_name, row, case, session)
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
                    observation = summarize(elements)
                except Exception as exc:
                    error_detail = str(exc)
                    event(action_events, "executor", f"{sheet_name}!{row}", "failed", error_detail)
                    observation = f"执行器异常：{error_detail}"

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
                    "status": status,
                    "action_mode": action_mode,
                    "actual": observation,
                    "observation": observation,
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
                        "type": "case_setup",
                        "result": "success" if setup_ok else "failed",
                        "events": setup_events,
                    }
                )
                write_runtime_stats(runtime_stats_path, session)
                print(f"{sheet_name}!{row}: {status} setup={setup_ok} action={action_ok} evidence={shot_ok}", flush=True)
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
            "policy": "module_cold_start_row_soft_reset",
            "stats": session_snapshot(session),
        }
    )
    final_path = journal.finalize(setup_trace=setup_trace)
    exception_queue = build_exception_queue({"cases": records})
    (output / "exception_queue.json").write_text(
        json.dumps(exception_queue, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rotate([], False)
    print(json.dumps({"manifest": manifest["expected_count"], "records": len(records), "output": str(final_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
