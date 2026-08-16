import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "context_tax_reminder",
    pathlib.Path(__file__).resolve().parents[2] / ".claude/hooks/context_tax_reminder.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
assess = mod.assess

TH = {"turns": 40, "actions": 60, "minutes": 30}

def test_no_reminder_below_thresholds():
    assert assess({"turns": 10, "actions": 20, "minutes": 5}, TH) == []

def test_reminder_on_turn_overflow():
    out = assess({"turns": 55, "actions": 20, "minutes": 5}, TH)
    assert any("turn" in m or "新开" in m for m in out)

def test_reminder_lists_all_breached():
    out = assess({"turns": 55, "actions": 80, "minutes": 45}, TH)
    assert len(out) == 3
