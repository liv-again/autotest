from tools.safety.env_auth import verify_env, version_in_range

BASE = {
  "type": "simulation", "assurance_level": "operator_attested", "revoked": False,
  "evidence": {"attested_by": "shenjie", "attested_at": "2026-07-29", "valid_until": "2026-10-29",
    "package": "com.hexin.plat.android.GuoJinZXGSecurity",
    "version_range": {"min": "8.05.001", "max_exclusive": "8.06.000"}, "account_aliases": ["pt","xy"]}}
PKG = BASE["evidence"]["package"]

def test_valid_attested_enables_simulated():
    mode, reasons = verify_env(BASE, PKG, "8.05.001", "2026-08-01", integrity_ok=True)
    assert mode == "simulated_submit" and reasons == []

def test_integrity_fail_downgrades():
    mode, reasons = verify_env(BASE, PKG, "8.05.001", "2026-08-01", integrity_ok=False)
    assert mode == "confirm_only" and "integrity_failed" in reasons

def test_expired_downgrades():
    mode, reasons = verify_env(BASE, PKG, "8.05.001", "2026-11-01", integrity_ok=True)
    assert mode == "confirm_only" and "expired" in reasons

def test_pkg_mismatch_downgrades():
    mode, reasons = verify_env(BASE, "com.other", "8.05.001", "2026-08-01", integrity_ok=True)
    assert mode == "confirm_only" and "package_mismatch" in reasons

def test_version_out_of_range_downgrades():
    mode, reasons = verify_env(BASE, PKG, "8.06.000", "2026-08-01", integrity_ok=True)
    assert mode == "confirm_only" and "version_out_of_range" in reasons

def test_revoked_downgrades():
    e = {**BASE, "revoked": True}
    mode, reasons = verify_env(e, PKG, "8.05.001", "2026-08-01", integrity_ok=True)
    assert mode == "confirm_only" and "revoked" in reasons

def test_version_in_range():
    assert version_in_range("8.05.001", {"min": "8.05.001", "max_exclusive": "8.06.000"})
    assert not version_in_range("8.06.000", {"min": "8.05.001", "max_exclusive": "8.06.000"})

def test_not_simulation_downgrades():
    e = {**BASE, "type": "live"}
    mode, reasons = verify_env(e, PKG, "8.05.001", "2026-08-01", integrity_ok=True)
    assert mode == "confirm_only" and "not_simulation" in reasons

def test_bad_assurance_level_downgrades():
    e = {**BASE, "assurance_level": "self_attested"}
    mode, reasons = verify_env(e, PKG, "8.05.001", "2026-08-01", integrity_ok=True)
    assert mode == "confirm_only" and "bad_assurance_level" in reasons

def test_malformed_env_missing_version_range_fails_closed():
    e = {**BASE, "evidence": {**BASE["evidence"]}}
    del e["evidence"]["version_range"]
    mode, reasons = verify_env(e, PKG, "8.05.001", "2026-08-01", integrity_ok=True)
    assert mode == "confirm_only" and "version_out_of_range" in reasons
