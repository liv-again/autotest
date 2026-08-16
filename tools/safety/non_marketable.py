import math


def check_non_marketable(price, side, quote, up_limit, down_limit, max_staleness_s, quote_ts, now_ts):
    if now_ts - quote_ts > max_staleness_s:
        return False, "quote_stale"
    # 严格不等式 price < ask1 / price > bid1（spec §5 原有"缓冲"简化为严格不等式；
    # 对报价瞬时快照仍非主动成交，报价移动的对冲由 staleness 校验 + 提交后 recovery 覆盖）
    if side == "buy":
        ask1 = quote.get("ask1")
        if ask1 is not None:
            return (price < ask1, "ok" if price < ask1 else "buy_price>=ask1")
        at_limit = math.isclose(price, down_limit, abs_tol=1e-6)
        return (at_limit, "ok" if at_limit else "no_ask_and_not_down_limit")
    if side == "sell":
        bid1 = quote.get("bid1")
        if bid1 is not None:
            return (price > bid1, "ok" if price > bid1 else "sell_price<=bid1")
        at_limit = math.isclose(price, up_limit, abs_tol=1e-6)
        return (at_limit, "ok" if at_limit else "no_bid_and_not_up_limit")
    return False, "bad_side"
