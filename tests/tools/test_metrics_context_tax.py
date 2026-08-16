import tools.metrics as M

def test_context_tax_metrics_maps_window_to_minutes():
    m = M.context_tax_metrics({"turns": 55, "actions": 80},
                              "2026-07-29T10:00:00Z", "2026-07-29T10:45:00Z")
    assert m["turns"] == 55 and m["minutes"] == 45

def test_remind_fires_over_threshold(capsys):
    M.remind({"turns": 55, "actions": 80},
             "2026-07-29T10:00:00Z", "2026-07-29T10:45:00Z")
    assert "上下文税" in capsys.readouterr().out

def test_remind_silent_under_threshold(capsys):
    M.remind({"turns": 5, "actions": 5})
    assert capsys.readouterr().out == ""
