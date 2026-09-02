from tools.contracts.validate import load_and_validate
import pathlib


P = pathlib.Path(__file__).resolve().parents[2] / "apps/zhongyou/prerequisites.yaml"


def test_zhongyou_stock_special_status_codes():
    doc, errs = load_and_validate(P, "prerequisites")
    assert errs == []
    by_code = {c["code"]: c for c in doc["known_codes"]}
    assert by_code["920001"]["attributes"]["special_status"] == "风险警示"
    assert by_code["920002"]["attributes"]["special_status"] == "退市整理"
    assert set(by_code) == {"920001", "920002"}
