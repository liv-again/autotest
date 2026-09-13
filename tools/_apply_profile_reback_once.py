"""Apply the reviewed six-sheet profile update plan with a reversible backup."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date
from pathlib import Path

import yaml

# Allow direct execution from the tools directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.reback import reback_run


def main() -> int:
    root = Path("output/2026-09-08-guotou-six-sheets").resolve()
    app_dir = Path("apps/guotou").resolve()
    profile_path = app_dir / "profile.yaml"
    prereq_path = app_dir / "prerequisites.yaml"
    plan = json.loads((root / "profile_reback_plan.json").read_text(encoding="utf-8"))
    approved = plan.get("approved_updates", [])
    if not approved:
        raise SystemExit("没有审核通过的候选，停止写回")

    backup_profile = root / "profile.before-reback.yaml"
    backup_prereq = root / "prerequisites.before-reback.yaml"
    shutil.copy2(profile_path, backup_profile)
    shutil.copy2(prereq_path, backup_prereq)

    with profile_path.open(encoding="utf-8") as handle:
        before = yaml.safe_load(handle) or {}
    before_by_key = {item.get("key"): dict(item) for item in before.get("entries", [])}

    updates_by_key: dict[str, dict[str, str]] = {}
    for item in approved:
        key = item["profile_key"]
        proposed = item.get("proposed_merge", {})
        current = updates_by_key.setdefault(key, {"key": key})
        current["last_verified"] = max(str(current.get("last_verified", "")), str(proposed.get("last_verified", "")))
        # Use the aggregate run root so all six sheet sub-runs remain addressable.
        current["evidence_run"] = "output/2026-09-08-guotou-six-sheets"

    reback_run(profile_path, prereq_path, {"profile": {"entries": list(updates_by_key.values())}})

    with profile_path.open(encoding="utf-8") as handle:
        after = yaml.safe_load(handle) or {}
    after_by_key = {item.get("key"): dict(item) for item in after.get("entries", [])}
    changes = []
    for key, update in updates_by_key.items():
        changes.append(
            {
                "key": key,
                "before": {field: before_by_key.get(key, {}).get(field) for field in ("last_verified", "evidence_run", "status")},
                "applied": {field: after_by_key.get(key, {}).get(field) for field in ("last_verified", "evidence_run", "status")},
                "candidate_count": sum(item["profile_key"] == key for item in approved),
            }
        )

    audit = {
        "schema_version": "1.0",
        "applied_at": date.today().isoformat(),
        "run_root": str(root),
        "profile_path": str(profile_path),
        "prerequisites_path": str(prereq_path),
        "backup_profile": str(backup_profile),
        "backup_prerequisites": str(backup_prereq),
        "candidate_count": len(approved),
        "updated_key_count": len(updates_by_key),
        "changes": changes,
    }
    (root / "profile_reback_applied.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
