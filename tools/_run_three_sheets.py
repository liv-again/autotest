"""Generic Agent-driven Excel/App execution core.

App-specific UI behaviour is provided by ``tools.app_adapter``.  The default
selection remains the Guotou profile for backward compatibility, but the
runner itself does not own Guotou package names, coordinates, or page rules.
"""

from __future__ import annotations

import datetime
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import xlrd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import droid
from tools.exception_queue import build_exception_queue
from tools.execution_journal import ExecutionJournal, JournalError
from tools.execution_gate import load_execution_policy
from tools.module_planner import build_module_plan
from tools.agent_plan import AgentPlanError, load_action_plan, sha256_file
from tools.agent_binding import resolve_agent_binding
from tools.agent_session import (
    FACTORY_ENV,
    create_agent_session,
    session_factory_available,
)
from tools.llm_retest import RetestLimits, run_retest_case
from tools.app_adapter import (
    AppAdapter,
    AppAdapterError,
    AppConfig,
    infer_app_slug_from_profile,
    load_app_adapter,
    load_app_config,
)
from tools.page_execution import (
    PageContract,
    ensure_target_page as _ensure_target_page,
    run_independent_entries,
    search_entry_two_way,
)
from tools.profile_feedback import build_profile_feedback
from tools.llm_review_queue import build_review_queue
from tools.retest_results import (
    RetestError,
    merge_retests,
    plan_retests,
    write_json as write_retest_json,
)
from tools.results_quality import append_judgment_reason


# The desktop image still exposes Python 3.7 for the subprocess launched by
# the deferred blocked-retest phase.  Keep the native BooleanOptionalAction
# when available, and provide the same --flag/--no-flag contract on older
# argparse versions so a completed first pass can resume safely.
if not hasattr(argparse, "BooleanOptionalAction"):
    class _BooleanOptionalAction(argparse.Action):
        def __init__(self, option_strings, dest, default=None, **kwargs):
            expanded = []
            for option_string in option_strings:
                expanded.append(option_string)
                if option_string.startswith("--"):
                    expanded.append("--no-" + option_string[2:])
            super().__init__(expanded, dest, nargs=0, const=None, default=default, **kwargs)

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, not str(option_string or "").startswith("--no-"))

    argparse.BooleanOptionalAction = _BooleanOptionalAction


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP_SLUG = "guotou"
DEFAULT_DEVICE = "c923178d"
# Compatibility aliases for callers that imported the old runner module.
# Runtime execution resolves the selected App from --app/app.yaml.
DEVICE = DEFAULT_DEVICE
SOURCE: Path | None = None
APP_PROFILE: Path | None = None
OUTPUT: Path | None = None
SHOTS: Path | None = None
MAX_SOFT_BACK = 3
PAGE_READY_RETRIES = 5
_EXECUTION_POLICY = load_execution_policy()

# main() binds the selected adapter before device work starts.  The lazy
# Guotou compatibility adapter keeps the old helper API usable by diagnostics
# and tests without making the generic runner import one app's UI rules at
# module import time.
_ACTIVE_ADAPTER: AppAdapter | None = None
_ACTIVE_APP_CONFIG: AppConfig | None = None
_COMPAT_ADAPTER: AppAdapter | None = None


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


class RunnerAdapterRuntime:
    """Small callback facade exposed to App adapters.

    Methods resolve the runner globals at call time, so existing tests and
    compatibility wrappers can still monkey-patch a helper without the
    adapter retaining stale function references.
    """

    @property
    def max_soft_back(self) -> int:
        return MAX_SOFT_BACK

    @property
    def max_action_retries(self) -> int:
        return MAX_ACTION_RETRIES

    def event(self, *args, **kwargs):
        return event(*args, **kwargs)

    def screen_elements(self):
        return screen_elements()

    def wait_for_page(self, *args, **kwargs):
        return wait_for_page(*args, **kwargs)

    def wait_for_selected(self, *args, **kwargs):
        return wait_for_selected(*args, **kwargs)

    def selected_label(self, *args, **kwargs):
        return selected_label(*args, **kwargs)

    def tap_text(self, *args, **kwargs):
        return tap_text(*args, **kwargs)

    def tap_id(self, *args, **kwargs):
        return tap_id(*args, **kwargs)

    def tap_xy(self, *args, **kwargs):
        return tap_xy(*args, **kwargs)

    def key_back(self, *args, **kwargs):
        return key_back(*args, **kwargs)

    def rotate(self, *args, **kwargs):
        return rotate(*args, **kwargs)

    def launch_packages(self, *args, **kwargs):
        return launch_packages(*args, **kwargs)


_RUNNER_ADAPTER_RUNTIME = RunnerAdapterRuntime()


def _active_adapter() -> AppAdapter:
    """Return the bound App adapter, with a Guotou-only test compatibility fallback."""

    global _COMPAT_ADAPTER
    if _ACTIVE_ADAPTER is not None:
        return _ACTIVE_ADAPTER
    if _COMPAT_ADAPTER is None:
        compatibility_config = load_app_config(PROJECT_ROOT, DEFAULT_APP_SLUG)
        _COMPAT_ADAPTER = load_app_adapter(compatibility_config, _RUNNER_ADAPTER_RUNTIME)
    return _COMPAT_ADAPTER


def _top_quote_sheets() -> tuple[str, ...]:
    value = getattr(_active_adapter(), "top_quote_sheets", ())
    return tuple(value or ())


def event(events: list[dict], kind: str, target: str, result: str, detail: str = "") -> None:
    item = {"type": kind, "target": target, "result": result, "timestamp": now()}
    if detail:
        item["detail"] = detail
    events.append(item)


def launch_packages(events: list[dict], packages: Iterable[str]) -> bool:
    """Common package launcher used by every adapter.

    The runner never chooses an App package by itself.  It receives an ordered
    candidate list from the adapter/configuration and tries each candidate
    once, recording the actual package command in the trace.
    """

    candidates = tuple(dict.fromkeys(str(package).strip() for package in packages if str(package).strip()))
    if not candidates:
        event(events, "launch", "App package", "failed", "当前 App 未配置 packages")
        return False
    for package in candidates:
        rc1, _, err1 = droid.adb("-s", DEVICE, "shell", "am", "force-stop", package)
        rc2, _, err2 = droid.adb("-s", DEVICE, "shell", "monkey", "-p", package, "1")
        wait_short(1.8)
        ok = rc1 == 0 and rc2 == 0
        event(events, "launch", package, "success" if ok else "failed", (err1 + err2).strip())
        if ok:
            return True
    return False


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


def swipe_duration(
    events: list[dict],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int,
    target: str,
) -> bool:
    """Execute one bounded swipe selected by an Agent action plan."""

    rc, _, err = droid.adb(
        "shell",
        "input",
        "swipe",
        str(x1),
        str(y1),
        str(x2),
        str(y2),
        str(duration_ms),
    )
    ok = rc == 0
    event(events, "swipe", target, "success" if ok else "failed", err.strip())
    wait_short()
    return ok


def type_text(events: list[dict], text: str) -> bool:
    """Type text from a validated Agent plan and record the real action."""

    rc, _, err = droid.adb("shell", "input", "text", text)
    ok = rc == 0
    # Do not put the value in the event target: account/code data should not
    # be copied into the readable report or logs.  The plan remains the
    # auditable source of the requested input action.
    event(events, "type", "输入框", "success" if ok else "failed", err.strip())
    wait_short()
    return ok


def key_name(events: list[dict], key: str) -> bool:
    """Press one of the small allow-list of Android key names."""

    key = str(key).upper()
    rc, _, err = droid.adb("shell", "input", "keyevent", f"KEYCODE_{key}")
    ok = rc == 0
    event(events, "key", key, "success" if ok else "failed", err.strip())
    wait_short()
    return ok


def wait_action(events: list[dict], seconds: float) -> bool:
    seconds = max(0.0, float(seconds))
    event(events, "wait", f"{seconds:g}秒", "success", "Agent 计划要求等待页面稳定")
    if seconds:
        time.sleep(seconds)
    return True


def rotate(events: list[dict], landscape: bool) -> bool:
    rotation = "1" if landscape else "0"
    rc1, _, err1 = droid.adb("-s", DEVICE, "shell", "settings", "put", "system", "accelerometer_rotation", "0")
    rc2, _, err2 = droid.adb("-s", DEVICE, "shell", "settings", "put", "system", "user_rotation", rotation)
    ok = rc1 == 0 and rc2 == 0
    event(events, "orientation", "横屏" if landscape else "竖屏", "success" if ok else "failed", (err1 + err2).strip())
    wait_short(1.0)
    return ok


def launch_market(events: list[dict]) -> bool:
    """Compatibility wrapper; package/module launch belongs to the adapter."""

    return _active_adapter().launch(events)


def screen_elements() -> list[dict]:
    try:
        return droid.parse(droid.dump_xml())
    except Exception:
        return []


_CONTROL_LOOKUP_ACTIONS = frozenset({"tap_text", "tap_id", "assert_text", "assert_id"})


def _control_lookup_failed(action_type: str, action_events: list[dict]) -> bool:
    """Classify a selector/assertion failure without interpreting Excel text."""

    if action_type not in _CONTROL_LOOKUP_ACTIONS or not action_events:
        return False
    latest = action_events[-1]
    if latest.get("type") not in {"tap", "assert"}:
        return False
    return str(latest.get("result") or "").casefold() in {"not_found", "failed"}


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
    """Compatibility wrapper; the actual contract belongs to the App adapter."""

    predicate = getattr(_active_adapter(), "is_market_shell", None)
    return bool(predicate and predicate(elements))


def is_fund_flow_root(elements: list[dict]) -> bool:
    """Compatibility wrapper; the actual contract belongs to the App adapter."""

    predicate = getattr(_active_adapter(), "is_fund_flow_root", None)
    return bool(predicate and predicate(elements))


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
    predicate = getattr(_active_adapter(), "is_search_page", None)
    return bool(predicate and predicate(elements))


def is_stock_detail_page(elements: list[dict]) -> bool:
    predicate = getattr(_active_adapter(), "is_stock_detail_page", None)
    return bool(predicate and predicate(elements))


def is_list_page(elements: list[dict]) -> bool:
    predicate = getattr(_active_adapter(), "is_list_page", None)
    return bool(predicate and predicate(elements))


def is_named_list_page(elements: list[dict], labels: Iterable[str]) -> bool:
    predicate = getattr(_active_adapter(), "is_named_list_page", None)
    return bool(predicate and predicate(elements, labels))


def is_market_home(elements: list[dict], sheet_name: str) -> bool:
    predicate = getattr(_active_adapter(), "is_market_home", None)
    return bool(predicate and predicate(elements, sheet_name))


def is_fund_flow_tab_page(elements: list[dict], tab_name: str) -> bool:
    predicate = getattr(_active_adapter(), "is_fund_flow_tab_page", None)
    return bool(predicate and predicate(elements, tab_name))


def _legacy_compat_module():
    loader = getattr(_active_adapter(), "legacy_module", None)
    if not callable(loader):
        raise AppAdapterError(
            f"App {_ACTIVE_APP_CONFIG.slug if _ACTIVE_APP_CONFIG else 'unknown'!r} 未提供 legacy 模块"
        )
    module = loader()
    if module is None:
        raise AppAdapterError(
            f"App {_ACTIVE_APP_CONFIG.slug if _ACTIVE_APP_CONFIG else 'unknown'!r} 未提供 legacy 模块"
        )
    return module


def _legacy_call(name: str, *args, **kwargs):
    return getattr(_legacy_compat_module(), name)(*args, **kwargs)


def _case_text(case: dict, keys: Iterable[str] = ("case_name", "entry", "step_name", "action", "precondition")) -> str:
    return _legacy_call("_case_text", case, keys)


def _action_lines(case: dict) -> list[str]:
    return _legacy_call("_action_lines", case)


def _target_orientation(case: dict) -> bool | None:
    return _legacy_call("_target_orientation", case)


def _tap_any_text(events: list[dict], labels: Iterable[str], target: str, *, scroll: bool = False) -> bool:
    return _legacy_call("_tap_any_text", events, labels, target, scroll=scroll)


def _tap_section_more(events: list[dict], section_label: str, target: str, *, scroll: bool = True) -> bool:
    return _legacy_call("_tap_section_more", events, section_label, target, scroll=scroll)


def _open_hk_connect_list(events: list[dict], market: str) -> bool:
    return _legacy_call("_open_hk_connect_list", events, market)


def _open_category_list(events: list[dict], sheet_name: str, category) -> bool:
    return _legacy_call("_open_category_list", events, sheet_name, category)


def _category_info(text: str):
    return _legacy_call("_category_info", text)


def _direct_navigation_action(case: dict) -> bool:
    return _legacy_call("_direct_navigation_action", case)


def _target_is_search(case: dict) -> bool:
    return _legacy_call("_target_is_search", case)


def _target_is_detail(case: dict) -> bool:
    return _legacy_call("_target_is_detail", case)


def target_page_contract(sheet_name: str, row: int, case: dict) -> PageContract:
    handler = getattr(_active_adapter(), "legacy_target_page_contract", None)
    if not callable(handler):
        raise AppAdapterError(
            f"App {_ACTIVE_APP_CONFIG.slug if _ACTIVE_APP_CONFIG else 'unknown'!r} 未提供 legacy 页面契约"
        )
    return handler(sheet_name, row, case)


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
    """Compatibility wrapper; market-tab geometry belongs to the adapter."""

    return _active_adapter().tap_market_tab(events, sheet_name)


def tap_fund_tab(events: list[dict], tab_name: str) -> bool:
    """Compatibility wrapper; 看资金 tab geometry belongs to the adapter."""

    return _active_adapter().tap_fund_tab(events, tab_name)


def enter_market_home(events: list[dict], sheet_name: str) -> bool:
    """Compatibility wrapper; module entry belongs to the adapter."""

    return _active_adapter().enter_market_home(events, sheet_name)


def module_root_visible(sheet_name: str, elements: list[dict]) -> bool:
    return _active_adapter().is_module_root(sheet_name, elements)


def soft_reset_to_module_root(events: list[dict], sheet_name: str) -> bool:
    """Compatibility wrapper; reset policy belongs to the adapter."""

    return _active_adapter().soft_reset_to_module_root(events, sheet_name)


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
        "policy": "adapter_module_cold_start_page_group_reuse_row_execution",
        "adapter": _active_adapter().name,
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
    if kind == "type":
        return "向输入框输入 Agent 计划指定的值"
    if kind == "key":
        return f"按下按键“{target}”"
    if kind == "wait":
        return f"等待页面稳定（{target}）"
    if kind == "orientation":
        return f"调整屏幕方向为“{target}”"
    if kind == "observe":
        return "采集当前页面状态"
    if kind == "selector_rebind":
        return f"视觉定位后刷新 UI 树并重新绑定控件“{target}”：{detail or result}"
    if kind == "executor":
        return f"执行器报告异常：{detail or target or result}"
    if kind == "interrupt":
        return f"检测到运行时覆盖层“{detail or target or result}”"
    if kind == "retry":
        return f"对“{target}”进行一次受限重试"
    if kind == "assert":
        return f"校验页面状态“{target}”：{detail or result}"
    if kind == "navigate":
        return f"执行公共导航“{target}”：{detail or result}"
    if kind == "probe":
        return f"导航探测“{target}”：{detail or result}"
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
    judgment_status: str,
    judgment_reason: str,
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
    return append_judgment_reason(
        "\n".join(lines),
        judgment_status,
        judgment_reason,
    )


def executor_judgment_reason(
    status: str,
    *,
    setup_ok: bool,
    action_ok: bool,
    shot_ok: bool,
    action_mode: str,
    error_detail: str = "",
    blocked_reason: str = "",
    probe: bool = False,
    agent_plan: bool = False,
) -> str:
    """Create a grounded reason for the executor's preliminary verdict."""

    if status == "⛔阻塞":
        return blocked_reason or error_detail or "目标页、动作或独立证据门禁未完成"
    if probe or action_mode == "probe":
        return "目标页门禁和独立截图均已完成；本次未执行业务动作，仅证明导航可达"
    if agent_plan:
        return (
            "当前 Agent 已将本行 Excel 内容解析为结构化动作并完成设备执行，"
            "已采集执行后页面和独立截图；最终通过/不通过必须由当前 Agent 依据本行截图、"
            "页面观察、实际动作和预期结果逐行复核，当前不自动判定为通过"
        )
    if status == "🟡待验证" and action_mode == "observe":
        return "该行是显式观察步骤，已采集执行后页面事实和独立截图，但仍需按预期条件完成语义确认"
    if status == "⚠️部分通过":
        return "已完成目标页校验、业务动作和独立截图，但缺少外部基准或所需交易时段，无法完成全部预期核对"
    if status == "✅通过":
        return "目标页门禁通过，动作执行完成且独立截图已生成；本轮执行门禁未发现失败，预期结果仍以逐行复核结论为准"
    return (
        f"执行条件：目标页={setup_ok}，动作={action_ok}，独立截图={shot_ok}；"
        f"{error_detail or '未形成可判定的终态'}"
    )


def setup_sheet(
    events: list[dict],
    sheet_name: str,
    row: int,
    case: dict,
    session: ModuleSession,
    page_group_id: str | None = None,
    page_group_key: str | None = None,
) -> bool:
    handler = getattr(_active_adapter(), "legacy_setup_sheet", None)
    if not callable(handler):
        raise AppAdapterError(
            f"App {_ACTIVE_APP_CONFIG.slug if _ACTIVE_APP_CONFIG else 'unknown'!r} 未提供 legacy setup"
        )
    return bool(handler(
        events,
        sheet_name,
        row,
        case,
        session,
        page_group_id=page_group_id,
        page_group_key=page_group_key,
    ))


def first_list_item(events: list[dict]) -> bool:
    return tap_xy(events, 540, 1000, "第一条列表记录")


def _page_value_matches(elements: list[dict], value: str, *, resource_id: bool = False) -> bool:
    expected = str(value).strip()
    if not expected:
        return False
    if resource_id:
        return any(
            expected == str(element.get("id") or "")
            or expected == str(element.get("id") or "").split("/")[-1]
            for element in elements
        )
    return any(
        expected == str(element.get("text") or "").strip()
        or expected == str(element.get("desc") or "").strip()
        or expected in str(element.get("text") or "")
        or expected in str(element.get("desc") or "")
        for element in elements
    )


def agent_target_match(target_page: dict, elements: list[dict]) -> tuple[bool, str]:
    """Evaluate only the observable, low-level page contract in an Agent plan."""

    missing: list[str] = []
    present_forbidden: list[str] = []
    for value in target_page.get("all_text") or []:
        if not _page_value_matches(elements, value):
            missing.append(f"文字:{value}")
    any_text = target_page.get("any_text") or []
    if any_text and not any(_page_value_matches(elements, value) for value in any_text):
        missing.append("任一文字:" + "/".join(str(value) for value in any_text))
    for value in target_page.get("all_ids") or []:
        if not _page_value_matches(elements, value, resource_id=True):
            missing.append(f"id:{value}")
    any_ids = target_page.get("any_ids") or []
    if any_ids and not any(_page_value_matches(elements, value, resource_id=True) for value in any_ids):
        missing.append("任一id:" + "/".join(str(value) for value in any_ids))
    for value in target_page.get("not_text") or []:
        if _page_value_matches(elements, value):
            present_forbidden.append(f"文字:{value}")
    for value in target_page.get("not_ids") or []:
        if _page_value_matches(elements, value, resource_id=True):
            present_forbidden.append(f"id:{value}")

    for value in target_page.get("selected_text") or []:
        if not selected_label(str(value)):
            missing.append(f"选中文字:{value}")
    if target_page.get("selected_ids"):
        try:
            root = ET.fromstring(droid.dump_xml())
            selected_ids = {
                (node.attrib.get("resource-id") or "").split("/")[-1]
                for node in root.iter("node")
                if node.attrib.get("selected") == "true"
                and node.attrib.get("resource-id")
            }
        except Exception:
            selected_ids = set()
        for value in target_page.get("selected_ids") or []:
            expected = str(value).split("/")[-1]
            if expected not in selected_ids:
                missing.append(f"选中id:{value}")

    orientation = str(target_page.get("orientation") or "").casefold()
    if orientation:
        expected_landscape = orientation == "landscape"
        if not orientation_matches(expected_landscape):
            missing.append(f"方向:{'横屏' if expected_landscape else '竖屏'}")

    if missing or present_forbidden:
        details = []
        if missing:
            details.append("缺少 " + "、".join(missing))
        if present_forbidden:
            details.append("不应出现 " + "、".join(present_forbidden))
        return False, "；".join(details)
    return True, "；".join(
        item
        for item in (
            f"目标页={target_page.get('description') or 'Agent 目标页'}",
            "UI 条件已满足",
        )
        if item
    )


def wait_for_agent_target(events: list[dict], target_page: dict, *, phase: str) -> bool:
    """Poll and record an Agent-authored target-page gate."""

    description = str(target_page.get("description") or "Agent 计划目标页")
    last_detail = "未获取到页面观察"
    for attempt in range(PAGE_READY_RETRIES):
        elements = screen_elements()
        ok, detail = agent_target_match(target_page, elements)
        last_detail = detail
        if ok:
            event(events, "assert", description, "success", f"{phase}：{detail}")
            return True
        if attempt < PAGE_READY_RETRIES - 1:
            wait_short(0.35)
    event(events, "assert", description, "failed", f"{phase}：{last_detail}")
    return False


def execute_agent_actions(
    events: list[dict],
    actions: list[dict],
    *,
    phase: str,
) -> tuple[bool, str, str]:
    """Execute only validated low-level actions authored by an Agent.

    This function intentionally has no Excel-text parser and no fallback
    branch. If the Agent did not provide an action, the caller receives a
    blocked result instead of an inferred tap or an observe-only pass.
    """

    if not actions:
        detail = f"Agent {phase}动作列表为空；执行器未从 Excel 原文推断动作"
        event(events, "executor", phase, "failed", detail)
        return False, detail, "agent"

    observed = False
    for index, spec in enumerate(actions, start=1):
        action_type = str(spec.get("type") or "")
        target = str(
            spec.get("target")
            or spec.get("text")
            or spec.get("id")
            or f"{action_type}#{index}"
        )
        if action_type == "tap_text":
            ok = tap_text(events, str(spec["text"]))
        elif action_type == "tap_id":
            ok = tap_id(events, str(spec["id"]))
        elif action_type == "tap_xy":
            ok = tap_xy(events, int(spec["x"]), int(spec["y"]), target)
        elif action_type == "tap_bbox":
            x1, y1, x2, y2 = (int(spec[key]) for key in ("x1", "y1", "x2", "y2"))
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            ok = tap_xy(events, cx, cy, target or f"bbox[{x1},{y1},{x2},{y2}]")
        elif action_type == "type_text":
            ok = type_text(events, str(spec["text"]))
        elif action_type == "key":
            ok = key_name(events, str(spec["key"]))
        elif action_type == "swipe":
            ok = swipe_duration(
                events,
                int(spec["x1"]),
                int(spec["y1"]),
                int(spec["x2"]),
                int(spec["y2"]),
                int(spec.get("duration_ms", 350)),
                target,
            )
        elif action_type == "rotate":
            ok = rotate(events, str(spec["orientation"]).casefold() == "landscape")
        elif action_type == "wait":
            ok = wait_action(events, float(spec["seconds"]))
        elif action_type == "observe":
            observed = True
            event(events, "observe", target, "success", f"Agent {phase}明确要求采集页面状态")
            ok = True
        elif action_type == "assert_text":
            elements = screen_elements()
            ok = _page_value_matches(elements, str(spec["text"]))
            event(events, "assert", target, "success" if ok else "failed", "页面文字断言")
        elif action_type == "assert_id":
            elements = screen_elements()
            ok = _page_value_matches(elements, str(spec["id"]), resource_id=True)
            event(events, "assert", target, "success" if ok else "failed", "页面 resource-id 断言")
        else:
            # load_action_plan validates this before device actions. Keep a
            # defensive branch so a direct caller still fails closed if a
            # dict is mutated after validation.
            ok = False
            event(events, "executor", target, "failed", f"未允许的 Agent 动作类型: {action_type!r}")

        if not ok:
            control_not_found = _control_lookup_failed(action_type, events)
            detail = f"Agent {phase}动作第{index}步未完成：{action_type}（{target}）"
            if control_not_found:
                detail += "；控件或断言目标未在当前 UI 树找到"
            event(events, "executor", phase, "failed", detail)
            return False, detail, "observe" if observed else "agent"

        after = spec.get("after")
        if after and not wait_for_agent_target(events, after, phase=f"{phase}第{index}步后"):
            detail = f"Agent {phase}动作第{index}步后页面断言失败"
            event(events, "executor", phase, "failed", detail)
            return False, detail, "observe" if observed else "agent"

    return True, "", "observe" if observed else "agent"


def setup_agent_case(
    events: list[dict],
    sheet_name: str,
    row: int,
    case: dict,
    session: ModuleSession,
    case_plan: dict,
    page_group_id: str | None = None,
    page_group_key: str | None = None,
) -> bool:
    """Establish one Agent-authored navigation context and target page."""

    del case
    group_id = page_group_id or str(case_plan.get("page_group_id") or f"{sheet_name}-ungrouped")
    group_key = page_group_key or str(case_plan.get("page_group_key") or sheet_name)
    same_group = (
        session.active_sheet == sheet_name
        and session.active_page_group_id == group_id
        and session.active_page_group_key == group_key
    )

    if same_group:
        if not rotate(events, False):
            return False
        session.page_group_reuse_count += 1
        event(events, "group_reuse", group_id, "success", "Agent 页面组复用；仍重新校验目标页")
    else:
        if not ensure_module_state(events, sheet_name, session):
            return False
        session.active_page_group_id = group_id
        session.active_page_group_key = group_key
        event(events, "page_group", group_id, "success", f"进入 Agent 导航分组：{group_key}")

    target_page = case_plan["target_page"]
    if wait_for_agent_target(events, target_page, phase="当前状态"):
        return True

    navigation = case_plan.get("navigation") or []
    if not navigation:
        event(events, "replan", group_id, "failed", "当前页不匹配且 Agent 未提供 navigation")
        return False
    ok, detail, _ = execute_agent_actions(events, navigation, phase="公共导航")
    if ok and wait_for_agent_target(events, target_page, phase="公共导航后"):
        return True

    # A failed route gets one bounded recovery.  This is still the exact
    # Agent plan (or its explicitly authored recovery_navigation), never a
    # new natural-language guess made by the deterministic runner.
    session.runtime_replan_count += 1
    event(events, "replan", group_id, "attempt", "Agent 目标页门禁失败，执行一次受限恢复导航")
    if not ensure_module_state(events, sheet_name, session):
        event(events, "replan", group_id, "failed", "恢复导航前模块复位失败")
        return False
    recovery = case_plan.get("recovery_navigation") or navigation
    recovery_ok, recovery_detail, _ = execute_agent_actions(events, recovery, phase="恢复导航")
    if recovery_ok and wait_for_agent_target(events, target_page, phase="恢复导航后"):
        event(events, "replan", group_id, "success", "恢复导航后 Agent 目标页校验通过")
        return True
    event(
        events,
        "replan",
        group_id,
        "failed",
        recovery_detail or "恢复导航后 Agent 目标页仍未通过",
    )
    return False


def execute_action(events: list[dict], sheet_name: str, row: int, case: dict) -> tuple[bool, str, str]:
    handler = getattr(_active_adapter(), "legacy_execute_action", None)
    if not callable(handler):
        raise AppAdapterError(
            f"App {_ACTIVE_APP_CONFIG.slug if _ACTIVE_APP_CONFIG else 'unknown'!r} 未提供 legacy action executor"
        )
    return handler(events, sheet_name, row, case)


def take_shot(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    rc1, _, _ = droid.adb("-s", DEVICE, "shell", "screencap", "-p", "/sdcard/_three_sheets.png")
    rc2, _, _ = droid.adb("-s", DEVICE, "pull", "/sdcard/_three_sheets.png", str(path))
    return rc1 == 0 and rc2 == 0 and path.exists() and path.stat().st_size > 0


def capture_retest_observation(
    output: Path,
    sheet_name: str,
    row: int,
    phase: str,
    turn: int,
) -> dict[str, Any]:
    """Capture the fresh screenshot/UI tree supplied to the retest Agent."""

    safe_sheet = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]+", "_", str(sheet_name)).strip("_") or "sheet"
    root = output / "retest-evidence" / f"{safe_sheet}_row_{int(row):03d}"
    root.mkdir(parents=True, exist_ok=True)
    shot_path = root / f"turn_{int(turn):03d}_{phase}.png"
    tree_path = root / f"turn_{int(turn):03d}_{phase}.xml"
    elements: list[dict] = []
    errors: list[str] = []
    try:
        xml = droid.dump_xml(str(tree_path))
        elements = droid.parse(xml)
    except Exception as exc:
        errors.append(f"UI树采集失败：{exc}")
    shot_ok = take_shot(shot_path)
    if not shot_ok:
        errors.append("截图未生成或为空")
    observation: dict[str, Any] = {
        "phase": phase,
        "turn": int(turn),
        "screenshot": str(shot_path.resolve()).replace("\\", "/") if shot_ok else "",
        "ui_tree": str(tree_path.resolve()).replace("\\", "/") if tree_path.is_file() else "",
        "page_observation": summarize(elements),
        "screenshot_ok": shot_ok,
        "ui_tree_ok": tree_path.is_file(),
        "evidence": [str(shot_path.resolve()).replace("\\", "/")] if shot_ok else [],
    }
    try:
        observation["current_activity"] = droid.current()
    except Exception as exc:
        errors.append(f"前台页面读取失败：{exc}")
    if errors:
        observation["observation_error"] = "；".join(errors)
    return observation


def build_manifest(workbook: xlrd.book.Book, sheet_names: Iterable[str] | None = None) -> dict:
    selected_sheets = tuple(sheet_names or workbook.sheet_names())
    selected = []
    for sheet_name in selected_sheets:
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
        "source_file": str(SOURCE.resolve()) if SOURCE else "",
        "source_sheets": list(selected_sheets),
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

    queue_agent_binding = document.get("agent_binding")
    if not isinstance(queue_agent_binding, Mapping):
        queue_manifest = document.get("execution_manifest")
        queue_agent_binding = (
            queue_manifest.get("agent_binding")
            if isinstance(queue_manifest, Mapping)
            else None
        )
    queue_agent_binding = (
        dict(queue_agent_binding) if isinstance(queue_agent_binding, Mapping) else {}
    )
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
                "initial_bucket": item.get("initial_bucket", ""),
                "initial_actual": item.get("initial_actual", ""),
                "initial_reason": item.get("reason", ""),
                "initial_record": (
                    dict(item.get("case"))
                    if isinstance(item.get("case"), Mapping)
                    else {}
                ),
                "agent_binding": dict(queue_agent_binding),
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


def _apply_agent_groups(
    manifest: dict,
    execution_items: list[tuple[dict, dict, dict]],
    action_case_plans: dict[tuple[str, int, str], dict],
) -> None:
    """Replace context-only group labels with the selected Agent's groups."""

    selected = manifest.get("selected_cases") or []
    by_key = {
        (str(case.get("sheet") or ""), int(case.get("row"))): action_case_plans[
            (str(case.get("case_id") or ""), int(case.get("row")), str(case.get("sheet") or ""))
        ]
        for _, _, case in execution_items
        if (str(case.get("case_id") or ""), int(case.get("row")), str(case.get("sheet") or ""))
        in action_case_plans
    }
    for item in selected:
        if not isinstance(item, dict):
            continue
        plan_item = by_key.get((str(item.get("sheet") or ""), int(item.get("row"))))
        if plan_item is not None:
            item["page_group_id"] = plan_item["page_group_id"]
            item["page_group_key"] = plan_item["page_group_key"]

    groups: list[dict] = []
    for _, _, case in execution_items:
        plan_item = action_case_plans.get(
            (str(case.get("case_id") or ""), int(case.get("row")), str(case.get("sheet") or ""))
        )
        if plan_item is None:
            continue
        group_id = str(plan_item["page_group_id"])
        group_key = str(plan_item["page_group_key"])
        if not groups or groups[-1]["page_group_id"] != group_id or groups[-1]["page_group_key"] != group_key:
            groups.append(
                {
                    "page_group_id": group_id,
                    "page_group_key": group_key,
                    "case_ids": [],
                    "row_range": [int(case["row"]), int(case["row"])],
                }
            )
        groups[-1]["case_ids"].append(case["case_id"])
        groups[-1]["row_range"][1] = int(case["row"])
    if groups:
        manifest["page_groups"] = groups


def _blocked_retest_command(
    *,
    app_slug: str,
    source: Path,
    profile: Path | None,
    device: str,
    output: Path,
    queue_path: Path,
    action_plan: str | None,
    legacy_deterministic: bool,
    resume: bool,
    llm_retest: bool = False,
) -> list[str]:
    """Build the isolated second-pass command."""

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--app",
        app_slug,
        "--source",
        str(source),
        "--device",
        device,
        "--output",
        str(output),
        "--retest-queue",
        str(queue_path),
        "--no-auto-retest-blocked",
    ]
    if llm_retest:
        command.append("--llm-retest")
    if profile is not None:
        command.extend(["--profile", str(profile)])
    if action_plan and not llm_retest:
        command.extend(["--action-plan", str(Path(action_plan).expanduser().resolve())])
    elif legacy_deterministic and not llm_retest:
        command.append("--legacy-deterministic")
    if resume:
        command.append("--resume")
    return command


def _run_blocked_retest_once(
    execution_document: Mapping[str, Any],
    *,
    output: Path,
    app_slug: str,
    source: Path,
    profile: Path | None,
    device: str,
    action_plan: str | None,
    legacy_deterministic: bool,
    llm_retest: bool = False,
) -> tuple[dict[str, Any], Path | None, dict[str, Any], int]:
    """Retest first-pass blocked rows once and merge both attempts.

    The child disables recursive blocked retesting so the deferred second pass
    remains bounded to one round.  In ``llm_retest`` mode the old action plan
    is retained only for binding/audit; the child asks a fresh retester session
    to re-understand the original Excel row and choose each next action.
    """

    queue_document = plan_retests(
        execution_document,
        scope="all",
        status_buckets=("blocked",),
    )
    queue_path = write_retest_json(queue_document, output / "blocked_retest_queue.json")
    retest_output = output / "blocked-retest"
    planned = len(queue_document.get("cases") or [])
    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "strategy": "deferred_second_pass",
        "status_buckets": ["blocked"],
        "max_retest_rounds": 1,
        "mode": "llm_stepwise_fresh_session" if llm_retest else "validated_plan_replay",
        "planned": planned,
        "completed": 0,
        "queue": str(queue_path),
        "output": str(retest_output),
    }
    summary_path = output / "blocked_retest_summary.json"
    if planned == 0:
        summary["status"] = "not_needed"
        write_retest_json(summary, summary_path)
        return dict(execution_document), None, summary, 0

    resume = (retest_output / "execution_manifest.json").is_file()
    command = _blocked_retest_command(
        app_slug=app_slug,
        source=source,
        profile=profile,
        device=device,
        output=retest_output,
        queue_path=queue_path,
        action_plan=action_plan,
        legacy_deterministic=legacy_deterministic,
        llm_retest=llm_retest,
        resume=resume,
    )
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            check=False,
        )
    except OSError as exc:
        summary["status"] = "failed"
        summary["error"] = f"复测执行器无法启动: {exc}"
        write_retest_json(summary, summary_path)
        return dict(execution_document), None, summary, 2
    summary["child_exit_code"] = completed.returncode
    if completed.returncode != 0:
        summary["status"] = "paused" if completed.returncode == 130 else "failed"
        write_retest_json(summary, summary_path)
        return dict(execution_document), None, summary, int(completed.returncode)

    retest_execution_path = retest_output / "retest_execution.json"
    if not retest_execution_path.is_file():
        summary["status"] = "failed"
        summary["error"] = f"复测执行完成但缺少结果文件: {retest_execution_path}"
        write_retest_json(summary, summary_path)
        return dict(execution_document), None, summary, 2
    try:
        retest_document = json.loads(retest_execution_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        summary["status"] = "failed"
        summary["error"] = f"复测结果无法读取: {exc}"
        write_retest_json(summary, summary_path)
        return dict(execution_document), None, summary, 2

    try:
        merged = merge_retests(
            execution_document,
            retest_document,
            plan=queue_document,
            require_all=True,
            require_evidence=True,
        )
    except (RetestError, OSError, TypeError) as exc:
        summary["status"] = "failed"
        summary["error"] = f"复测结果合并失败: {exc}"
        write_retest_json(summary, summary_path)
        return dict(execution_document), None, summary, 2
    merged_path = write_retest_json(
        merged,
        output / "execution_records.retested.json",
    )
    summary.update(
        {
            "status": "complete",
            "completed": len((retest_document or {}).get("cases") or []),
            "merged_result": str(merged_path),
        }
    )
    write_retest_json(summary, summary_path)
    return merged, merged_path, summary, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="模块级规划、Excel 行级执行的 Android 用例执行器")
    parser.add_argument("--app", help="App slug；未指定时从 --profile 推断，否则默认 guotou")
    parser.add_argument("--source", help=".xls/.xlsx 用例文件；未指定时使用 App 配置的 default_source")
    parser.add_argument("--profile", help="覆盖 App 配置中的 profile.yaml")
    parser.add_argument("--device", help="ADB 设备序列号；默认使用配置或当前默认设备")
    parser.add_argument("--output", help="本轮运行目录；未指定时使用 App 配置或 output/<app>-run")
    parser.add_argument("--sheet", action="append", dest="sheets", help="指定 Sheet，可重复；默认执行预设 Sheet")
    parser.add_argument("--resume", action="store_true", help="从 execution_records.jsonl 继续未完成用例")
    parser.add_argument(
        "--retest-queue",
        help="只执行 retest_results.py plan 生成的单用例复测队列；不传时保持全量 Sheet 执行",
    )
    parser.add_argument(
        "--auto-retest-blocked",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "首轮完成后在当前 output/blocked-retest 下自动复测阻塞用例一次；"
            "默认开启，最多复测一轮"
        ),
    )
    parser.add_argument(
        "--llm-retest",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "阻塞复测时为每条用例创建新的 LLM retester 会话，重新读取原始 Excel、"
            "App 画像和实时截图/UI树并逐步决定动作；不使用旧 action plan 作为动作来源；"
            "显式 --retest-queue 时可省略旧 action plan"
        ),
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="导航探测模式：只执行 setup/目标页门禁/截图，不执行 Excel 业务动作",
    )
    parser.add_argument(
        "--probe-queue",
        help="导航探测队列 JSON；必须与 --probe 一起使用，格式同 retest queue",
    )
    planner_group = parser.add_mutually_exclusive_group()
    planner_group.add_argument(
        "--action-plan",
        help=(
            "当前 Agent 生成的 agent_action_plan.json；执行器只消费其中的结构化 "
            "navigation/actions，不再从 Excel 原文推断动作"
        ),
    )
    planner_group.add_argument(
        "--legacy-deterministic",
        action="store_true",
        help="显式兼容旧版固定规则执行器；仅用于迁移/诊断，不能代表 Agent 驱动执行",
    )
    args = parser.parse_args(argv)

    if (
        not args.action_plan
        and not args.legacy_deterministic
        and not (args.llm_retest and args.retest_queue and not args.probe)
    ):
        raise ValueError(
            "必须提供 --action-plan。固定动作解析已不再是默认路径；"
            "如确需兼容旧行为，请显式使用 --legacy-deterministic；"
            "LLM 复测模式可对显式 --retest-queue 省略旧 action plan"
        )

    app_slug = args.app or infer_app_slug_from_profile(args.profile) or DEFAULT_APP_SLUG
    app_config = load_app_config(PROJECT_ROOT, app_slug, profile_path=args.profile)
    source_value = args.source or app_config.default_source
    if source_value is None:
        raise ValueError(
            f"App {app_config.slug!r} 未配置 default_source，请通过 --source 指定用例文件"
        )
    source = Path(source_value).expanduser().resolve()
    if args.output:
        output = Path(args.output).expanduser().resolve()
    elif app_config.default_output:
        output = app_config.default_output
    else:
        output = (PROJECT_ROOT / "output" / f"{app_config.slug}-run").resolve()

    global DEVICE, SOURCE, APP_PROFILE, OUTPUT, _ACTIVE_ADAPTER, _ACTIVE_APP_CONFIG
    DEVICE = str(args.device or app_config.document.get("device") or DEFAULT_DEVICE).strip()
    if not DEVICE:
        DEVICE = DEFAULT_DEVICE
    SOURCE = source
    APP_PROFILE = app_config.profile_path
    OUTPUT = output
    global SHOTS
    SHOTS = OUTPUT / "shots"
    _ACTIVE_APP_CONFIG = app_config
    _ACTIVE_ADAPTER = load_app_adapter(app_config, _RUNNER_ADAPTER_RUNTIME)
    if args.legacy_deterministic and not _ACTIVE_ADAPTER.supports_legacy_deterministic:
        raise ValueError(
            f"App {app_config.slug!r} 没有 legacy-deterministic adapter；"
            "请使用 Agent --action-plan 执行通用路径"
        )

    if args.probe and args.retest_queue:
        raise ValueError("--probe 不能与 --retest-queue 同时使用")
    if args.probe and not args.probe_queue:
        raise ValueError("--probe 必须提供 --probe-queue")
    if args.probe_queue and not args.probe:
        raise ValueError("--probe-queue 必须与 --probe 一起使用")
    queue_path = args.probe_queue if args.probe else args.retest_queue
    queue_entries = _load_retest_queue(Path(queue_path).expanduser().resolve()) if queue_path else []
    stepwise_retest = bool(args.llm_retest and queue_entries and not args.probe)
    llm_retest_requested = bool(
        args.llm_retest
        and not args.probe
        and (queue_entries or args.auto_retest_blocked)
    )
    if llm_retest_requested and not session_factory_available():
        raise ValueError(
            "已请求 --llm-retest，但未配置 Agent session factory；"
            "请由桌面 Agent 宿主注册 factory 或设置 SIXGILL_AGENT_SESSION_FACTORY=module:function"
        )
    if (
        llm_retest_requested
        and args.auto_retest_blocked
        and not queue_entries
        and not args.probe
        and not str(os.environ.get(FACTORY_ENV) or "").strip()
    ):
        raise ValueError(
            "自动 LLM 阻塞复测会在独立子进程中运行；请设置 "
            f"{FACTORY_ENV}=module:function，进程内注册的 factory 不能跨子进程继承"
        )
    queue_sheets = tuple(dict.fromkeys(entry["sheet"] for entry in queue_entries))
    if queue_entries and args.sheets:
        unexpected_sheets = sorted(set(queue_sheets).difference(args.sheets))
        if unexpected_sheets:
            raise ValueError(
                "--sheet 未覆盖复测队列中的 Sheet: " + ", ".join(unexpected_sheets)
            )
    # An explicit --sheet or queue narrows the scope.  With neither, read
    # every Sheet present in the source workbook; a hard-coded six-sheet
    # default silently dropped the workbook's separate ``个股详情`` module.
    selected_sheets = tuple(args.sheets or queue_sheets)
    output.mkdir(parents=True, exist_ok=True)
    shots = output / "shots"
    shots.mkdir(parents=True, exist_ok=True)

    # This is only the deterministic Excel/profile context.  The executable
    # plan must come from the selected Agent and is validated before any
    # device action.
    plan = build_module_plan(source, selected_sheets, profile_path=APP_PROFILE)
    plan["app"] = app_config.manifest_context(adapter_name=_ACTIVE_ADAPTER.name)
    manifest = plan["execution_manifest"]
    manifest["app"] = app_config.manifest_context(adapter_name=_ACTIVE_ADAPTER.name)
    retest_items = _retest_execution_items(plan, queue_entries) if queue_entries else []
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

    action_plan_document: dict | None = None
    action_case_plans: dict[tuple[str, int, str], dict] = {}
    if args.action_plan and stepwise_retest:
        # In stepwise mode an old plan is optional audit input.  Read only its
        # planner identity; do not validate or expose its actions as a source
        # of device operations.
        reference_path = Path(args.action_plan).expanduser().resolve()
        try:
            reference_document = json.loads(reference_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"LLM 复测 action plan 审计文件不可读取: {reference_path}") from exc
        if not isinstance(reference_document, dict):
            raise ValueError("LLM 复测 action plan 审计文件必须是 JSON 对象")
        planner = reference_document.get("planner")
        planner = dict(planner) if isinstance(planner, Mapping) else {}
        action_plan_document = {"planner": planner} if planner else None
        plan["retest_action_plan_file"] = str(reference_path)
        manifest["retest_action_plan_file"] = str(reference_path)
        manifest["retest_action_plan_sha256"] = sha256_file(reference_path)
    elif args.action_plan:
        action_plan_document, action_case_plans = load_action_plan(
            args.action_plan,
            cases=[case for _, _, case in execution_items],
            source_path=source,
            profile_path=APP_PROFILE,
        )
        planner = action_plan_document.get("planner") or {}
        plan["planning_mode"] = "agent_structured_action_plan"
        plan["planner_backend"] = str(planner.get("agent") or "agent")
        plan["action_plan_file"] = str(Path(args.action_plan).expanduser().resolve())
        plan["action_plan"] = action_plan_document
        manifest["planning_mode"] = "agent_structured_action_plan"
        manifest["agent_plan_required"] = True
        manifest["llm_plan_required"] = True
        manifest["agent_plan_file"] = str(Path(args.action_plan).expanduser().resolve())
        manifest["agent_plan_sha256"] = sha256_file(args.action_plan)
        manifest["planner_agent"] = str(planner.get("agent") or "agent")
        manifest["planner_model"] = str(planner.get("model") or "")
        manifest["planner_prompt_version"] = str(planner.get("prompt_version") or "")
        manifest["app_profile_file"] = str(APP_PROFILE) if APP_PROFILE else ""
        manifest["app_profile_sha256"] = sha256_file(APP_PROFILE) if APP_PROFILE else ""
    else:
        # Keeping this mode available makes migration and diagnosis possible,
        # but its output is explicitly marked as legacy and is never the
        # default execution path.
        plan["planning_mode"] = "legacy_deterministic_explicit"
        plan["planner_backend"] = "legacy_rule_executor"
        manifest["planning_mode"] = "legacy_deterministic_explicit"
        manifest["agent_plan_required"] = False
        manifest["llm_plan_required"] = False

    if stepwise_retest:
        # The old plan may be copied for audit and used to inherit the planner
        # identity, but it is not an execution authority in this mode.
        plan["planning_mode"] = "llm_stepwise_retest"
        plan["planner_backend"] = "desktop_agent_retester"
        manifest["planning_mode"] = "llm_stepwise_retest"
        manifest["agent_plan_required"] = False
        manifest["llm_plan_required"] = False
        manifest["retest_action_plan_authority"] = "reference_only"
        manifest["retest_agent_role"] = "retester"
        manifest["app_profile_file"] = str(APP_PROFILE) if APP_PROFILE else ""
        manifest["app_profile_sha256"] = sha256_file(APP_PROFILE) if APP_PROFILE else ""

    # Persist the planner/retester/reviewer binding for audit and downstream
    # review.  A direct LLM retest may omit the old plan; in that case inherit
    # the first-pass binding carried by the queue, if present.
    binding_source: Mapping[str, Any] | None = action_plan_document
    if stepwise_retest and action_plan_document is None:
        queue_binding = next(
            (
                entry.get("agent_binding")
                for entry in queue_entries
                if isinstance(entry.get("agent_binding"), Mapping)
                and entry.get("agent_binding")
            ),
            None,
        )
        if isinstance(queue_binding, Mapping):
            planner_binding = queue_binding.get("planner")
            if isinstance(planner_binding, Mapping):
                binding_source = {"planner": dict(planner_binding)}
    agent_binding = resolve_agent_binding(binding_source)
    manifest["agent_binding"] = agent_binding
    manifest["blocked_retest"] = {
        "enabled": bool(args.auto_retest_blocked and not queue_entries and not args.probe),
        "strategy": "deferred_llm_stepwise" if args.llm_retest else "deferred_second_pass",
        "status_buckets": ["blocked"],
        "max_retest_rounds": 1,
        "queue_file": "blocked_retest_queue.json",
        "output_directory": "blocked-retest",
    }
    queue_context_by_key = {
        (str(entry["sheet"]), int(entry["row"])): entry for entry in queue_entries
    }

    if queue_entries:
        # Keep the source/module plan available for audit, but narrow the
        # execution manifest to the queue.  This makes journal completeness
        # and downstream review scope reflect the actual probe/retest run.
        manifest["mode"] = "sample" if args.probe else "retest"
        manifest["execution_scope"] = "navigation_probe" if args.probe else "single_case_retest"
        queue_key = "navigation_probe_queue" if args.probe else "retest_queue"
        manifest[queue_key] = str(Path(queue_path).expanduser().resolve())
        cases_key = "navigation_probe_cases" if args.probe else "retest_queue_cases"
        manifest[cases_key] = [
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
        if args.probe:
            manifest["llm_review_required"] = False
            manifest["navigation_probe_expected_count"] = len(retest_items)
        else:
            manifest["retest_expected_count"] = len(retest_items)
    if args.action_plan and not stepwise_retest:
        _apply_agent_groups(manifest, execution_items, action_case_plans)
    existing_manifest_path = output / "execution_manifest.json"
    existing_run_id = ""
    if args.resume and existing_manifest_path.is_file():
        try:
            existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            existing_mode = str(existing_manifest.get("mode") or "full")
            requested_mode = "sample" if args.probe else ("retest" if queue_entries else "full")
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
    if action_plan_document and Path(args.action_plan).resolve() != (output / "agent_action_plan.json").resolve():
        shutil.copy2(args.action_plan, output / "agent_action_plan.json")
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
        for module, page_group, case in execution_items:
                    sheet_name = module["sheet"]
                    page_group_id = str(page_group.get("page_group_id") or f"{sheet_name}-ungrouped")
                    page_group_key = str(page_group.get("page_group_key") or sheet_name)
                    row = int(case["row"])
                    action_case_plan = (
                        action_case_plans.get((str(case["case_id"]), row, sheet_name))
                        if args.action_plan
                        else None
                    )
                    if action_case_plan is not None and not stepwise_retest:
                        # The selected Agent owns the navigation grouping in
                        # structured-plan mode;
                        # the deterministic context grouping is only the
                        # fallback used by the explicit legacy path.
                        page_group_id = str(action_case_plan["page_group_id"])
                        page_group_key = str(action_case_plan["page_group_key"])
                    if journal.has_case(sheet_name, row):
                        continue

                    if queue_entries:
                        # A probe/retest queue is explicitly isolated.  Clear
                        # the page-group cache before every row so a row never
                        # inherits target-page state from another attempt.
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
                    elements: list[dict] = []
                    llm_retest_result: dict[str, Any] | None = None
                    try:
                        if stepwise_retest:
                            # Retesting starts from a safe module state only;
                            # the live retester decides how to reach the case's
                            # target page from the fresh screenshot/UI tree.
                            setup_ok = ensure_module_state(setup_events, sheet_name, session)
                        elif args.action_plan:
                            if action_case_plan is None:
                                raise AgentPlanError(
                                    f"执行用例缺少已校验的 Agent 计划: {sheet_name}!{row}"
                                )
                            setup_ok = setup_agent_case(
                                setup_events,
                                sheet_name,
                                row,
                                case,
                                session,
                                action_case_plan,
                                page_group_id=page_group_id,
                                page_group_key=page_group_key,
                            )
                        else:
                            setup_ok = setup_sheet(
                                setup_events,
                                sheet_name,
                                row,
                                case,
                                session,
                                page_group_id=page_group_id,
                                page_group_key=page_group_key,
                            )
                        if setup_ok and args.probe:
                            event(
                                action_events,
                                "probe",
                                f"{sheet_name}!{row}",
                                "success",
                                "导航探测模式：已完成目标页校验，未执行 Excel 业务动作",
                            )
                            action_ok = True
                            action_mode = "probe"
                        elif setup_ok:
                            if stepwise_retest:
                                retest_binding = agent_binding.get("retester") or {}
                                first_pass = queue_context_by_key.get((sheet_name, row), {})
                                profile_context = {
                                    "path": str(APP_PROFILE) if APP_PROFILE else "",
                                    "sha256": manifest.get("app_profile_sha256", ""),
                                    "hints": case.get("profile_hints") or [],
                                }

                                def _observe_retest(phase: str, turn: int) -> Mapping[str, Any]:
                                    return capture_retest_observation(
                                        output,
                                        sheet_name,
                                        row,
                                        phase,
                                        turn,
                                    )

                                def _execute_retest(action: Mapping[str, Any]) -> Mapping[str, Any]:
                                    operation_events: list[dict] = []
                                    ok, detail, mode = execute_agent_actions(
                                        operation_events,
                                        [dict(action)],
                                        phase="LLM复测动作",
                                    )
                                    return {
                                        "ok": ok,
                                        "detail": detail or ("底层动作已完成" if ok else "底层动作未完成"),
                                        "action_mode": mode,
                                        "events": operation_events,
                                    }

                                agent_session = create_agent_session(
                                    retest_binding,
                                    initial_context={
                                        "case_id": case["case_id"],
                                        "sheet": sheet_name,
                                        "row": row,
                                        "app": app_config.manifest_context(
                                            adapter_name=_active_adapter().name
                                        ),
                                    },
                                )
                                try:
                                    llm_retest_result = run_retest_case(
                                        case,
                                        session=agent_session,
                                        observe=_observe_retest,
                                        execute=_execute_retest,
                                        profile=profile_context,
                                        generic_knowledge=plan.get("generic_planning_knowledge") or {},
                                        first_pass=first_pass,
                                        limits=RetestLimits(),
                                    )
                                finally:
                                    agent_session.close()
                                action_events.extend(llm_retest_result.get("action_trace") or [])
                                action_ok = llm_retest_result.get("status") != "⛔阻塞"
                                action_mode = "llm_retest"
                                if llm_retest_result.get("status") == "⛔阻塞":
                                    error_detail = str(
                                        llm_retest_result.get("reason") or "LLM 复测未形成安全终态"
                                    )
                            elif args.action_plan:
                                action_ok, error_detail, action_mode = execute_agent_actions(
                                    action_events,
                                    action_case_plan.get("actions") or [],
                                    phase="本行操作",
                                )
                            else:
                                action_ok, error_detail, action_mode = execute_action(
                                    action_events, sheet_name, row, case
                                )
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
                    except Exception as exc:
                        error_detail = str(exc)
                        action_ok = False
                        event(action_events, "executor", f"{sheet_name}!{row}", "failed", error_detail)
                        page_observation = "未获取到可用的页面观察"

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
                    if stepwise_retest and llm_retest_result is not None:
                        if not setup_ok:
                            status = "⛔阻塞"
                            blocked_reason = error_detail or "模块初始状态建立失败"
                        elif not shot_ok:
                            status = "⛔阻塞"
                            blocked_reason = "独立证据截图未生成或为空"
                        else:
                            status = str(llm_retest_result.get("status") or "⛔阻塞")
                            blocked_reason = "" if status == "✅通过" else str(
                                llm_retest_result.get("reason") or "LLM 复测未给出具体判断理由"
                            )
                    elif not setup_ok or not action_ok:
                        status = "⛔阻塞"
                        blocked_reason = error_detail or "入口、动作或独立证据采集失败"
                    elif not shot_ok:
                        status = "⛔阻塞"
                        blocked_reason = "独立证据截图未生成或为空"
                    elif args.probe:
                        status = "✅通过"
                        blocked_reason = ""
                    elif args.action_plan:
                        # A successful low-level action is not a semantic
                        # verdict.  Leave this row for the Agent's screenshot /
                        # expected-result review; only deterministic gates can
                        # produce an immediate blocked status here.
                        status = "🟡待验证"
                        blocked_reason = ""
                    elif action_mode == "observe":
                        status = "🟡待验证"
                        blocked_reason = "该行是显式观察类步骤，需根据页面事实确认预期结果"
                    elif verification_only:
                        status = "⚠️部分通过"
                        blocked_reason = "已实际进入页面并执行操作，但本轮缺少外部基准或所需交易时段，无法完成全部预期核对"
                    else:
                        status = "✅通过"
                        blocked_reason = ""

                    if stepwise_retest and llm_retest_result is not None:
                        judgment_reason = str(
                            llm_retest_result.get("reason")
                            or blocked_reason
                            or "LLM 复测未给出具体判断理由"
                        )
                    else:
                        judgment_reason = executor_judgment_reason(
                            status,
                            setup_ok=setup_ok,
                            action_ok=action_ok,
                            shot_ok=shot_ok,
                            action_mode=action_mode,
                            error_detail=error_detail,
                            blocked_reason=blocked_reason,
                            probe=args.probe,
                            agent_plan=bool(args.action_plan),
                        )
                    if (
                        stepwise_retest
                        and llm_retest_result is not None
                        and shot_ok
                        and str(llm_retest_result.get("status")) == status
                    ):
                        observation = str(llm_retest_result.get("actual") or "")
                    else:
                        observation = build_actual(
                            action_events,
                            elements,
                            setup_ok=setup_ok,
                            action_ok=action_ok,
                            error_detail=error_detail,
                            judgment_status=status,
                            judgment_reason=judgment_reason,
                        )

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
                        "planning_mode": (
                            "llm_stepwise_retest"
                            if stepwise_retest
                            else "agent_structured_action_plan"
                            if args.action_plan
                            else "legacy_deterministic_explicit"
                        ),
                        "action_mode": action_mode,
                        "actual": observation,
                        "observation": observation,
                        "judgment_reason": judgment_reason,
                        "judgment": {
                            "status": status,
                            "reason": judgment_reason,
                            "source": "executor_preliminary" if args.action_plan else "executor",
                        },
                        "page_observation": page_observation,
                        "evidence": [str(evidence_path).replace("\\", "/")],
                        "action_trace": action_events,
                        "tested_at": now(),
                    }
                    if action_case_plan is not None and not stepwise_retest:
                        record["action_plan_case"] = {
                            "target_page": action_case_plan.get("target_page"),
                            "navigation": action_case_plan.get("navigation") or [],
                            "actions": action_case_plan.get("actions") or [],
                            "expected_observations": action_case_plan.get("expected_observations") or [],
                        }
                    if llm_retest_result is not None:
                        record["steps"] = llm_retest_result.get("steps") or []
                        record["llm_retest"] = llm_retest_result.get("llm_retest") or {}
                        record["retest_agent"] = llm_retest_result.get("llm_retest", {}).get("session") or {}
                    if args.probe:
                        record["probe"] = {
                            "navigation_only": True,
                            "business_action_executed": False,
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
                    run_label = "probe" if args.probe else ("retest" if queue_entries else "batch")
                    print(
                        f"{sheet_name}!{row}: {status} group={page_group_id} "
                        f"setup={setup_ok} action={action_ok} evidence={shot_ok}"
                        f" [{run_label}]",
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
            "policy": (
                "llm_stepwise_retest_fresh_session"
                if stepwise_retest
                else "agent_structured_actions_adapter_module_cold_start_page_group_reuse_row_execution"
                if args.action_plan
                else "legacy_deterministic_adapter_module_cold_start_page_group_reuse_row_execution"
            ),
            "adapter": _active_adapter().name,
            "agent_plan_required": bool(args.action_plan and not stepwise_retest),
            "llm_plan_required": bool(args.action_plan and not stepwise_retest),
            "stats": session_snapshot(session),
        }
    )
    final_path = journal.finalize(setup_trace=setup_trace)
    if args.probe:
        shutil.copy2(final_path, output / "navigation_probe_execution.json")
    elif queue_entries:
        # Keep the conventional retest_results.py input name alongside the
        # journal's canonical execution_records.json.
        shutil.copy2(final_path, output / "retest_execution.json")
    execution_document = json.loads(final_path.read_text(encoding="utf-8"))
    review_source_path = final_path
    blocked_retest_summary: dict[str, Any] | None = None
    if args.auto_retest_blocked and not queue_entries and not args.probe:
        (
            execution_document,
            retested_path,
            blocked_retest_summary,
            retest_exit_code,
        ) = _run_blocked_retest_once(
            execution_document,
            output=output,
            app_slug=app_config.slug,
            source=source,
            profile=APP_PROFILE,
            device=DEVICE,
            action_plan=args.action_plan,
            legacy_deterministic=bool(args.legacy_deterministic),
            llm_retest=bool(args.llm_retest),
        )
        if retest_exit_code != 0:
            rotate([], False)
            print(
                json.dumps(
                    {
                        "status": blocked_retest_summary.get("status", "failed"),
                        "phase": "blocked_retest",
                        "records": len(records),
                        "output": str(output),
                    },
                    ensure_ascii=False,
                )
            )
            return retest_exit_code
        if retested_path is not None:
            review_source_path = retested_path

    final_records = list(execution_document.get("cases") or [])
    exception_queue = build_exception_queue({"cases": final_records})
    (output / "exception_queue.json").write_text(
        json.dumps(exception_queue, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    profile_feedback = build_profile_feedback(
        {"cases": final_records},
        run_dir=output,
        app_slug=app_config.slug,
        app_version=str(plan.get("app_profile_context", {}).get("app_version") or "unknown"),
    )
    (output / "profile_feedback.json").write_text(
        json.dumps(profile_feedback, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    review_queue = build_review_queue(
        execution_document,
        run_dir=output,
        source_path=review_source_path,
    )
    (output / "llm_review_queue.json").write_text(
        json.dumps(review_queue, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rotate([], False)
    print(
        json.dumps(
            {
                "manifest": manifest["expected_count"],
                "records": len(final_records),
                "output": str(review_source_path),
                "blocked_retest": blocked_retest_summary,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
