from tools.safety.non_marketable import check_non_marketable

def guard_submit(order, account_hmac, constraint, quote_ctx, now_ts):
    if constraint.get("mode") == "confirm_only":
        return False, ["mode_confirm_only"]
    for k in ("code", "price", "qty", "side"):
        if order.get(k) in (None, ""):
            return False, ["field_missing"]
    reasons = []
    if account_hmac not in constraint.get("account_allowlist_hmac", []):
        reasons.append("account_not_allowed")
    if order["code"] not in constraint.get("code_allowlist", []):
        reasons.append("code_not_allowed")
    qty = order["qty"]
    if not isinstance(qty, int) or qty <= 0:
        reasons.append("qty_invalid")
    elif qty > constraint.get("qty_max", 0):
        reasons.append("qty_over_max")
    pr = constraint.get("price_rule")
    if pr == "non_marketable":
        required = ("ask1", "bid1", "up_limit", "down_limit", "max_staleness_s", "quote_ts")
        if not isinstance(quote_ctx, dict) or any(k not in quote_ctx for k in required):
            reasons.append("quote_incomplete")
        else:
            ok, why = check_non_marketable(order["price"], order["side"], quote_ctx,
                quote_ctx["up_limit"], quote_ctx["down_limit"],
                quote_ctx["max_staleness_s"], quote_ctx["quote_ts"], now_ts)
            if not ok:
                reasons.append("price_marketable:" + why)
    else:
        reasons.append("unknown_price_rule:" + str(pr))
    return (not reasons, reasons or ["ok"])
