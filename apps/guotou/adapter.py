"""国投证券 App adapter.

Only the behaviour that depends on the 国投/安信白标 UI lives here: package
selection, the 行情/看资金 module roots, market tab coordinates, and the
stable page predicates used by the compatibility runner.  The executor's
Agent plan validation, low-level action dispatch, journal, and evidence flow
remain app-neutral.
"""

from __future__ import annotations

from typing import Any, Iterable

from tools.app_adapter import AppAdapter, AppConfig


MARKET_TABS = {
    "股指": (108, 277),
    "沪深京": (324, 277),
    "板块": (540, 277),
    "港股": (756, 277),
    "其他": (972, 277),
}
TOP_QUOTE_SHEETS = tuple(MARKET_TABS)
FUND_TABS = {
    "自选": (135, 288),
    "沪深京": (405, 288),
    "行业": (675, 288),
    "概念": (945, 288),
}
ROUTE_CANARIES: tuple[tuple[str, int], ...] = (
    ("股指", 2),
    ("股指", 7),
    ("沪深京", 2),
    ("沪深京", 14),
    ("板块", 2),
    ("港股", 2),
    ("港股", 13),
    ("港股", 19),
    ("港股", 26),
    ("其他", 2),
    ("其他", 30),
    ("看资金", 2),
    ("看资金", 8),
    ("个股详情", 3),
    ("个股详情", 11),
    ("个股详情", 14),
    ("个股详情", 22),
    ("个股详情", 49),
)


def has_label(elements: Iterable[dict], label: str) -> bool:
    return any(label == item.get("text") or label == item.get("desc") for item in elements)


def has_id(elements: Iterable[dict], resource_id: str) -> bool:
    return any(resource_id == item.get("id") for item in elements)


def has_title(elements: Iterable[dict], title: str) -> bool:
    return any(
        item.get("id") == "title_bar_middle"
        and (item.get("text") == title or item.get("desc") == title)
        for item in elements
    )


def is_market_shell(elements: list[dict]) -> bool:
    return has_id(elements, "title_bar_middle") and all(
        has_label(elements, label) for label in ("沪深京", "港股", "其他")
    )


def is_fund_flow_root(elements: list[dict]) -> bool:
    title_surface = any(
        has_id(elements, resource_id)
        for resource_id in ("titlebar_leftview_text", "titlebar_left_layout", "title_bar_middle")
    )
    tabs_surface = has_id(elements, "navi_buttonbar") or all(
        has_label(elements, label) for label in ("自选", "沪深京", "行业", "概念")
    )
    return title_surface and has_label(elements, "看资金") and tabs_surface


def is_search_page(elements: list[dict]) -> bool:
    return has_id(elements, "stocksearch") or (
        has_id(elements, "search_edit_layout")
        and has_label(elements, "请输入代码或简拼")
        and has_label(elements, "取消")
    )


def is_stock_detail_page(elements: list[dict]) -> bool:
    if is_search_page(elements):
        return False
    return has_id(elements, "navi_animation_label") and any(
        has_id(elements, resource_id)
        for resource_id in ("backButton", "al_leftbutton", "al_viewfilpper", "navi_title_right")
    )


def is_list_page(elements: list[dict]) -> bool:
    if is_search_page(elements) or is_stock_detail_page(elements):
        return False
    return any(
        has_id(elements, resource_id)
        for resource_id in ("table", "ggt_table", "dragable_listview", "dragablelistview")
    ) or has_label(elements, "返回")


def is_named_list_page(elements: list[dict], labels: Iterable[str]) -> bool:
    if not is_list_page(elements):
        return False
    if has_label(elements, "返回") and any(has_label(elements, label) for label in labels):
        return True
    return has_id(elements, "table") and has_id(elements, "dragable_listview_header")


def is_market_home(elements: list[dict], sheet_name: str) -> bool:
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
    return (
        has_id(elements, "title_bar_middle")
        and has_id(elements, "titlebar_left_layout")
        and (
            has_id(elements, "tips_view")
            or has_id(elements, "title_bar_right1")
            or has_id(elements, "new_title_search")
        )
    )


def is_fund_flow_tab_page(
    elements: list[dict], tab_name: str, *, selected: bool = False
) -> bool:
    return (
        is_fund_flow_root(elements)
        and has_label(elements, tab_name)
        and not is_search_page(elements)
        and (selected or has_id(elements, "navi_buttonbar"))
    )


class GuotouAdapter(AppAdapter):
    name = "guotou"
    supports_legacy_deterministic = True

    @property
    def top_quote_sheets(self) -> tuple[str, ...]:
        return TOP_QUOTE_SHEETS

    @property
    def market_tabs(self) -> dict[str, tuple[int, int]]:
        return dict(MARKET_TABS)

    @property
    def probe_canaries(self) -> tuple[tuple[str, int], ...]:
        return ROUTE_CANARIES

    @property
    def preferred_packages(self) -> tuple[str, ...]:
        configured = self.config.document.get("runtime_package")
        preferred = (str(configured).strip(),) if configured else ()
        return tuple(dict.fromkeys((*preferred, *self.config.packages)))

    @staticmethod
    def is_market_shell(elements: list[dict]) -> bool:
        return is_market_shell(elements)

    @staticmethod
    def is_fund_flow_root(elements: list[dict]) -> bool:
        return is_fund_flow_root(elements)

    @staticmethod
    def is_search_page(elements: list[dict]) -> bool:
        return is_search_page(elements)

    @staticmethod
    def is_stock_detail_page(elements: list[dict]) -> bool:
        return is_stock_detail_page(elements)

    @staticmethod
    def is_list_page(elements: list[dict]) -> bool:
        return is_list_page(elements)

    @staticmethod
    def is_named_list_page(elements: list[dict], labels: Iterable[str]) -> bool:
        return is_named_list_page(elements, labels)

    @staticmethod
    def is_market_home(elements: list[dict], sheet_name: str) -> bool:
        return is_market_home(elements, sheet_name)

    def is_fund_flow_tab_page(self, elements: list[dict], tab_name: str) -> bool:
        selected = bool(self.runtime and self.runtime.selected_label(tab_name))
        return is_fund_flow_tab_page(elements, tab_name, selected=selected)

    def launch(self, events: list[dict]) -> bool:
        runtime = self._runtime()
        if not runtime.launch_packages(events, self.preferred_packages):
            return False
        if runtime.tap_text(events, "行情") and runtime.wait_for_page(
            self.is_market_shell, "行情根页面"
        ):
            return True
        if runtime.tap_xy(events, 324, 2263, "底部行情（坐标重试）"):
            return runtime.wait_for_page(self.is_market_shell, "行情根页面")
        return False

    def is_module_root(self, sheet_name: str, elements: list[dict]) -> bool:
        if sheet_name == "看资金":
            return self.is_fund_flow_root(elements)
        # 个股详情 and other quote-oriented sheets return to the shared
        # 行情 shell before their own Agent/legacy navigation continues.
        return self.is_market_shell(elements)

    def soft_reset_to_module_root(self, events: list[dict], sheet_name: str) -> bool:
        runtime = self._runtime()
        elements = runtime.screen_elements()
        if self.is_module_root(sheet_name, elements):
            return True

        if sheet_name == "看资金" and self.is_market_shell(elements):
            if runtime.tap_xy(events, 94, 156, "看资金入口（软复位）"):
                return runtime.wait_for_page(self.is_fund_flow_root, "看资金根页面")
            return False

        for attempt in range(runtime.max_soft_back):
            if not runtime.key_back(events):
                return False
            elements = runtime.screen_elements()
            if self.is_module_root(sheet_name, elements):
                runtime.event(
                    events,
                    "reset",
                    f"{sheet_name}模块根页面",
                    "success",
                    f"返回次数={attempt + 1}",
                )
                return True

        if has_label(elements, "行情"):
            if runtime.tap_text(events, "行情") and runtime.wait_for_page(
                self.is_market_shell, "行情根页面"
            ):
                if sheet_name in TOP_QUOTE_SHEETS:
                    return True
                if runtime.tap_xy(events, 94, 156, "看资金入口（软复位）"):
                    return runtime.wait_for_page(self.is_fund_flow_root, "看资金根页面")
        return False

    def tap_market_tab(self, events: list[dict], sheet_name: str) -> bool:
        runtime = self._runtime()
        coords = MARKET_TABS
        if sheet_name not in coords:
            return False
        if runtime.selected_label(sheet_name):
            runtime.event(events, "assert", f"顶部{sheet_name}", "success", "目标 Tab 已处于选中状态")
            return True
        x, y = coords[sheet_name]
        for attempt in range(runtime.max_action_retries + 1):
            if attempt:
                runtime.event(
                    events,
                    "retry",
                    f"顶部{sheet_name}",
                    "attempt",
                    "Tab 选中状态未确认，重试一次",
                )
            if runtime.tap_xy(events, x, y, f"顶部{sheet_name}") and runtime.wait_for_selected(sheet_name):
                runtime.event(events, "assert", f"顶部{sheet_name}", "success", "目标 Tab 已选中")
                return True
        runtime.event(
            events,
            "assert",
            f"顶部{sheet_name}",
            "failed",
            "点击命令已发送，但目标 Tab 未进入选中状态",
        )
        return False

    def tap_fund_tab(self, events: list[dict], tab_name: str) -> bool:
        runtime = self._runtime()
        if tab_name not in FUND_TABS:
            return runtime.tap_text(events, tab_name)
        x, y = FUND_TABS[tab_name]
        return runtime.tap_xy(events, x, y, f"看资金-{tab_name}tab")

    def enter_module(self, events: list[dict], sheet_name: str) -> bool:
        if sheet_name in TOP_QUOTE_SHEETS:
            return self.enter_market_home(events, sheet_name)
        runtime = self._runtime()
        if self.is_fund_flow_root(runtime.screen_elements()):
            return True
        entered = runtime.tap_text(events, "看资金")
        if not entered:
            entered = runtime.tap_xy(events, 94, 156, "看资金入口（语义入口重试）")
        return entered and runtime.wait_for_page(self.is_fund_flow_root, "看资金根页面")

    def enter_market_home(self, events: list[dict], sheet_name: str) -> bool:
        runtime = self._runtime()
        home_predicate = lambda elements: self.is_market_home(elements, sheet_name)
        if home_predicate(runtime.screen_elements()):
            runtime.event(events, "assert", f"{sheet_name}模块首页", "success", "目标 Sheet 首页已处于前台")
            return True
        if sheet_name not in MARKET_TABS:
            return False
        x, y = MARKET_TABS[sheet_name]
        for attempt in range(runtime.max_action_retries + 1):
            if attempt:
                runtime.event(
                    events,
                    "retry",
                    f"{sheet_name}模块首页",
                    "attempt",
                    "Tab 已选中但首页未确认，重新进入首页",
                )
            if runtime.tap_xy(events, x, y, f"顶部{sheet_name}（首页重试）") and runtime.wait_for_page(
                home_predicate, f"{sheet_name}模块首页"
            ):
                runtime.event(events, "assert", f"{sheet_name}模块首页", "success", "顶部 Tab 入口后首页复核通过")
                return True
            if runtime.tap_text(events, sheet_name) and runtime.wait_for_page(
                home_predicate, f"{sheet_name}模块首页"
            ):
                runtime.event(events, "assert", f"{sheet_name}模块首页", "success", "语义入口后首页复核通过")
                return True
        runtime.event(
            events,
            "assert",
            f"{sheet_name}模块首页",
            "failed",
            "Tab 选中但无法确认已进入 Sheet 首页",
        )
        return False

    @staticmethod
    def legacy_module():
        from apps.guotou import legacy

        return legacy

    def legacy_target_page_contract(self, sheet_name: str, row: int, case: dict):
        return self.legacy_module().target_page_contract(sheet_name, row, case)

    def legacy_setup_sheet(self, *args, **kwargs) -> bool:
        return bool(self.legacy_module().setup_sheet(*args, **kwargs))

    def legacy_execute_action(self, *args, **kwargs):
        return self.legacy_module().execute_action(*args, **kwargs)


def create_adapter(config: AppConfig, runtime: Any = None) -> GuotouAdapter:
    return GuotouAdapter(config, runtime)
