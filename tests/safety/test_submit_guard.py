from tools.safety.submit_guard import guard_submit

CONS = {"mode": "simulated_submit", "code_allowlist": ["950025"], "qty_max": 100,
        "price_rule": "non_marketable", "account_allowlist_hmac": ["HMAC_XY"]}
QC = {"ask1": 96.2, "bid1": None, "up_limit": 125.063, "down_limit": 67.343,
      "max_staleness_s": 5, "quote_ts": 100}
ORDER = {"code": "950025", "price": 67.343, "qty": 100, "side": "buy"}

def test_all_pass_allows():
    ok, r = guard_submit(ORDER, "HMAC_XY", CONS, QC, now_ts=101)
    assert ok and r == ["ok"]

def test_confirm_only_never_submits():
    ok, r = guard_submit(ORDER, "HMAC_XY", {**CONS, "mode": "confirm_only"}, QC, 101)
    assert not ok and "mode_confirm_only" in r

def test_account_not_allowed():
    ok, r = guard_submit(ORDER, "HMAC_OTHER", CONS, QC, 101)
    assert not ok and "account_not_allowed" in r

def test_code_not_allowed():
    ok, r = guard_submit({**ORDER, "code": "950015"}, "HMAC_XY", CONS, QC, 101)
    assert not ok and "code_not_allowed" in r

def test_qty_over_max():
    ok, r = guard_submit({**ORDER, "qty": 200}, "HMAC_XY", CONS, QC, 101)
    assert not ok and "qty_over_max" in r

def test_marketable_price_rejected():
    ok, r = guard_submit({**ORDER, "price": 96.2}, "HMAC_XY", CONS, QC, 101)
    assert not ok and any("price_marketable" in x for x in r)

def test_missing_field_rejected():
    ok, r = guard_submit({**ORDER, "price": None}, "HMAC_XY", CONS, QC, 101)
    assert not ok and "field_missing" in r

def test_qty_zero_rejected():
    ok, r = guard_submit({**ORDER, "qty": 0}, "HMAC_XY", CONS, QC, 101)
    assert not ok and "qty_invalid" in r

def test_qty_negative_rejected():
    ok, r = guard_submit({**ORDER, "qty": -100}, "HMAC_XY", CONS, QC, 101)
    assert not ok and "qty_invalid" in r

def test_unknown_price_rule_rejected():
    ok, r = guard_submit(ORDER, "HMAC_XY", {**CONS, "price_rule": "whatever"}, QC, 101)
    assert not ok and any("unknown_price_rule" in x for x in r)

def test_missing_price_rule_rejected():
    c = {k: v for k, v in CONS.items() if k != "price_rule"}
    ok, r = guard_submit(ORDER, "HMAC_XY", c, QC, 101)
    assert not ok and any("unknown_price_rule" in x for x in r)

def test_multiple_violations_accumulate():
    ok, r = guard_submit({**ORDER, "code": "999", "qty": 500}, "BAD", CONS, QC, 101)
    assert not ok and "account_not_allowed" in r and "code_not_allowed" in r and "qty_over_max" in r

def test_quote_incomplete_rejected():
    qc = {k: v for k, v in QC.items() if k != "up_limit"}
    ok, r = guard_submit(ORDER, "HMAC_XY", CONS, qc, 101)
    assert not ok and "quote_incomplete" in r
