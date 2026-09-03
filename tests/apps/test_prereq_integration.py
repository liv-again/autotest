"""端到端集成：用**真实** apps/guojin/prerequisites.yaml 跑 prereq_extract 的 needed_codes 解析，
证明 Plan 2 引擎（requires.instrument 词表）↔ Plan 3 数据（known_codes.attributes）契约打通——
即引擎解析器只读 attributes、按属性子集匹配时，真实数据能对正向用例解析出对应码，
而非（修复前那样）market/product 在顶层、holding/collateral 用了引擎不认的键名 → 静默全部缺码。
"""
import pathlib

from tools.prereq_extract import run

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREREQ = ROOT / "apps/guojin/prerequisites.yaml"
RULES = ROOT / "tools/prereq_rules.yaml"


def _run(tmp_path, cases_body):
    cases = tmp_path / "cases.yaml"
    cases.write_text(cases_body, encoding="utf-8")
    req = run(str(cases), str(RULES),
              str(tmp_path / "o.yaml"), str(tmp_path / "o.md"),
              prerequisites_path=str(PREREQ))
    return {c["tc_id"]: c for c in req["cases"]}, req


def test_real_prereq_resolves_positive_cases(tmp_path):
    by, req = _run(
        tmp_path,
        "cases:\n"
        "  - {tc_id: TC-SELL, title: 北交所ETF 限价卖出, keywords: [可卖持仓]}\n"   # has_holding
        "  - {tc_id: TC-IOPV, title: IOPV 线 溢价率 字段显示}\n"                     # has_nav
        "  - {tc_id: TC-COLL, title: 担保品买入 北交所ETF}\n",                       # collateral_eligible
    )
    # 引擎↔数据打通：正向用例真能对真实 prerequisites.yaml 解析出对应码（修复前全部为空/缺码）。
    assert by["TC-SELL"]["needed_codes"] == ["950015"], by["TC-SELL"]
    assert by["TC-IOPV"]["needed_codes"] == ["950001"], by["TC-IOPV"]
    assert set(by["TC-COLL"]["needed_codes"]) == {"950022", "950025", "950027"}, by["TC-COLL"]
    # 这三条都不该落进缺码（否则说明契约仍未对齐）。
    for tc in ("TC-SELL", "TC-IOPV", "TC-COLL"):
        assert tc not in req["summary"]["missing_codes"], req["summary"]["missing_codes"]


def test_real_prereq_exercises_financing_eligible_attribute(tmp_path):
    # 负向属性用例：非融资标的（financing_eligible:false）须能解析出北交所非融资码，
    # 直接检验 financing_eligible 键已进 attributes 并被引擎读到（否则该用例静默缺码）。
    by, _ = _run(
        tmp_path,
        "cases:\n"
        "  - {tc_id: TC-NOFIN, title: 非融资标的 不可融资买入}\n",
    )
    codes = set(by["TC-NOFIN"]["needed_codes"])
    assert "950025" in codes, by["TC-NOFIN"]        # 950025 明确 non_financing
    assert codes, "financing_eligible:false 应解析出北交所非融资码，说明 financing_eligible 已入 attributes 词表"


def test_stock_special_status_rules_resolve_normal_and_require_marked_status(tmp_path):
    by, req = _run(
        tmp_path,
        "cases:\n"
        "  - {tc_id: TC-RISK, title: 风险警示股票买入}\n"
        "  - {tc_id: TC-DELIST, title: 退市整理股票买入}\n"
        "  - {tc_id: TC-NORMAL, title: 普通股票买入}\n",
    )
    assert by["TC-RISK"]["required_instruments"][0]["special_status"] == "风险警示"
    assert by["TC-DELIST"]["required_instruments"][0]["special_status"] == "退市整理"
    assert by["TC-NORMAL"]["required_instruments"][0]["special_status"] == "普通"
    assert all(by[tc]["status"] == "identified" for tc in ("TC-RISK", "TC-DELIST", "TC-NORMAL"))
    assert by["TC-NORMAL"]["needed_codes"] == ["430019"]
    assert {"TC-RISK", "TC-DELIST"} <= set(req["summary"]["missing_codes"])
