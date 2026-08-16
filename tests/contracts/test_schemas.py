import os
import pytest
from tools.contracts.validate import load_and_validate

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")


@pytest.mark.parametrize("name", ["selection", "run", "safety_constraint"])
def test_valid_passes(name):
    _, errs = load_and_validate(os.path.join(FIX, f"{name}_valid.yaml"), name)
    assert errs == []


@pytest.mark.parametrize("name", ["selection", "run", "safety_constraint"])
def test_invalid_reports(name):
    _, errs = load_and_validate(os.path.join(FIX, f"{name}_invalid.yaml"), name)
    assert errs


def test_safety_constraint_rejects_live_submit():
    _, errs = load_and_validate(os.path.join(FIX, "safety_constraint_live.yaml"), "safety_constraint")
    assert errs  # mode=live_submit not in enum
