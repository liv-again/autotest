"""Compatibility adapter for the quote regression runner.

The tracked runner now requires ``--action-plan`` for normal execution and
does not call this adapter's fixed action parser in that mode.  The detail
overrides below remain available only when a caller explicitly selects
``--legacy-deterministic`` during migration diagnostics.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tools import _run_three_sheets as base  # noqa: E402


DETAIL_SHEET = "个股详情"
DETAIL_MARKET = "沪深京"
STOCK_CODE = "600000"
STOCK_NAME = "浦发银行"


def _blob(elements: list[dict]) -> str:
    return " ".join(
        f"{item.get('text', '')} {item.get('desc', '')} {item.get('id', '')}"
        for item in elements
    )


def _has_any_id(elements: list[dict], ids: set[str]) -> bool:
    return any(item.get("id") in ids for item in elements)


def _is_detail_600000(elements: list[dict]) -> bool:
    if not base.is_stock_detail_page(elements):
        return False
    blob = _blob(elements)
    return STOCK_CODE in blob or STOCK_NAME in blob


def _is_search_page(elements: list[dict]) -> bool:
    return base.is_search_page(elements)


def _is_quote_popup(elements: list[dict]) -> bool:
    return _has_any_id(
        elements,
        {"fenshi_head_pankou", "fenshi_pop_line", "fenshi_hangqing_detail_layout"},
    )


def _is_five_page(elements: list[dict]) -> bool:
    return _is_detail_600000(elements) and base.has_id(elements, "five_buy_sale_new")


def _is_mingxi_page(elements: list[dict]) -> bool:
    return _is_detail_600000(elements) and _has_any_id(
        elements, {"mingxilayout", "cjmx_component"}
    )


def _is_pankou_page(elements: list[dict]) -> bool:
    return _is_detail_600000(elements) and base.has_id(elements, "fenshi_pankou_gg_title3")


def _is_kline_page(elements: list[dict]) -> bool:
    return _is_detail_600000(elements) and (
        _has_any_id(elements, {"curveview", "day_period_layout", "periodlayout"})
        or any(label in _blob(elements) for label in ("日K", "周K", "月K"))
    )


def _is_fund_root_actual(elements: list[dict]) -> bool:
    """The live build exposes the fund-flow title as title_bar_middle."""

    return (
        base.has_id(elements, "title_bar_middle")
        and base.has_label(elements, "看资金")
        and base.has_id(elements, "navi_buttonbar")
        and all(base.has_label(elements, label) for label in ("自选", "沪深京", "行业", "概念"))
    )


def _is_fund_tab_actual(elements: list[dict], tab_name: str) -> bool:
    return _is_fund_root_actual(elements) and base.has_label(elements, tab_name) and (
        base.has_id(elements, "table") or base.has_id(elements, "dragable_listview")
    )


def _enter_fund_root(events: list[dict]) -> bool:
    if _is_fund_root_actual(base.screen_elements()):
        return True
    for attempt in range(base.MAX_ACTION_RETRIES + 1):
        if attempt:
            base.event(events, "retry", "看资金入口", "attempt", "看资金根页面未确认，重试一次")
        if base.tap_xy(events, 94, 156, "看资金入口") and base.wait_for_page(
            _is_fund_root_actual, "看资金根页面"
        ):
            return True
    base.event(events, "assert", "看资金根页面", "failed", "无法确认进入看资金根页面")
    return False


def _type_stock_code(events: list[dict]) -> bool:
    rc, _, err = base.droid.adb("shell", "input", "text", STOCK_CODE)
    ok = rc == 0
    base.event(events, "type", STOCK_CODE, "success" if ok else "failed", err.strip())
    base.wait_short(0.8)
    return ok


def _open_stock_search(events: list[dict]) -> bool:
    if _is_search_page(base.screen_elements()):
        return True
    for attempt in range(base.MAX_ACTION_RETRIES + 1):
        if attempt:
            base.event(events, "retry", "股票搜索入口", "attempt", "搜索页未确认，重试一次")
        opened = base.tap_id(events, "new_title_search")
        if not opened:
            opened = base.tap_xy(events, 1005, 156, "右上角股票搜索（坐标重试）")
        if opened and base.wait_for_page(_is_search_page, "股票搜索页"):
            return True
    base.event(events, "assert", "股票搜索页", "failed", "无法确认已进入股票搜索页")
    return False


def _open_stock_detail(events: list[dict]) -> bool:
    elements = base.screen_elements()
    if _is_detail_600000(elements):
        return True
    if not _open_stock_search(events):
        return False
    if not _type_stock_code(events):
        return False
    if base.wait_for_page(_is_detail_600000, "600000浦发银行股票详情页"):
        return True

    # This build normally opens the detail page as soon as the code is typed.
    # Keep one semantic result-tap fallback for builds that retain the result
    # list after input.
    base.event(events, "retry", STOCK_NAME, "attempt", "输入代码后仍在搜索页，点击语义结果一次")
    clicked = base.tap_text(events, STOCK_NAME)
    if not clicked:
        clicked = base.tap_text(events, STOCK_CODE)
    return clicked and base.wait_for_page(_is_detail_600000, "600000浦发银行股票详情页")


def _select_detail_tab(
    events: list[dict],
    *,
    resource_id: str | None,
    label: str | None,
    predicate,
    description: str,
) -> bool:
    for attempt in range(base.MAX_ACTION_RETRIES + 1):
        if attempt:
            base.event(events, "retry", description, "attempt", "目标区域未确认，重试一次")
        clicked = False
        if resource_id:
            clicked = base.tap_id(events, resource_id)
        if not clicked and label:
            clicked = base.tap_text(events, label)
        if clicked and base.wait_for_page(predicate, description):
            return True
    base.event(events, "assert", description, "failed", "点击命令已发送，但目标区域未确认")
    return False


def _prepare_detail_target(events: list[dict], row: int) -> bool:
    if row == 2:
        return _open_stock_search(events)
    if not _open_stock_detail(events):
        return False
    if row == 5:
        return _select_detail_tab(
            events,
            resource_id="fenshi_headline_view",
            label="现价",
            predicate=_is_quote_popup,
            description="行情数据框",
        )
    if row == 12:
        return _select_detail_tab(
            events,
            resource_id="ll_wudang",
            label="五档",
            predicate=_is_five_page,
            description="五档区域",
        )
    if row == 15:
        return _select_detail_tab(
            events,
            resource_id="ll_mingxi",
            label="明细",
            predicate=_is_mingxi_page,
            description="明细区域",
        )
    if row == 27:
        return _select_detail_tab(
            events,
            resource_id=None,
            label="盘口",
            predicate=_is_pankou_page,
            description="盘口内容区域",
        )
    if row == 50:
        if not base.swipe(events, 850, 1000, 150, 1000, "左滑分时页面进入K线"):
            return False
        return base.wait_for_page(_is_kline_page, "K线页面")
    return base.wait_for_page(_is_detail_600000, "600000浦发银行股票详情页")


def setup_detail(
    events: list[dict],
    sheet_name: str,
    row: int,
    case: dict,
    session: base.ModuleSession,
    page_group_id: str | None = None,
    page_group_key: str | None = None,
) -> bool:
    del case, page_group_id, page_group_key
    if sheet_name != DETAIL_SHEET:
        return False
    if not base.ensure_module_state(events, DETAIL_SHEET, session):
        return False
    if not base.enter_market_home(events, DETAIL_MARKET):
        return False
    return _prepare_detail_target(events, row)


def _is_quote_home(elements: list[dict], sheet_name: str) -> bool:
    return base.is_market_home(elements, sheet_name)


def _setup_quote_override(
    events: list[dict],
    sheet_name: str,
    row: int,
    session: base.ModuleSession,
) -> bool | None:
    """Correct three ambiguous P0 route contracts in the legacy runner."""

    special = {
        ("股指", 3),       # observe the home-page 国内指数 grid
        ("沪深京", 3),     # observe the home-page three-card area
        ("沪深京", 14),    # observe the 沪深A股 independent list
        ("板块", 3),       # observe the 板块 home page
        ("其他", 30),      # observe the 三板 independent list
        ("看资金", 2),      # observe the fund-flow module identity
        ("看资金", 8),      # observe a fund-flow list tab
    }
    if (sheet_name, row) not in special:
        return None
    if not base.ensure_module_state(events, sheet_name, session):
        return False
    if sheet_name == "看资金":
        if not _enter_fund_root(events):
            return False
        if row == 8:
            if not base.tap_fund_tab(events, "沪深京"):
                return False
            return base.wait_for_page(
                lambda elements: _is_fund_tab_actual(elements, "沪深京"),
                "看资金-沪深京列表页",
            )
        return base.wait_for_page(_is_fund_root_actual, "看资金根页面")
    if not base.enter_market_home(events, sheet_name):
        return False
    if (sheet_name, row) in {
        ("股指", 3),
        ("沪深京", 3),
        ("板块", 3),
    }:
        return base.wait_for_page(
            lambda elements: _is_quote_home(elements, sheet_name),
            f"{sheet_name}模块首页",
        )
    if (sheet_name, row) == ("沪深京", 14):
        if not base._tap_section_more(events, "涨幅榜", "沪深A股列表", scroll=True):
            return False
        return base.wait_for_page(
            lambda elements: base.is_named_list_page(
                elements, ("沪深A股", "沪深京", "涨幅榜")
            ),
            "沪深A股列表页",
        )
    if (sheet_name, row) == ("其他", 30):
        if not base._tap_any_text(events, ("三板", "新三板"), "三板列表页", scroll=True):
            return False
        return base.wait_for_page(
            lambda elements: base.is_named_list_page(elements, ("三板", "新三板")),
            "三板列表页",
        )
    return False


def execute_detail_action(
    events: list[dict], sheet_name: str, row: int, case: dict
) -> tuple[bool, str, str]:
    del case
    if sheet_name != DETAIL_SHEET:
        return False, "未识别详情 Sheet", "unrecognized"
    if row == 2:
        if not _type_stock_code(events):
            return False, "输入600000失败", "action"
        if base.wait_for_page(_is_detail_600000, "600000浦发银行股票详情页"):
            return True, "", "action"
        base.event(events, "retry", STOCK_NAME, "attempt", "输入后未进入详情页，点击语义结果一次")
        clicked = base.tap_text(events, STOCK_NAME) or base.tap_text(events, STOCK_CODE)
        ok = clicked and base.wait_for_page(_is_detail_600000, "600000浦发银行股票详情页")
        return ok, "输入后未确认600000详情页" if not ok else "", "action"
    if row == 11:
        ok = _select_detail_tab(
            events,
            resource_id="ll_wudang",
            label="五档",
            predicate=_is_five_page,
            description="五档区域",
        )
        return ok, "点击五档后未确认五档区域" if not ok else "", "action"
    if row == 14:
        ok = _select_detail_tab(
            events,
            resource_id="ll_mingxi",
            label="明细",
            predicate=_is_mingxi_page,
            description="明细区域",
        )
        return ok, "点击明细后未确认明细区域" if not ok else "", "action"
    if row == 22:
        ok = _select_detail_tab(
            events,
            resource_id=None,
            label="盘口",
            predicate=_is_pankou_page,
            description="盘口内容区域",
        )
        return ok, "点击盘口后未确认盘口内容" if not ok else "", "action"
    if row == 49:
        ok = base.swipe(events, 850, 1000, 150, 1000, "左滑分时页面进入K线")
        if ok:
            ok = base.wait_for_page(_is_kline_page, "K线页面")
        return ok, "左滑后未确认K线页面" if not ok else "", "action"
    base.event(events, "observe", "当前详情页面与数据区域", "success", "显式观察类步骤，需根据页面事实判定")
    return True, "", "observe"


_original_module_root_visible = base.module_root_visible
_original_setup_sheet = base.setup_sheet
_original_execute_action = base.execute_action


def module_root_visible(sheet_name: str, elements: list[dict]) -> bool:
    if sheet_name == DETAIL_SHEET:
        return base.is_market_shell(elements)
    if sheet_name == "看资金":
        return _is_fund_root_actual(elements)
    return _original_module_root_visible(sheet_name, elements)


def setup_sheet(*args, **kwargs) -> bool:
    sheet_name = args[1] if len(args) > 1 else kwargs.get("sheet_name")
    if sheet_name == DETAIL_SHEET:
        return setup_detail(*args, **kwargs)
    row = args[2] if len(args) > 2 else kwargs.get("row")
    session = args[4] if len(args) > 4 else kwargs.get("session")
    if session is not None:
        override = _setup_quote_override(args[0], sheet_name, row, session)
        if override is not None:
            return override
    return _original_setup_sheet(*args, **kwargs)


def execute_action(*args, **kwargs) -> tuple[bool, str, str]:
    sheet_name = args[1] if len(args) > 1 else kwargs.get("sheet_name")
    if sheet_name == DETAIL_SHEET:
        return execute_detail_action(*args, **kwargs)
    return _original_execute_action(*args, **kwargs)


base.module_root_visible = module_root_visible
base.setup_sheet = setup_sheet
base.execute_action = execute_action


def main(argv: list[str] | None = None) -> int:
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


