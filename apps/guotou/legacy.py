"""Guotou legacy deterministic route compatibility.

This module is intentionally isolated from the generic runner.  It is loaded
only when an App explicitly opts into --legacy-deterministic; normal Agent
runs never call these functions.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Iterable

from tools.page_execution import PageContract, run_independent_entries, search_entry_two_way
from apps.guotou.adapter import TOP_QUOTE_SHEETS


def _runner():
    # Imported lazily because the generic runner loads this module through the
    # Guotou adapter after its own module has finished importing.
    main_module = sys.modules.get("__main__")
    if main_module is not None and str(getattr(main_module, "__file__", "")).endswith(
        "_run_three_sheets.py"
    ):
        return main_module
    from tools import _run_three_sheets as runner
    return runner


def _call(name: str, *args, **kwargs):
    return getattr(_runner(), name)(*args, **kwargs)


# These tiny proxies keep the moved compatibility code independent from the
# runner's module namespace.  They are not new action parsers.
def event(*args, **kwargs): return _call("event", *args, **kwargs)
def screen_elements(*args, **kwargs): return _call("screen_elements", *args, **kwargs)
def wait_for_page(*args, **kwargs): return _call("wait_for_page", *args, **kwargs)
def wait_for_orientation(*args, **kwargs): return _call("wait_for_orientation", *args, **kwargs)
def orientation_matches(*args, **kwargs): return _call("orientation_matches", *args, **kwargs)
def rotate(*args, **kwargs): return _call("rotate", *args, **kwargs)
def swipe(*args, **kwargs): return _call("swipe", *args, **kwargs)
def tap_text(*args, **kwargs): return _call("tap_text", *args, **kwargs)
def tap_id(*args, **kwargs): return _call("tap_id", *args, **kwargs)
def tap_xy(*args, **kwargs): return _call("tap_xy", *args, **kwargs)
def key_back(*args, **kwargs): return _call("key_back", *args, **kwargs)
def first_list_item(*args, **kwargs): return _call("first_list_item", *args, **kwargs)
def ensure_module_state(*args, **kwargs): return _call("ensure_module_state", *args, **kwargs)
def ensure_page_group_state(*args, **kwargs): return _call("ensure_page_group_state", *args, **kwargs)
def soft_reset_to_module_root(*args, **kwargs): return _call("soft_reset_to_module_root", *args, **kwargs)
def enter_market_home(*args, **kwargs): return _call("enter_market_home", *args, **kwargs)
def tap_market_tab(*args, **kwargs): return _call("tap_market_tab", *args, **kwargs)
def tap_fund_tab(*args, **kwargs): return _call("tap_fund_tab", *args, **kwargs)
def is_market_shell(*args, **kwargs): return _call("is_market_shell", *args, **kwargs)
def is_fund_flow_root(*args, **kwargs): return _call("is_fund_flow_root", *args, **kwargs)
def is_search_page(*args, **kwargs): return _call("is_search_page", *args, **kwargs)
def is_stock_detail_page(*args, **kwargs): return _call("is_stock_detail_page", *args, **kwargs)
def is_list_page(*args, **kwargs): return _call("is_list_page", *args, **kwargs)
def is_named_list_page(*args, **kwargs): return _call("is_named_list_page", *args, **kwargs)
def is_market_home(*args, **kwargs): return _call("is_market_home", *args, **kwargs)
def has_label(*args, **kwargs): return _call("has_label", *args, **kwargs)
def has_id(*args, **kwargs): return _call("has_id", *args, **kwargs)


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
    """Legacy fixed-rule executor kept only behind ``--legacy-deterministic``.

    The normal path never calls this function.  Agent-authored runs use the
    structured action executor, so arbitrary Excel action prose cannot be
    silently translated by this compatibility parser.
    """

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


