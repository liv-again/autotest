from tools.page_execution import (
    PageContract,
    ensure_target_page,
    run_independent_entries,
    search_entry_two_way,
)


def test_two_way_entry_search_is_bounded_and_ordered():
    phases = []
    attempts = []
    viewport = {"name": "middle"}

    def try_candidate(label):
        attempts.append(label)
        return label == "底部入口" and viewport["name"] == "bottom"

    def restore_top():
        phases.append("restore_top")
        viewport["name"] = "top"
        return True

    def search_bottom():
        phases.append("search_bottom")
        viewport["name"] = "bottom"
        return True

    found = search_entry_two_way(
        ("顶部入口", "底部入口"),
        try_candidate,
        restore_top=restore_top,
        search_bottom=search_bottom,
        on_phase=phases.append,
    )

    assert found
    assert phases == ["current", "restore_top", "top", "search_bottom", "bottom"]
    assert attempts == ["顶部入口", "底部入口", "顶部入口", "底部入口", "顶部入口", "底部入口"]


def test_two_way_entry_search_does_not_scroll_after_current_hit():
    phases = []
    scrolled = []

    assert search_entry_two_way(
        ("入口",),
        lambda label: True,
        restore_top=lambda: scrolled.append("top") or True,
        search_bottom=lambda: scrolled.append("bottom") or True,
        on_phase=phases.append,
    )
    assert phases == ["current"]
    assert scrolled == []


def test_multiple_entries_restore_source_before_every_entry():
    calls = []

    assert run_independent_entries(
        ("A", "B"),
        ensure_source_page=lambda: calls.append("source") or True,
        execute_entry=lambda entry: calls.append(entry) or True,
        verify_entry=lambda entry: calls.append(f"verify:{entry}") or True,
    )
    assert calls == ["source", "A", "verify:A", "source", "B", "verify:B"]


def test_shared_page_gate_fails_closed_after_navigation_recheck():
    events = []
    states = iter(("wrong", "target"))

    def record_event(items, kind, target, result, detail=""):
        items.append((kind, target, result, detail))

    contract = PageContract(
        "目标页",
        predicate=lambda state: state == "target",
        navigate=lambda items: items.append(("navigate", "目标页")) or True,
    )

    assert ensure_target_page(
        events,
        contract,
        read_state=lambda: next(states),
        wait_for_page=lambda predicate, description: predicate("target"),
        record_event=record_event,
    )
    assert [event[0] for event in events] == ["assert", "navigate", "assert"]
