from itertools import combinations

from tools.prereq.rules import rules_for


def case_text(case):
    return case["title"] + " " + " ".join(case.get("keywords", []))


def match_rule(case, rule):
    t = case_text(case)
    m = rule.get("match", {})
    if not all(k in t for k in m.get("all", [])):
        return False
    anys = m.get("any", [])
    if anys and not any(k in t for k in anys):
        return False
    if any(k in t for k in m.get("none", [])):
        return False
    return True


def _primary(matched):
    return sorted(matched, key=lambda r: r["priority"], reverse=True)[0]


def _merge_instruments(matched):
    """命中规则 requires.instrument 属性并集。
    返回 (merged, conflict_keys)：
    - 键不重叠 → 合并进同一 dict（收全属性,不静默丢）；
    - 同键同值 → 保留一份；
    - 同键异值 → 记入 conflict_keys（值取先见,供人工裁决）。"""
    merged = {}
    conflict_keys = []
    for r in matched:
        inst = r["requires"].get("instrument") or {}
        for k, v in inst.items():
            if k in merged:
                if merged[k] != v and k not in conflict_keys:
                    conflict_keys.append(k)
            else:
                merged[k] = v
    return merged, conflict_keys


def _instrument_conflict(matched, conflict_keys):
    """定位首个 instrument 冲突键，返回 (note, [rule_id_a, rule_id_b])；无则 None。
    冲突为 union 级(任意两命中规则同键取值相反),不限于并列最高优先级。"""
    if not conflict_keys:
        return None
    k = conflict_keys[0]
    holders = {}                       # value -> 首个携带该值的 rule_id
    for r in matched:
        inst = r["requires"].get("instrument") or {}
        if k in inst and inst[k] not in holders:
            holders[inst[k]] = r["id"]
    (va, ra), (vb, rb) = list(holders.items())[:2]
    note = f"instrument.{k}: {va} vs {vb} (polarity/requires 不相容)"
    return note, [ra, rb]


def _account_conflict(tops):
    """并列最高优先级里 account 一普通一信用的互斥，返回 (note, [a, b])；无则 None。"""
    for a, b in combinations(tops, 2):
        aa = a["requires"].get("account")
        ab = b["requires"].get("account")
        if aa and ab and aa != ab and "any" not in (aa, ab):
            note = f"account: {aa} vs {ab} (polarity/requires 不相容)"
            return note, [a["id"], b["id"]]
    return None


def extract(cases, rules_doc, app_slug="*", market="北交所"):
    pool = rules_for(rules_doc, app_slug, market)
    out_cases, unidentified, conflicts = [], [], []
    conflict_count = 0
    for case in cases:
        matched = [r for r in pool if match_rule(case, r)]
        if not matched:
            out_cases.append({
                "tc_id": case["tc_id"], "title": case["title"],
                "matched_rule_ids": [], "status": "unidentified",
                "required_instruments": [], "required_account": "any",
                "polarity": "unknown", "needed_codes": [], "provenance": [],
            })
            unidentified.append(case["tc_id"])
            continue

        merged, conflict_keys = _merge_instruments(matched)

        top = max(r["priority"] for r in matched)
        tops = [r for r in matched if r["priority"] == top]
        conflict = (_instrument_conflict(matched, conflict_keys)
                    or (_account_conflict(tops) if len(tops) > 1 else None))
        note = conflict[0] if conflict else None

        p = _primary(matched)
        rec = {
            "tc_id": case["tc_id"], "title": case["title"],
            "matched_rule_ids": [r["id"] for r in matched],   # 全部命中 = 追溯
            "status": "conflict" if note else "identified",
            "required_instruments": [merged] if merged else [],  # 全部命中规则属性并集
            "required_account": p["requires"].get("account", "any"),
            "polarity": p["polarity"], "needed_codes": [],
            "provenance": [p["provenance"]],
        }
        if p.get("expected_capability"):                       # negative_property 带预期失败
            rec["expected_capability"] = p["expected_capability"]
        out_cases.append(rec)

        if note:
            conflict_count += 1
            conflicts.append({
                "tc_id": case["tc_id"],
                "rule_ids": conflict[1],
                "note": note,
            })

    identified = sum(1 for c in out_cases if c["status"] == "identified")
    return {
        "rules_version": rules_doc["version"],
        "generated_from": {"cases_count": len(cases)},
        "cases": out_cases,
        "unidentified": unidentified,
        "conflicts": conflicts,
        "summary": {
            "identified": identified,
            "unidentified": len(unidentified),
            "conflict": conflict_count,
            "missing_codes": [],
        },
    }
