import os
from tools.contracts.validate import validate, load_and_validate

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")

def test_valid_env_passes():
    doc, errs = load_and_validate(os.path.join(FIX, "env_valid.yaml"), "env")
    assert errs == []
    assert doc["assurance_level"] == "operator_attested"

def test_invalid_env_reports_errors():
    _, errs = load_and_validate(os.path.join(FIX, "env_invalid.yaml"), "env")
    assert errs  # missing required field → non-empty

def test_unknown_schema_raises():
    try:
        validate({}, "nope")
        assert False, "should raise"
    except FileNotFoundError:
        pass
