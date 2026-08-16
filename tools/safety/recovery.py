_CANCELABLE = {"已报", "未报", "部成", "可撤"}   # 非终态可撤；已撤/部撤/已成=终态不再撤

# 匹配键不含账户维度——假设 today_orders 是单账户快照（本轮登录账户）；
# 多账户快照需在接线层按账户过滤后再传入，否则可能误匹配/误 STOP。
def _match(o, ro, window_s):
    if ro.get("contract_no") and o.get("contract_no"):
        return o["contract_no"] == ro["contract_no"]
    return (o["code"] == ro["code"] and o["side"] == ro["side"]
            and o["qty"] == ro["qty"] and abs(o["price"] - ro["price"]) < 1e-6
            and abs(o["submit_ts"] - ro["submit_ts"]) <= window_s)

def plan_recovery(run_orders, today_orders, window_s=120):
    cancel = []
    for ro in run_orders:
        matches = [o for o in today_orders if _match(o, ro, window_s)]
        if len(matches) > 1:
            return {"action": "STOP", "cancel": [], "stop_reason": f"ambiguous_match:{ro['code']}"}
        if len(matches) == 1 and matches[0]["status"] in _CANCELABLE:
            cancel.append(matches[0])
    return {"action": "CANCEL", "cancel": cancel, "stop_reason": None}
