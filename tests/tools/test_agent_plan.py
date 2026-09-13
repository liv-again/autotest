import hashlib
import json

import pytest
from openpyxl import Workbook

from tools import _run_three_sheets as runner
from tools.agent_plan import (
    AgentPlanError,
    build_agent_context,
    load_action_plan,
    load_planner_model_defaults,
)


def _source(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "模块A"
    sheet.append(["用例名称", "操作描述", "预期结果"])
    sheet.append(["打开列表", "点击列表", "显示列表"])
    source = tmp_path / "cases.xlsx"
    workbook.save(source)
    return source


def _plan(source, *, actions=None):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "schema_version": "1.0",
        "plan_type": "agent_action_plan",
        "planner": {
            "agent": "Trae",
            "model": "test-model",
            "prompt_version": "agent-actions-v1",
        },
        "source": {"path": str(source), "sha256": digest},
        "cases": [
            {
                "case_id": "模块A-row-002",
                "sheet": "模块A",
                "row": 2,
                "page_group_id": "模块A-agent-group-001",
                "page_group_key": "模块A|列表",
                "navigation": [],
                "actions": actions if actions is not None else [{"type": "tap_text", "text": "列表"}],
                "target_page": {"description": "列表页", "all_text": ["列表"]},
                "expected_observations": ["显示列表"],
            }
        ],
    }


def test_context_marks_module_planner_as_context_only(tmp_path):
    source = _source(tmp_path)

    context = build_agent_context(source)

    assert context["planner_backend"] == "context_loader_only"
    assert context["planning_mode"] == "context_only"
    assert context["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "agent_action_plan" in context["agent_prompt"]
    technique_ids = {
        item["id"] for item in context["generic_planning_knowledge"]["techniques"]
    }
    assert technique_ids == {
        "landscape_list_title_visibility",
        "scroll_effect_by_first_stock",
        "target_element_search_after_page_confirm",
        "list_sort_by_first_two_rows",
    }
    assert "generic_planning_knowledge" in context["agent_prompt"]
    assert context["planner_model_defaults"]["model"] == "gpt-5.6-luna"
    assert context["planner_model_defaults"]["exact_wire_id_required"] is True
    assert "gpt-5.6-luna" in context["agent_prompt"]


def test_planner_model_environment_override(tmp_path, monkeypatch):
    config = tmp_path / "agent_model_config.yaml"
    config.write_text(
        "schema_version: '1.0'\n"
        "planner:\n"
        "  model: configured-model\n"
        "  model_env: PLANNER_MODEL_OVERRIDE\n",
        encoding="utf-8",
    )

    defaults = load_planner_model_defaults(
        config_path=config,
        environment={"PLANNER_MODEL_OVERRIDE": "gpt-5.6-luna"},
    )

    assert defaults["model"] == "gpt-5.6-luna"
    assert defaults["source"] == "environment:PLANNER_MODEL_OVERRIDE"


def test_action_plan_requires_exact_source_and_low_level_actions(tmp_path):
    source = _source(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(source), ensure_ascii=False), encoding="utf-8")
    context = build_agent_context(source)
    cases = [case for module in context["modules"] for case in module["cases"]]

    plan, selected = load_action_plan(plan_path, cases=cases, source_path=source)

    assert plan["plan_type"] == "agent_action_plan"
    assert len(selected) == 1
    assert selected[("模块A-row-002", 2, "模块A")]["actions"][0]["type"] == "tap_text"


def test_action_plan_rejects_natural_language_or_stale_source(tmp_path):
    source = _source(tmp_path)
    context = build_agent_context(source)
    cases = [case for module in context["modules"] for case in module["cases"]]

    invalid = _plan(source, actions=[{"type": "click_excel_action", "text": "点击列表"}])
    (tmp_path / "invalid.json").write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(AgentPlanError, match="不受支持"):
        load_action_plan(tmp_path / "invalid.json", cases=cases, source_path=source)

    stale = _plan(source)
    stale["source"]["sha256"] = "0" * 64
    (tmp_path / "stale.json").write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(AgentPlanError, match="Excel 不一致"):
        load_action_plan(tmp_path / "stale.json", cases=cases, source_path=source)


def test_structured_executor_has_no_empty_action_fallback():
    events = []

    ok, detail, mode = runner.execute_agent_actions(events, [], phase="本行操作")

    assert not ok
    assert "动作列表为空" in detail
    assert mode == "agent"
    assert events[-1]["type"] == "executor"


def test_runner_refuses_to_start_without_action_plan(tmp_path):
    with pytest.raises(ValueError, match="--action-plan"):
        runner.main(["--source", str(tmp_path / "missing.xlsx"), "--output", str(tmp_path / "run")])
