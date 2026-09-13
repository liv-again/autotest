import json

from openpyxl import Workbook

from tools.navigation_probe import (
    build_probe_queue,
    select_probe_cases,
)


def _case(sheet, row, source_order, *, level_3="", entry="", priority="P1"):
    return {
        "sheet": sheet,
        "row": row,
        "source_order": source_order,
        "case_id": f"{sheet}-row-{row:03d}",
        "case_name": f"TC_{row}",
        "priority": priority,
        "page_group_id": f"{sheet}-page-group-{row:03d}",
        "page_group_key": f"{sheet}|行情|{level_3}|{entry}",
        "navigation_context": {
            "sheet": sheet,
            "level_1": "行情",
            "level_2": sheet,
            "level_3": level_3,
            "entry": entry,
        },
    }


def test_generic_probe_selects_one_case_per_route_and_prefers_p0():
    plan = {
        "modules": [
            {
                "sheet": "模块A",
                "cases": [
                    _case("模块A", 2, 1, level_3="首页", priority="P1"),
                    _case("模块A", 3, 2, level_3="首页", priority="P0"),
                    _case("模块A", 4, 3, level_3="列表", priority="P1"),
                ],
            }
        ]
    }

    selected = select_probe_cases(plan, app_slug="other")

    assert [(item["sheet"], item["row"]) for item in selected] == [
        ("模块A", 3),
        ("模块A", 4),
    ]


def test_explicit_probe_selectors_resolve_case_id_and_sheet_row():
    cases = [
        _case("模块A", 2, 1, level_3="首页"),
        _case("模块A", 3, 2, level_3="列表"),
    ]
    plan = {"modules": [{"sheet": "模块A", "cases": cases}]}

    selected = select_probe_cases(
        plan,
        selectors=("模块A!3", "模块A-row-002"),
    )

    assert [(item["sheet"], item["row"]) for item in selected] == [
        ("模块A", 2),
        ("模块A", 3),
    ]


def test_guotou_probe_queue_contains_route_canaries(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "股指"
    sheet.append(["一级目录", "用例名称", "优先级", "入口", "步骤名称", "前置条件", "操作描述", "参数", "预期结果"])
    sheet.append(["行情", "首页身份", "P0", "", "目标页", "", "查看", "", "显示首页"])
    sheet.append(["行情", "宫格展示", "P0", "", "目标页", "", "查看", "", "显示宫格"])
    for row in range(4, 8):
        sheet.append(["行情", f"用例{row}", "P0", "", "目标页", "", "查看", "", "显示"])
    source = tmp_path / "guotou.xlsx"
    workbook.save(source)

    # A small synthetic workbook cannot contain the full Guotou preset.  The
    # explicit selector path remains deterministic and is the supported escape
    # hatch when a source does not contain the preset rows.
    queue = build_probe_queue(source, selectors=("股指!2", "股指!3"))

    assert queue["queue_type"] == "navigation_probe"
    assert queue["navigation_only"] is True
    assert [item["case_id"] for item in queue["cases"]] == [
        "股指-row-002",
        "股指-row-003",
    ]


def test_probe_queue_is_json_serializable():
    plan = {
        "modules": [{"sheet": "模块A", "cases": [_case("模块A", 2, 1, level_3="首页")]}],
        "app_profile_context": {"slug": "other"},
    }
    selected = select_probe_cases(plan, app_slug="other")
    assert json.loads(json.dumps(selected, ensure_ascii=False))[0]["case_id"] == "模块A-row-002"
