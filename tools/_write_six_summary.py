import json
from collections import Counter, defaultdict
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "output" / "2026-09-08-guotou-six-sheets"
doc = json.loads((root / "results.json").read_text(encoding="utf-8"))
groups = defaultdict(Counter)
for item in doc.get("cases", []):
    groups[str(item.get("sheet") or "")][str(item.get("status") or "")] += 1
runs = {
    "股指": root / "股指-首轮-v2",
    "沪深京": root / "沪深京-首轮",
    "板块": root / "板块-首轮",
    "港股": root / "港股-首轮",
    "其他": root / "其他-首轮",
    "看资金": root / "看资金-首轮-v3",
}
summary = {
    "run_root": str(root.resolve()),
    "source": "国投行情测试用例(1).xls",
    "app": "guotou",
    "sheets": {
        sheet: {
            "count": sum(groups[sheet].values()),
            "status_counts": dict(groups[sheet]),
            "run_dir": str(run.resolve()),
        }
        for sheet, run in runs.items()
    },
    "total": len(doc.get("cases", [])),
    "status_counts": dict(Counter(str(item.get("status") or "") for item in doc.get("cases", []))),
    "device_blocker": "c923178d ADB unauthorized after reboot; 看资金首轮后段记录保留阻塞状态。",
}
(root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False))
