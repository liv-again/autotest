from tools.safety.non_marketable import check_non_marketable as chk

Q = {"ask1": 96.200, "bid1": None}

def test_buy_below_ask_ok():
    ok, _ = chk(67.343, "buy", Q, up_limit=125.063, down_limit=67.343, max_staleness_s=5, quote_ts=100, now_ts=101)
    assert ok

def test_buy_at_or_above_ask_rejected():
    ok, r = chk(96.200, "buy", Q, 125.063, 67.343, 5, 100, 101)
    assert not ok and "ask" in r

def test_sell_above_bid_ok():
    ok, _ = chk(125.063, "sell", {"ask1": None, "bid1": 96.0}, 125.063, 67.343, 5, 100, 101)
    assert ok

def test_no_ask_requires_down_limit():
    ok, _ = chk(67.343, "buy", {"ask1": None, "bid1": None}, 125.063, 67.343, 5, 100, 101)
    assert ok
    ok2, r = chk(80.0, "buy", {"ask1": None, "bid1": None}, 125.063, 67.343, 5, 100, 101)
    assert not ok2 and "down_limit" in r

def test_stale_quote_rejected():
    ok, r = chk(67.343, "buy", Q, 125.063, 67.343, 5, 100, 110)
    assert not ok and "stale" in r

def test_sell_no_bid_requires_up_limit():
    ok, _ = chk(125.063, "sell", {"ask1": None, "bid1": None}, 125.063, 67.343, 5, 100, 101)
    assert ok
    ok2, r = chk(120.0, "sell", {"ask1": None, "bid1": None}, 125.063, 67.343, 5, 100, 101)
    assert not ok2 and "up_limit" in r

def test_sell_at_bid_rejected():
    ok, r = chk(96.0, "sell", {"ask1": None, "bid1": 96.0}, 125.063, 67.343, 5, 100, 101)
    assert not ok and "bid" in r

def test_bad_side_rejected():
    ok, r = chk(100.0, "hold", {"ask1": 96.2, "bid1": None}, 125.063, 67.343, 5, 100, 101)
    assert not ok and "side" in r
