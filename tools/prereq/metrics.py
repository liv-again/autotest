def _hashable(v):
    return tuple(sorted(v.items())) if isinstance(v, dict) else v


def _label(entry):
    inst = None
    ri = entry.get("required_instruments") or []
    if ri:
        inst = ri[0]
    elif "instrument" in entry:
        inst = entry["instrument"]
    inst = inst or {}
    return (entry.get("polarity"), entry.get("required_account", "any"),
            frozenset((k, _hashable(v)) for k, v in inst.items()))


def _attr_frozenset(merged):
    return frozenset((k, _hashable(v)) for k, v in merged.items())


def _pred_instrument_attrs(case):
    """一条用例 required_instruments 里全部 instrument dict 的属性并集。"""
    merged = {}
    for inst in case.get("required_instruments") or []:
        for k, v in (inst or {}).items():
            merged[k] = v
    return _attr_frozenset(merged)


def _matched_rule_attrs(rule_ids, rules_index):
    """命中规则 requires.instrument 属性并集(追溯真值)。"""
    merged = {}
    for rid in rule_ids:
        r = rules_index.get(rid)
        if not r:
            continue
        for k, v in (r.get("requires", {}).get("instrument") or {}).items():
            merged[k] = v
    return _attr_frozenset(merged)


def score(request, gold, rules_doc):
    preds = {c["tc_id"]: c for c in request["cases"]}
    need = {"positive", "negative_property"}
    gold_need = {tid: g for tid, g in gold.items() if g.get("polarity") in need}

    correct = 0
    for tid, g in gold_need.items():
        p = preds.get(tid)
        if p and p["status"] == "identified" and _label(p) == _label(g):
            correct += 1
    recall = correct / len(gold_need) if gold_need else 1.0
    manual_supplement = len(gold_need) - correct

    # 误报：预测需备码但 gold 无标注(过报) 或 label 不符。召回优先→仅设宽松上界(测试断言 <=0.34)。
    pred_need = [c for c in request["cases"] if c["polarity"] in need]
    fp = 0
    for c in pred_need:
        g = gold.get(c["tc_id"])
        if not g or _label(c) != _label(g):
            fp += 1
    false_positive_rate = fp / len(pred_need) if pred_need else 0.0

    # 极性正确率分母：gold 有标注 且 引擎已 identified；排除 unidentified(那是漏报,归 recall,不算极性错误)。
    both = [tid for tid in gold
            if tid in preds and preds[tid]["status"] == "identified"]
    pol_ok = sum(1 for tid in both if gold[tid].get("polarity") == preds[tid]["polarity"])
    polarity_accuracy = pol_ok / len(both) if both else 1.0

    # 完整性门(守 Fix A)：每条 identified 用例 required_instruments 的属性并集
    # 必须 == 其全部 matched 规则 requires.instrument 属性并集(双向:无静默丢/无凭空多出)。
    # Fix A 之后应=1.0；若再退化成静默丢,此门跌破 1.0 被抓。
    idx = {r["id"]: r for r in rules_doc["rules"]}
    ident = [c for c in request["cases"] if c["status"] == "identified"]
    complete = sum(1 for c in ident
                   if _pred_instrument_attrs(c)
                   == _matched_rule_attrs(c["matched_rule_ids"], idx))
    traceability = complete / len(ident) if ident else 1.0

    return {"recall": recall, "false_positive_rate": false_positive_rate,
            "unidentified_count": len(request["unidentified"]),
            "manual_supplement": manual_supplement,
            "polarity_accuracy": polarity_accuracy, "traceability": traceability}
