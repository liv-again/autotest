from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "output" / "2026-09-08-guotou-six-sheets"
RUNS = {
    "股指": ROOT / "股指-首轮-v2",
    "沪深京": ROOT / "沪深京-首轮",
    "板块": ROOT / "板块-首轮",
    "港股": ROOT / "港股-首轮",
    "其他": ROOT / "其他-首轮",
    "看资金": ROOT / "看资金-首轮-v3",
}


def main() -> None:
    cases = []
    manifests = []
    for sheet, run in RUNS.items():
        path = run / "results.json"
        if not path.is_file():
            raise SystemExit(f"missing reviewed results: {path}")
        doc = json.loads(path.read_text(encoding="utf-8"))
        cases.extend(doc.get("cases", []))
        manifests.append(doc.get("execution_manifest") or {})
    sheet_order = {name: index for index, name in enumerate(RUNS)}
    cases.sort(key=lambda item: (sheet_order.get(str(item.get("sheet") or ""), 999), int(item.get("row") or 0)))
    for index, item in enumerate(cases, start=1):
        item["source_order"] = index
        item["execution_order"] = index
    root_manifest = {
        "manifest_version": "2.0",
        "mode": "full",
        "run_id": "run-guotou-six-sheets-20260908",
        "execution_scope": "single_excel_row",
        "llm_review_required": True,
        "source_file": str((ROOT.parent.parent / "国投行情测试用例(1).xls").resolve()),
        "source_sheets": list(RUNS),
        "expected_count": sum(int(item.get("expected_count") or 0) for item in manifests),
        "selected_cases": [
            {
                "case_id": item.get("case_id"),
                "sheet": item.get("sheet"),
                "row": item.get("row"),
                "case_name": item.get("case_name", ""),
            }
            for item in cases
        ],
        "subruns": {sheet: str(run.resolve()) for sheet, run in RUNS.items()},
        "note": "看资金首轮在设备重新授权前已完成46条记录；其中确定性阻塞原样保留。",
    }
    aggregate = {
        "schema_version": "1.1",
        "reviewed": True,
        "review_scope": "single_excel_row",
        "execution_manifest": root_manifest,
        "cases": cases,
        "status_counts": dict(Counter(str(item.get("status") or "") for item in cases)),
    }
    out = ROOT / "results.json"
    out.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "cases": len(cases), "status_counts": aggregate["status_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
