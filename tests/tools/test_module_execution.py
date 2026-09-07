import json

from openpyxl import Workbook

from tools import _run_three_sheets as runner
from tools.exception_queue import build_exception_queue
from tools.execution_journal import ExecutionJournal, JournalError
from tools.module_planner import build_module_plan
from tools.execution_gate import validate_execution_contract


def _make_cases(path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "模块A"
    sheet.append(["1级目录", "用例名称", "优先级", "入口", "步骤名称", "前置条件", "操作描述", "参数", "预期结果"])
    sheet.append(["行情", "TC_打开模块", "P0", "首页", "进入", "竖屏", "点击模块", "", "显示模块首页"])
    sheet.append(["行情", "TC_查看列表", "P1", "模块A", "查看", "", "查看列表", "", "显示列表"])
    sheet2 = workbook.create_sheet("模块B")
    sheet2.append(["用例名称", "操作描述", "预期结果"])
    sheet2.append(["TC_刷新", "点击刷新", "数据更新"])
    workbook.save(path)


def test_module_plan_reads_once_and_keeps_row_order(tmp_path):
    source = tmp_path / "cases.xlsx"
    _make_cases(source)

    plan = build_module_plan(source, ["模块A", "模块B"])

    assert plan["planning_scope"] == "module"
    assert plan["execution_scope"] == "single_excel_row"
    assert plan["page_batching_allowed"] is False
    assert plan["expected_count"] == 3
    assert [case["row"] for case in plan["modules"][0]["cases"]] == [2, 3]
    assert plan["modules"][0]["cases"][0]["action"] == "点击模块"
    assert plan["execution_manifest"]["selected_cases"][2]["source_order"] == 3


def test_summarize_records_page_observation_without_action_echo():
    actual = runner.summarize([{"id": "query_page"}, {"text": "结果列表"}])

    assert actual == "当前页面观察：query_page、结果列表"
    assert "点击查询" not in actual


def test_row_target_contract_rejects_detail_and_accepts_a_share_list(monkeypatch):
    case = {
        "case_name": "TC_表头字段排序",
        "step_name": "横屏模式",
        "precondition": "横屏模式",
        "action": "进入其他--个股-上证A股横屏\n点击表头字段",
    }
    monkeypatch.setattr(runner, "orientation_matches", lambda landscape: landscape)

    contract = runner.target_page_contract("其他", 33, case)
    detail_page = [
        {"id": "page_queue_nav_bar"},
        {"id": "navi_animation_label"},
        {"id": "backButton"},
        {"text": "深纺织B"},
    ]
    a_share_list = [
        {"id": "hx_page_title_bar"},
        {"id": "table"},
        {"text": "返回"},
        {"text": "上证A股"},
    ]

    assert not contract.predicate(detail_page)
    assert contract.predicate(a_share_list)


def test_hk_home_uses_dedicated_surface_and_allows_scrolled_home():
    home = [
        {"id": "title_bar_middle"},
        {"id": "ganggu_page"},
        {"id": "titlebar_left_layout"},
        {"text": "港股"},
        {"text": "沪深京"},
        {"text": "其他"},
        {"text": "恒生指数"},
        {"text": "国企指数"},
        {"text": "沪、深港通"},
    ]
    scrolled_home = [item for item in home if item.get("text") not in {"恒生指数", "国企指数", "沪、深港通"}]
    incomplete = [item for item in home if item.get("text") != "港股"]

    assert runner.is_market_shell(home)
    assert runner.is_market_home(home, "港股")
    assert runner.is_market_home(scrolled_home, "港股")
    assert not runner.is_market_home(incomplete, "港股")


def test_enter_market_home_prefers_top_tab_over_same_text_title(monkeypatch):
    calls = []
    shell = [
        {"id": "title_bar_middle"},
        {"text": "沪深京"},
        {"text": "港股"},
        {"text": "其他"},
    ]
    monkeypatch.setattr(runner, "screen_elements", lambda: shell)
    monkeypatch.setattr(runner, "tap_xy", lambda events, x, y, target: calls.append((x, y)) or True)
    monkeypatch.setattr(runner, "tap_text", lambda *args: calls.append("text") or False)
    monkeypatch.setattr(runner, "wait_for_page", lambda predicate, description: True)

    assert runner.enter_market_home([], "港股")
    assert calls == [(756, 277)]


def test_target_page_guard_rechecks_after_navigation(monkeypatch):
    calls = []
    wrong_page = [{"id": "page_queue_nav_bar"}]
    target_page = [{"id": "table"}, {"text": "上证A股"}]
    monkeypatch.setattr(runner, "screen_elements", lambda: wrong_page)

    def navigate(events):
        calls.append("navigate")
        return True

    def wait_for_target(predicate, description):
        calls.append("recheck")
        return predicate(target_page)

    monkeypatch.setattr(runner, "wait_for_page", wait_for_target)
    contract = runner.PageContract(
        "上证A股列表页",
        lambda elements: runner.is_named_list_page(elements, ("上证A股",)),
        navigate,
    )

    events = []
    assert runner.ensure_target_page(events, contract)
    assert calls == ["navigate", "recheck"]
    assert [item["result"] for item in events if item["type"] == "assert"] == ["failed", "success"]


def test_detail_case_name_is_used_only_when_action_needs_detail():
    click_stock = {
        "case_name": "TC_列表跳转个股详情界面",
        "action": "进入看资金--概念\n点击列表中任意个股",
        "precondition": "横屏模式",
    }
    close_detail = {
        "case_name": "TC_个股详情界面返回概念板块列表",
        "action": "进入看资金--概念\n点击个股分时图右上角的X按钮",
        "precondition": "横屏模式",
    }

    assert not runner._target_is_detail(click_stock)
    assert runner._target_is_detail(close_detail)


def test_journal_persists_each_row_and_resumes(tmp_path):
    manifest = {
        "mode": "full",
        "expected_count": 1,
        "selected_cases": [{"sheet": "模块A", "row": 2, "case_id": "模块A-row-002"}],
    }
    journal = ExecutionJournal(tmp_path, manifest)
    journal.append(
        {
            "sheet": "模块A",
            "row": 2,
            "case_id": "模块A-row-002",
            "source_order": 1,
            "execution_order": 1,
            "status": "⛔阻塞",
            "blocked_reason": "控件未找到",
            "actual": "页面未出现目标控件",
            "action_trace": [{"type": "tap", "target": "目标", "result": "not_found"}],
            "evidence": ["shots/模块A-row-002.png"],
        }
    )

    state = json.loads((tmp_path / "execution_state.json").read_text(encoding="utf-8"))
    assert state["completed_count"] == 1
    assert state["last_completed"]["row"] == 2
    assert (tmp_path / "execution_records.jsonl").exists()
    assert journal.finalize().exists()
    assert ExecutionJournal(tmp_path, manifest, resume=True).has_case("模块A", 2)


def test_journal_rejects_duplicate_case(tmp_path):
    manifest = {
        "mode": "full",
        "expected_count": 1,
        "selected_cases": [{"sheet": "模块A", "row": 2, "case_id": "模块A-row-002"}],
    }
    journal = ExecutionJournal(tmp_path, manifest)
    record = {
        "sheet": "模块A",
        "row": 2,
        "case_id": "模块A-row-002",
        "source_order": 1,
        "execution_order": 1,
        "status": "⛔阻塞",
    }
    journal.append(record)
    try:
        journal.append(record)
    except JournalError as exc:
        assert "重复写入用例" in str(exc)
    else:
        raise AssertionError("duplicate case should be rejected")


def test_exception_queue_is_compact_and_excludes_pass_cases():
    queue = build_exception_queue(
        {
            "cases": [
                {"sheet": "模块A", "row": 2, "case_id": "A-2", "status": "⛔阻塞", "actual": "未找到", "evidence": ["a.png"]},
                {"sheet": "模块A", "row": 3, "case_id": "A-3", "status": "✅通过", "actual": "页面显示列表"},
            ]
        }
    )

    assert queue["review_scope"] == "module"
    assert queue["exception_count"] == 1
    assert queue["cases"][0]["exception_id"] == "A-2"


def test_row_scoped_gate_rejects_observe_only_pass_and_missing_order():
    manifest = {
        "mode": "full",
        "execution_scope": "single_excel_row",
        "expected_count": 1,
        "selected_cases": [{"sheet": "模块A", "row": 2, "case_id": "模块A-row-002"}],
    }
    record = {
        "sheet": "模块A",
        "row": 2,
        "case_id": "模块A-row-002",
        "status": "✅通过",
        "action_mode": "observe",
        "actual": "页面显示列表",
        "evidence": ["shots/a.png"],
        "action_trace": [{"type": "observe", "result": "success"}],
    }

    errors = validate_execution_contract([record], manifest)

    assert any("缺少 source_order" in error for error in errors)
    assert any("缺少 execution_order" in error for error in errors)
    assert any("observe-only" in error for error in errors)


def test_module_session_cold_starts_once_then_soft_resets(monkeypatch):
    calls = {"rotate": 0, "launch": 0, "soft_reset": 0}

    def fake_rotate(events, landscape):
        calls["rotate"] += 1
        return True

    def fake_launch(events):
        calls["launch"] += 1
        return True

    def fake_soft_reset(events, sheet_name):
        calls["soft_reset"] += 1
        return True

    monkeypatch.setattr(runner, "rotate", fake_rotate)
    monkeypatch.setattr(runner, "launch_market", fake_launch)
    monkeypatch.setattr(runner, "soft_reset_to_module_root", fake_soft_reset)

    session = runner.ModuleSession()
    assert runner.ensure_module_state([], "港股", session)
    assert runner.ensure_module_state([], "港股", session)

    assert calls == {"rotate": 2, "launch": 1, "soft_reset": 1}
    assert session.cold_start_count == 1
    assert session.soft_reset_count == 1
    assert session.recovery_restart_count == 0


def test_module_session_uses_one_bounded_cold_start_when_soft_reset_fails(monkeypatch):
    calls = {"launch": 0, "soft_reset": 0}

    monkeypatch.setattr(runner, "rotate", lambda events, landscape: True)

    def fake_launch(events):
        calls["launch"] += 1
        return True

    def fake_soft_reset(events, sheet_name):
        calls["soft_reset"] += 1
        return False

    monkeypatch.setattr(runner, "launch_market", fake_launch)
    monkeypatch.setattr(runner, "soft_reset_to_module_root", fake_soft_reset)

    session = runner.ModuleSession(active_sheet="港股")
    assert runner.ensure_module_state([], "港股", session)

    assert calls == {"launch": 1, "soft_reset": 1}
    assert session.cold_start_count == 1
    assert session.soft_reset_count == 0
    assert session.recovery_restart_count == 1
