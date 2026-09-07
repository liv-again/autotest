"""Reusable page contracts and bounded entry navigation.

This module contains app-agnostic runtime guards.  App-specific runners only
provide their UI-state reader, page predicates, and tap/swipe callbacks.  The
same components can therefore be reused by other Sheets, modules, or apps
without weakening the fail-closed execution rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class PageContract:
    """A deterministic pre-action page contract for one Excel row."""

    description: str
    predicate: Callable[[Any], bool]
    navigate: Callable[[list[dict]], bool]


def ensure_target_page(
    events: list[dict],
    contract: PageContract,
    *,
    read_state: Callable[[], Any],
    wait_for_page: Callable[[Callable[[Any], bool], str], bool],
    record_event: Callable[[list[dict], str, str, str, str], None],
) -> bool:
    """Check -> navigate once -> recheck; fail closed when the contract fails.

    ``navigate`` is public navigation only.  The caller must keep the row's
    business action in a separate phase, so a failed page transition can never
    accidentally execute that action on the wrong page.
    """

    if contract.predicate(read_state()):
        record_event(events, "assert", contract.description, "success", "复位后已处于目标页面")
        return True

    record_event(
        events,
        "assert",
        contract.description,
        "failed",
        "当前页面不是目标页面，开始执行公共导航",
    )
    if not contract.navigate(events):
        record_event(events, "navigate", contract.description, "failed", "目标页面导航动作失败")
        record_event(
            events,
            "assert",
            contract.description,
            "failed",
            "导航失败，阻塞本行且不执行操作",
        )
        return False

    if wait_for_page(contract.predicate, contract.description):
        record_event(events, "assert", contract.description, "success", "导航后目标页面复核通过")
        return True
    record_event(
        events,
        "assert",
        contract.description,
        "failed",
        "导航后目标页面复核未通过，阻塞本行且不执行操作",
    )
    return False


def search_entry_two_way(
    candidates: Iterable[str],
    try_candidate: Callable[[str], bool],
    *,
    restore_top: Callable[[], bool] | None = None,
    search_bottom: Callable[[], bool] | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> bool:
    """Search a page entry in a bounded, deterministic direction order.

    The order is intentionally stable:

    1. try the currently visible viewport;
    2. restore the page to its top and try again;
    3. scroll toward the lower section and try one final time.

    No loop is unbounded.  A caller may add a page predicate after a successful
    tap to verify that the entry opened the intended page.
    """

    labels = tuple(label for label in candidates if label)

    def attempt(phase: str) -> bool:
        if on_phase:
            on_phase(phase)
        return any(try_candidate(label) for label in labels)

    if attempt("current"):
        return True
    if restore_top and restore_top() and attempt("top"):
        return True
    if search_bottom and search_bottom() and attempt("bottom"):
        return True
    return False


def run_independent_entries(
    entries: Iterable[str],
    *,
    ensure_source_page: Callable[[], bool],
    execute_entry: Callable[[str], bool],
    verify_entry: Callable[[str], bool] | None = None,
) -> bool:
    """Execute several entries as independent sub-actions.

    A single Excel row may describe several home-page entries.  If opening the
    first entry changes the page, the next entry must not be tapped against the
    child page.  ``ensure_source_page`` is therefore called before every
    entry, and an optional verifier confirms each destination immediately.
    """

    for entry in entries:
        if not ensure_source_page():
            return False
        if not execute_entry(entry):
            return False
        if verify_entry and not verify_entry(entry):
            return False
    return True
