import base64

import pytest
from openpyxl import Workbook, load_workbook

from tools.annotate_excel import AnnotationError, annotate_workbook
from tools.build_results import BuildResultsError, build_results
from tools.contracts.validate import validate


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _make_workbook(path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "用例"
    sheet.append(["用例ID", "用例名称", "优先级", "步骤"])
    sheet.append(["TC-001", "查询", "high", "点击查询"])
    workbook.save(path)


def _strict_case(*, actual, status="✅通过", evidence=None, case_id="TC-001"):
    return {
        "sheet": "用例",
        "row": 2,
        "case_id": case_id,
        "status": status,
        "actual": actual,
        "evidence": evidence or [f"ui assertion: {case_id}"],
        "action_trace": [
            {"type": "tap", "target": case_id, "result": "success"}
        ],
    }


def test_strict_rejects_generic_actual_before_saving(tmp_path):
    source = tmp_path / "cases.xlsx"
    output = tmp_path / "out.xlsx"
    _make_workbook(source)

    with pytest.raises(AnnotationError, match="质量校验"):
        annotate_workbook(
            source,
            [_strict_case(actual="已在国投 App 中执行该行指定的点击操作并保留截图。")],
            output,
            strict=True,
        )
    assert not output.exists()


def test_strict_rejects_action_echo_with_page_labels(tmp_path):
    source = tmp_path / "cases.xlsx"
    output = tmp_path / "out.xlsx"
    _make_workbook(source)

    with pytest.raises(AnnotationError, match="质量校验"):
        annotate_workbook(
            source,
            [
                _strict_case(
                    actual="Excel行2已执行：点击查询\n输入关键字；当前页面观察：query_page、结果列表"
                )
            ],
            output,
            strict=True,
        )
    assert not output.exists()


def test_strict_rejects_ui_tree_only_actual(tmp_path):
    source = tmp_path / "cases.xlsx"
    output = tmp_path / "out.xlsx"
    _make_workbook(source)

    with pytest.raises(AnnotationError, match="UI 树"):
        annotate_workbook(
            source,
            [_strict_case(actual="当前页面观察：action_bar_root、content、hx_page_view")],
            output,
            strict=True,
        )
    assert not output.exists()


def test_strict_preserves_not_applicable_status_and_inherits_case_evidence(tmp_path):
    source = tmp_path / "cases.xlsx"
    output = tmp_path / "out.xlsx"
    evidence = tmp_path / "evidence.png"
    evidence.write_bytes(TINY_PNG)
    _make_workbook(source)

    report = annotate_workbook(
        source,
        {
            "execution_manifest": {
                "mode": "full",
                "expected_count": 1,
                "selected_cases": [{"sheet": "用例", "row": 2, "case_id": "TC-001"}],
            },
            "cases": [
                {
                    "sheet": "用例",
                    "row": 2,
                    "case_id": "TC-001",
                    "status": "☑不适用",
                    "evidence": [{"path": "evidence.png", "description": "页面截图"}],
                    "steps": [
                        {
                            "step_id": "TC-001#S1",
                            "step_index": 1,
                            "row": 2,
                            "status": "☑不适用",
                            "actual": "页面显示该功能不适用",
                        }
                    ],
                }
            ]
        },
        output,
        result_dir=tmp_path,
        strict=True,
        append_summary=False,
    )

    assert len(report["matched"]) == 1
    workbook = load_workbook(output)
    sheet = workbook["用例"]
    headers = [sheet.cell(1, column).value for column in range(1, sheet.max_column + 1)]
    status_column = headers.index("🤖AI状态") + 1
    evidence_column = headers.index("🤖AI证据") + 1
    assert sheet.cell(2, status_column).value == "☑不适用"
    assert sheet.cell(2, evidence_column).value == "页面截图: evidence.png"
    assert len(sheet._images) == 1


def test_strict_rejects_reused_actual_across_unrelated_cases(tmp_path):
    source = tmp_path / "cases.xlsx"
    output = tmp_path / "out.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "用例"
    sheet.append(["用例ID", "用例名称", "优先级", "步骤"])
    for case_id, name in (("TC-001", "查询"), ("TC-002", "排序"), ("TC-003", "详情")):
        sheet.append([case_id, name, "high", "操作"])
    workbook.save(source)

    cases = [
        _strict_case(actual="页面状态正常", case_id=case_id)
        for case_id in ("TC-001", "TC-002", "TC-003")
    ]
    for row, case in enumerate(cases, start=2):
        case["row"] = row

    with pytest.raises(AnnotationError, match="复用"):
        annotate_workbook(source, cases, output, strict=True)
    assert not output.exists()


def test_contextual_repeat_actual_is_allowed_for_independent_rows():
    cases = []
    selected = []
    for row, case_id in ((2, "TC-001"), (3, "TC-002"), (4, "TC-003")):
        case = _strict_case(actual="当前页面显示港股首页", case_id=case_id)
        case.update(
            {
                "row": row,
                "action": f"执行第 {row} 行业务动作",
                "expected": f"第 {row} 行预期结果",
                "evidence": [f"shots/{row}.png"],
            }
        )
        cases.append(case)
        selected.append({"sheet": "用例", "row": row, "case_id": case_id})

    document = build_results(
        {
            "execution_manifest": {
                "mode": "full",
                "expected_count": len(selected),
                "selected_cases": selected,
            },
            "cases": cases,
        }
    )

    assert len(document["cases"]) == 3


def test_build_results_preserves_setup_trace_and_inherits_evidence():
    document = build_results(
        {
            "execution_manifest": {
                "mode": "full",
                "expected_count": 1,
                "selected_cases": [{"sheet": "用例", "row": 2, "case_id": "TC-001"}],
            },
            "cases": [
                {
                    "sheet": "用例",
                    "row": 2,
                    "case_id": "TC-001",
                    "status": "✅通过",
                    "evidence": ["assertions/query-page"],
                    "steps": [
                        {
                            "step_id": "TC-001#S1",
                            "step_index": 1,
                            "row": 2,
                            "status": "✅通过",
                                "actual": "点击查询后进入查询页面并显示结果列表",
                                "action_trace": [
                                    {"type": "tap", "target": "查询", "result": "success"}
                                ],
                        }
                    ],
                }
            ],
            "setup_trace": [{"kind": "setup", "action": "进入行情首页"}],
        }
    )

    assert document["schema_version"] == "2.0"
    assert document["setup_trace"][0]["kind"] == "setup"
    assert document["cases"][0]["steps"][0]["evidence"] == ["assertions/query-page"]
    assert validate(document, "results") == []


def test_build_results_never_fills_missing_actual():
    with pytest.raises(BuildResultsError, match="actual"):
        build_results(
            {
                "cases": [
                    {
                        "sheet": "用例",
                        "row": 2,
                        "status": "✅通过",
                        "evidence": ["assertions/query-page"],
                    }
                ]
            }
        )


def test_build_results_requires_global_execution_manifest_and_action_trace():
    case = _strict_case(actual="页面显示查询结果")
    with pytest.raises(BuildResultsError, match="execution_manifest"):
        build_results({"cases": [case]})

    case_without_trace = dict(case)
    case_without_trace.pop("action_trace")
    with pytest.raises(BuildResultsError, match="action_trace"):
        build_results(
            {
                "execution_manifest": {
                    "mode": "full",
                    "expected_count": 1,
                    "selected_cases": [{"sheet": "用例", "row": 2, "case_id": "TC-001"}],
                },
                "cases": [case_without_trace],
            }
        )


def test_build_results_rejects_unexecuted_and_reused_evidence():
    not_executed = _strict_case(actual="尚未执行", status="未执行")
    with pytest.raises(BuildResultsError, match="未完成执行"):
        build_results(
            {
                "execution_manifest": {
                    "mode": "full",
                    "expected_count": 1,
                    "selected_cases": [{"sheet": "用例", "row": 2, "case_id": "TC-001"}],
                },
                "cases": [not_executed],
            }
        )

    first = _strict_case(actual="页面显示查询结果", case_id="TC-001")
    second = _strict_case(actual="页面显示排序结果", case_id="TC-002")
    second["row"] = 3
    second["evidence"] = list(first["evidence"])
    with pytest.raises(BuildResultsError, match="独立结果证据"):
        build_results(
            {
                "execution_manifest": {
                    "mode": "full",
                    "expected_count": 2,
                    "selected_cases": [
                        {"sheet": "用例", "row": 2, "case_id": "TC-001"},
                        {"sheet": "用例", "row": 3, "case_id": "TC-002"},
                    ],
                },
                "cases": [first, second],
            }
        )


def test_page_batching_remains_allowed_when_cases_are_independently_traced():
    first = _strict_case(actual="页面显示查询结果", case_id="TC-001")
    second = _strict_case(actual="页面显示排序结果", case_id="TC-002")
    second["row"] = 3
    document = build_results(
        {
            "execution_manifest": {
                "mode": "full",
                "expected_count": 2,
                "selected_cases": [
                    {"sheet": "用例", "row": 2, "case_id": "TC-001"},
                    {"sheet": "用例", "row": 3, "case_id": "TC-002"},
                ],
            },
            "setup_trace": [{"kind": "setup", "action": "进入查询页"}],
            "cases": [first, second],
        }
    )
    assert document["execution_manifest"]["expected_count"] == 2
    assert len(document["cases"]) == 2
