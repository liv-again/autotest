from tools.safety.recovery import plan_recovery

RO = {"code": "950025", "side": "buy", "qty": 100, "price": 67.343, "submit_ts": 1000, "contract_no": None}

def test_unique_cancelable_match_planned():
    today = [{"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1001,"status":"已报"}]
    out = plan_recovery([RO], today)
    assert out["action"] == "CANCEL" and len(out["cancel"]) == 1

def test_ambiguous_match_stops():
    today = [
      {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1001,"status":"已报"},
      {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1002,"status":"已报"}]
    out = plan_recovery([RO], today)
    assert out["action"] == "STOP" and "ambiguous" in out["stop_reason"]

def test_no_match_no_cancel():
    out = plan_recovery([RO], [])
    assert out["action"] == "CANCEL" and out["cancel"] == []

def test_terminal_status_not_cancelable():
    today = [{"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1001,"status":"已撤"}]
    out = plan_recovery([RO], today)
    assert out["cancel"] == []

def test_contract_no_uniquely_matches():
    ro = {**RO, "contract_no": "6"}
    today = [
      {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1001,"status":"已报","contract_no":"6"},
      {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1002,"status":"已报","contract_no":"8"}]
    out = plan_recovery([ro], today)
    assert out["action"] == "CANCEL" and len(out["cancel"]) == 1

def test_single_side_contract_no_falls_back_to_tuple():
    ro = {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1000,"contract_no":"6"}
    today = [{"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1001,"status":"已报"}]  # no contract_no
    out = plan_recovery([ro], today)
    assert out["action"] == "CANCEL" and len(out["cancel"]) == 1

def test_stop_discards_prior_cancels():
    ro1 = {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1000,"contract_no":None}
    ro2 = {"code":"950015","side":"sell","qty":100,"price":300.0,"submit_ts":2000,"contract_no":None}
    today = [
      {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1001,"status":"已报"},
      {"code":"950015","side":"sell","qty":100,"price":300.0,"submit_ts":2001,"status":"已报"},
      {"code":"950015","side":"sell","qty":100,"price":300.0,"submit_ts":2002,"status":"已报"}]
    out = plan_recovery([ro1, ro2], today)
    assert out["action"] == "STOP" and out["cancel"] == []
