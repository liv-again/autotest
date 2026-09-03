from tools.contracts.validate import load_and_validate, validate
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


def test_stock_special_status_is_required_and_enum_limited():
    base = {
        "slug": "x",
        "account_capabilities": [],
        "instrument_properties": [],
        "known_codes": [{
            "code": "430019",
            "name": "测试股票",
            "market": "北交所",
            "attributes": {"market": "北交所", "product": "股票", "special_status": "普通"},
        }],
    }
    assert validate(base, "prerequisites") == []

    missing = {**base, "known_codes": [{**base["known_codes"][0],
                                         "attributes": {"market": "北交所", "product": "股票"}}]}
    assert validate(missing, "prerequisites")

    invalid = {**base, "known_codes": [{**base["known_codes"][0],
                                         "attributes": {"market": "北交所", "product": "股票", "special_status": "ST"}}]}
    assert validate(invalid, "prerequisites")


def test_capabilities_allow_version_metadata():
    # 回归守护（§七-8 修复）：capabilities 与 entries 对齐后，允许 app_version/evidence_run。
    doc = {
        "slug": "x",
        "app_version": "9.02.10",
        "entries": [],
        "capabilities": [
            {
                "key": "cap.industry_panel",
                "supported": False,
                "note": "个股分时无行业板块",
                "last_verified": "2026-08-10",
                "app_version": "9.02.10",
                "evidence_run": "2026-08-10-test-cases",
                "status": "verified",
            },
        ],
        "verified_chains": [],
    }
    from tools.contracts.validate import validate
    assert validate(doc, "profile") == []
