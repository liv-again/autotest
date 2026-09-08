from pathlib import Path
import json

root = Path(__file__).resolve().parents[1]
results = root / "output" / "2026-09-08-guotou-six-sheets" / "results.json"
doc = json.loads(results.read_text(encoding="utf-8"))
missing = []
for case in doc.get("cases", []):
    paths = []
    for value in case.get("evidence") or []:
        path = Path(str(value).replace("/", "\\"))
        if path.is_file():
            paths.append(str(path))
        else:
            missing.append(str(value))
    case["evidence"] = paths
doc["annotation_note"] = f"{len(missing)} 条证据路径在设备重启后未生成，已保留对应文字记录并在汇总中标注。"
doc["missing_evidence_paths"] = missing
out = results.with_name("results.annotatable.json")
out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"out": str(out), "missing_evidence": len(missing)}, ensure_ascii=False))
