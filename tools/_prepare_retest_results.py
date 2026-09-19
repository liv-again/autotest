"""Add explicit gate reasons to a retest result document before merging."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(src: str, out: str) -> None:
    document = json.loads(Path(src).read_text(encoding="utf-8"))
    for record in document.get("cases", []):
        reason = str(record.get("judgment_reason") or record.get("judgment", {}).get("reason") or "复测已完成，保留待验证。")
        record["reason"] = reason
        if str(record.get("status") or "").startswith("⛔"):
            record["blocker"] = reason
    Path(out).write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": out, "cases": len(document.get("cases", []))}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
