from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.site_compare_ctl import SiteCompareController, SiteCompareError


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _controller(tmp_path: Path) -> SiteCompareController:
    (tmp_path / "cases.xlsx").write_bytes(b"same source")
    return SiteCompareController(tmp_path)


def _manifest(*, rows: tuple[int, ...] = (2, 3)) -> dict:
    selected = [
        {
            "case_id": f"行情-row-{row:03d}",
            "sheet": "行情",
            "row": row,
            "case_name": f"case-{row}",
        }
        for row in rows
    ]
    return {
        "manifest_version": "1.0",
        "mode": "full",
        "expected_count": len(selected),
        "selected_cases": selected,
    }


def _records(manifest: dict, *, evidence_prefix: str) -> list[dict]:
    records = []
    for index, selected in enumerate(manifest["selected_cases"], start=1):
        records.append(
            {
                **selected,
                "status": "通过",
                "actual": (
                    "AI执行步骤：进入行情页面并检查字段。"
                    "操作结果：页面展示目标字段。"
                    "判断理由：截图证据明确显示目标字段，判定通过。"
                ),
                "execution_trace": [{"type": "tap", "status": "success"}],
                "evidence": [f"{evidence_prefix}-{index}.png"],
                "source_order": index,
                "execution_order": index,
            }
        )
    return records


def _make_run(controller: SiteCompareController, run_name: str, *, rows: tuple[int, ...] = (2, 3)) -> None:
    run_dir = controller.workflow_root / run_name
    manifest = _manifest(rows=rows)
    records = _records(manifest, evidence_prefix=run_name)
    _write_json(run_dir / "execution_manifest.json", manifest)
    _write_json(run_dir / "execution_records.json", {"execution_manifest": manifest, "cases": records})
    _write_json(run_dir / "results.final.json", {"execution_manifest": manifest, "cases": records})


def test_init_persists_app_and_rejects_duplicate(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    result = controller.init(source="cases.xlsx", app="zhongyuan")

    assert result["phase"] == "RUNNING_NEW"
    assert result["app"] == "zhongyuan"
    assert controller.load_state()["source"] == "cases.xlsx"
    with pytest.raises(SiteCompareError, match="禁止覆盖"):
        controller.init(source="cases.xlsx", app="zhongyuan")


def test_init_can_resolve_app_site_compare_source(tmp_path: Path) -> None:
    (tmp_path / "cases.xlsx").write_bytes(b"same source")
    app_dir = tmp_path / "apps" / "demo"
    app_dir.mkdir(parents=True)
    (app_dir / "app.yaml").write_text(
        "slug: demo\npackages: []\nsite_compare_source: ../../cases.xlsx\n",
        encoding="utf-8",
    )
    controller = SiteCompareController(tmp_path)

    result = controller.init(app="demo")

    assert result["source"] == "cases.xlsx"


def test_invalid_state_is_rejected(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.workflow_root.mkdir(parents=True)
    controller.state_path.write_text('{"phase":"RUNNING_NEW"}\n', encoding="utf-8")

    with pytest.raises(SiteCompareError, match="缺少字段"):
        controller.status()


def test_finish_new_requires_manifest_and_complete_results(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.init(source="cases.xlsx", app="zhongyuan")
    new_dir = controller.workflow_root / "new"
    new_dir.mkdir(exist_ok=True)

    with pytest.raises(SiteCompareError, match="manifest"):
        controller.finish_new()

    manifest = _manifest()
    records = _records(manifest, evidence_prefix="new")[:1]
    _write_json(new_dir / "execution_manifest.json", manifest)
    _write_json(new_dir / "execution_records.json", {"cases": records})
    _write_json(new_dir / "results.json", {"cases": records})
    with pytest.raises(SiteCompareError, match="数量不完整"):
        controller.finish_new()


def test_full_flow_enforces_source_and_case_scope(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.init(source="cases.xlsx", app="zhongyuan")
    _make_run(controller, "new")

    waiting = controller.finish_new()
    assert waiting["phase"] == "WAITING_FOR_OLD_SITE"
    assert waiting["next_action"] == "STOP_AND_ASK_USER_TO_SWITCH_SITE"

    (tmp_path / "cases.xlsx").write_bytes(b"changed source")
    with pytest.raises(SiteCompareError, match="Source changed"):
        controller.start_old()
    (tmp_path / "cases.xlsx").write_bytes(b"same source")
    assert controller.start_old()["phase"] == "RUNNING_OLD"

    _make_run(controller, "old", rows=(2, 4))
    with pytest.raises(SiteCompareError, match="不一致"):
        controller.finish_old()

    # Rebuild the old fixture with the exact same selected scope.
    for path in (controller.workflow_root / "old").glob("*.json"):
        path.unlink()
    _make_run(controller, "old")
    assert controller.finish_old()["phase"] == "READY_TO_COMPARE"
    inputs = controller.compare_inputs()
    assert inputs["new_results"] == "runs/site_compare/new/results.final.json"
    assert inputs["old_records"] == "runs/site_compare/old/execution_records.json"

    comparison = {
        "version": 1,
        "source": "cases.xlsx",
        "cases": [
            {"case_key": "行情:2", "status": "MATCH", "reason": "证据明确一致。"},
            {"case_key": "行情-row-003", "status": "PENDING", "reason": "证据不足。"},
        ],
    }
    comparison_path = controller.workflow_root / "comparison" / "comparison.json"
    _write_json(comparison_path, comparison)
    completed = controller.complete("runs/site_compare/comparison/comparison.json")
    assert completed["phase"] == "COMPLETED"
    assert controller.load_state()["comparison_file"] == "runs/site_compare/comparison/comparison.json"


def test_phase_guards_and_comparison_coverage(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.init(source="cases.xlsx", app="zhongyuan")
    with pytest.raises(SiteCompareError, match="RUNNING_NEW"):
        controller.start_old()
    with pytest.raises(SiteCompareError, match="READY_TO_COMPARE"):
        controller.compare_inputs()

    _make_run(controller, "new")
    controller.finish_new()
    controller.start_old()
    _make_run(controller, "old")
    controller.finish_old()

    bad = {
        "version": 1,
        "cases": [{"case_key": "行情!2", "status": "MATCH", "reason": "只有一个。"}],
    }
    path = controller.workflow_root / "comparison" / "bad.json"
    _write_json(path, bad)
    with pytest.raises(SiteCompareError, match="缺 Case"):
        controller.complete(path)
