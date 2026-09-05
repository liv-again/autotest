import pytest

from tools.contracts.validate import validate
from tools.retest_results import RetestError, merge_retests, plan_retests


def _case(row, case_id, status, actual, *, module="行情", evidence=None):
    return {
        "sheet": "行情",
        "row": row,
        "case_id": case_id,
        "case_name": case_id,
        "module": module,
        "status": status,
        "actual": actual,
        "evidence": evidence or [f"assertions/{case_id}"],
    }


def test_plan_selects_non_passing_cases_one_by_one_and_excludes_skip():
    document = {
        "cases": [
            _case(2, "TC-FAIL", "❌失败", "页面显示错误提示"),
            _case(3, "TC-PARTIAL", "⚠️部分通过", "列表仅显示部分数据"),
            _case(4, "TC-PASS", "✅通过", "页面显示完整数据"),
            _case(5, "TC-SKIP", "☑不适用", "页面显示该功能不适用"),
        ]
    }

    plan = plan_retests(document, scope="module", scope_name="行情")

    assert plan["execution_mode"] == "single_case"
    assert [item["retest_id"] for item in plan["cases"]] == ["行情!2", "行情!3"]
    assert all(item["fresh_setup_required"] for item in plan["cases"])
    assert plan["cases"][0]["case"]["actual"] == "页面显示错误提示"


def test_merge_retest_replaces_visible_result_but_keeps_both_attempts():
    initial = {
        "schema_version": "2.0",
        "cases": [
            _case(2, "TC-001", "❌失败", "页面显示错误提示"),
            _case(3, "TC-002", "✅通过", "页面显示完整数据"),
        ],
    }
    plan = plan_retests(initial, scope="sheet", scope_name="行情")
    retest = {
        "schema_version": "2.0",
        "setup_trace": [{"kind": "setup", "action": "重新进入行情首页"}],
        "cases": [
            _case(2, "TC-001", "✅通过", "重新进入后页面显示完整数据", evidence=["retest/TC-001"]),
        ],
    }

    merged = merge_retests(initial, retest, plan=plan)

    final = merged["cases"][0]
    assert final["status"] == "✅通过"
    assert final["actual"] == "重新进入后页面显示完整数据"
    assert [item["phase"] for item in final["attempts"]] == ["batch", "single_case_retest"]
    assert final["attempts"][0]["status"] == "❌失败"
    assert final["attempts"][1]["status"] == "✅通过"
    assert final["retest"]["mode"] == "single_case"
    assert final["retest"]["setup_trace"][0]["action"] == "重新进入行情首页"
    assert merged["cases"][1]["status"] == "✅通过"
    assert validate(merged, "results") == []


def test_merge_requires_every_planned_case_and_validates_retry_quality():
    initial = {
        "cases": [
            _case(2, "TC-001", "❌失败", "页面显示错误提示"),
            _case(3, "TC-002", "⛔阻塞", "页面无响应"),
        ]
    }
    plan = plan_retests(initial)
    only_one_retry = {"cases": [_case(2, "TC-001", "✅通过", "页面显示完整数据", evidence=["retest/1"])]}

    with pytest.raises(RetestError, match="缺少"):
        merge_retests(initial, only_one_retry, plan=plan)

    missing_actual = {"cases": [_case(2, "TC-001", "✅通过", "", evidence=["retest/1"])]}
    with pytest.raises(RetestError, match="actual"):
        merge_retests(initial, missing_actual, plan=plan, require_all=False)
