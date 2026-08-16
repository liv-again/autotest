import subprocess
import sys

from tools.contracts.validate import load_and_validate
from tools.prereq_extract import main, run


def _write_cases(tmp_path, body):
    p = tmp_path / "cases.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_cli_help_runs_as_script():
    # 作为脚本直接运行须能 import tools.*(顶部注入仓根),不再 ModuleNotFoundError
    r = subprocess.run([sys.executable, "tools/prereq_extract.py", "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_cli_main_runs_on_fixture(tmp_path):
    rc = main(["--cases", "tests/fixtures/guojin_728_cases.yaml",
               "--rules", "tools/prereq_rules.yaml",
               "--out-yaml", str(tmp_path / "o.yaml"),
               "--out-md", str(tmp_path / "o.md")])
    assert rc == 0


def test_cli_produces_valid_yaml_and_md(tmp_path):
    cases = _write_cases(
        tmp_path,
        "cases:\n"
        "  - {tc_id: TC-001, title: 北交所ETF 限价买入}\n"
        "  - {tc_id: TC-010, title: 融资买入 北交所ETF}\n",
    )
    out_yaml = tmp_path / "本轮前置.yaml"
    out_md = tmp_path / "本轮前置.md"
    req = run(str(cases), "tools/prereq_rules.yaml", str(out_yaml), str(out_md))
    # 结构断言:产出过 schema + summary 形状
    _, errs = load_and_validate(str(out_yaml), "prereq_request")
    assert errs == []
    assert req["summary"]["identified"] >= 1
    assert "missing_codes" in req["summary"]
    md = out_md.read_text(encoding="utf-8")
    assert "TC-010" in md and "⚠️缺码" in md   # 无 prerequisites → 缺码高亮
    assert "未识别" in md and "冲突" in md      # 分区标题都在


def test_no_prereq_case_not_flagged_missing(tmp_path):
    cases = _write_cases(
        tmp_path,
        "cases:\n"
        "  - {tc_id: TC-001, title: 北交所ETF 限价买入}\n"   # no_prereq
        "  - {tc_id: TC-010, title: 融资买入 北交所ETF}\n",  # positive
    )
    req = run(str(cases), "tools/prereq_rules.yaml",
              str(tmp_path / "o.yaml"), str(tmp_path / "o.md"))
    missing = req["summary"]["missing_codes"]
    assert "TC-001" not in missing          # no_prereq 永不缺码
    assert "TC-010" in missing              # positive 且无可解析码 → 缺码


def test_positive_case_without_code_flagged_missing(tmp_path):
    cases = _write_cases(
        tmp_path,
        "cases:\n"
        "  - {tc_id: TC-005, title: IOPV 线 溢价率 字段显示}\n"   # positive: 需 has_nav
        "  - {tc_id: TC-003, title: 北交所ETF 限价卖出}\n",       # positive: 需 has_holding
    )
    prereq = tmp_path / "prerequisites.yaml"
    prereq.write_text(   # 只提供满足 has_nav 的码,不提供 has_holding 的码
        "known_codes:\n"
        "  - {code: '950001', name: 测试2, attributes: {market: 北交所, product: ETF, has_nav: true}}\n",
        encoding="utf-8",
    )
    req = run(str(cases), "tools/prereq_rules.yaml",
              str(tmp_path / "o.yaml"), str(tmp_path / "o.md"),
              prerequisites_path=str(prereq))
    by = {c["tc_id"]: c for c in req["cases"]}
    assert by["TC-005"]["needed_codes"] == ["950001"]     # 属性子集命中
    assert "TC-005" not in req["summary"]["missing_codes"]
    assert by["TC-003"]["needed_codes"] == []             # 无满足 has_holding 的码
    assert "TC-003" in req["summary"]["missing_codes"]    # positive 且无可解析码 → 缺码
