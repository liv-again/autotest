from tools.contracts.validate import load_and_validate
from tools.safety.env_auth import verify_env
from tools.safety import env_sign
import pathlib, yaml
ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = "com.hexin.plat.android.GuoJinZXGSecurity"

def _load(p): return yaml.safe_load((ROOT / p).read_text(encoding="utf-8"))

def test_shipped_env_trusted_internal_reaches_simulated_submit():
    env = _load("apps/guojin/env.yaml")
    # 团队内轻量档 trusted_internal：未撤销∧模拟盘∧版本命中 → simulated_submit；
    # 不依赖签名，故 integrity_ok=False 也应通过（旁路掉身份仪式）。
    mode, reasons = verify_env(env, PKG, "8.05.001", "2026-07-30", False)
    assert mode == "simulated_submit", reasons

def test_trusted_internal_revoked_falls_back():
    # revoked 仍是有效的一键锁死开关。
    env = _load("apps/guojin/env.yaml")
    env["revoked"] = True
    mode, reasons = verify_env(env, PKG, "8.05.001", "2026-07-30", True)
    assert mode == "confirm_only" and "revoked" in reasons

def test_trusted_internal_wrong_version_falls_back():
    # 与身份无关的基础卫生（测对 app/版本）在轻量档下仍生效。
    env = _load("apps/guojin/env.yaml")
    mode, reasons = verify_env(env, PKG, "9.99.999", "2026-07-30", True)
    assert mode == "confirm_only" and "version_out_of_range" in reasons

def test_example_attestation_placeholder_falls_back_to_confirm_only():
    env = _load("apps/guojin/env.yaml.example")
    # 终审 Important：样例 attested_by=EXAMPLE-OPERATOR 为占位名，即便形状/日期/版本都对，
    # verify_env 也必须判 attestation_placeholder 回退 confirm_only（守住 P0：占位不得达 simulated_submit）。
    mode, reasons = verify_env(env, PKG, "8.05.001", "2026-08-01", True)
    assert mode == "confirm_only"
    assert "attestation_placeholder" in reasons

def test_real_attested_name_reaches_simulated_submit():
    # happy-path：真实署名 + 未撤销 + 未过期 + 版本命中 + integrity_ok → 正路仍通 simulated_submit。
    env = {
        "type": "simulation",
        "assurance_level": "operator_attested",
        "revoked": False,
        "evidence": {
            "attested_by": "ops-shenjie",
            "attested_at": "2026-07-01",
            "valid_until": "2099-12-31",
            "package": PKG,
            "version_range": {"min": "8.05.001", "max_exclusive": "8.06.000"},
        },
    }
    mode, reasons = verify_env(env, PKG, "8.05.001", "2026-08-01", True)
    assert mode == "simulated_submit", reasons

def test_env_sign_roundtrip():
    key = b"test-key-32bytes-xxxxxxxxxxxxxxxx"
    sig = env_sign.sign(ROOT / "apps/guojin/env.yaml.example", key)
    assert isinstance(sig, str) and len(sig) == 64

def test_app_yaml_passes_schema_and_is_masked(forbidden_full_accounts):
    doc, errs = load_and_validate(ROOT / "apps/guojin/app.yaml", "app")
    assert errs == []
    txt = (ROOT / "apps/guojin/app.yaml").read_text(encoding="utf-8")
    for full in forbidden_full_accounts:
        assert full not in txt  # 仓内不得出现完整账号
