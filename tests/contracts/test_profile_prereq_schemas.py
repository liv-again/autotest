from tools.contracts.validate import load_and_validate
import pathlib
FX = pathlib.Path(__file__).resolve().parents[1] / "fixtures"

def test_profile_valid():
    doc, errs = load_and_validate(FX / "profile_valid.yaml", "profile")
    assert errs == []

def test_profile_invalid():
    _, errs = load_and_validate(FX / "profile_invalid.yaml", "profile")
    assert errs

def test_prerequisites_valid():
    _, errs = load_and_validate(FX / "prerequisites_valid.yaml", "prerequisites")
    assert errs == []

def test_prerequisites_rejects_full_account():
    _, errs = load_and_validate(FX / "prerequisites_invalid.yaml", "prerequisites")
    assert errs  # 裸 account_no / 非脱敏 mask 被拒
