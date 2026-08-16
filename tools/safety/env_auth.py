def _parse_ver(v):
    return tuple(int(x) for x in str(v).split("."))

def version_in_range(v, rng):
    return _parse_ver(rng["min"]) <= _parse_ver(v) < _parse_ver(rng["max_exclusive"])

# 占位署名黑名单：空/这些名/以这些前缀开头都视为「未真正认证」（P0：执行程序不得自签/伪造）。
_PLACEHOLDER_ATTESTORS = {"PENDING_OPERATOR_ATTESTATION", "EXAMPLE-OPERATOR"}
_PLACEHOLDER_PREFIXES = ("PENDING_", "EXAMPLE-")

def _is_placeholder_attestor(name):
    if not name:                                  # 空/None → 未署名，视为占位
        return True
    return name in _PLACEHOLDER_ATTESTORS or name.startswith(_PLACEHOLDER_PREFIXES)

# 严格身份路径的认证级别（休眠，供将来测真账户/生产用）。
_STRICT_ASSURANCE = ("operator_attested", "technical_verified")

def verify_env(env, device_pkg, device_version, now_iso, integrity_ok):
    """环境认证 → mode 推导。

    两条路径：
    - `trusted_internal`（**团队内自测默认**）：跑在已知模拟盘上，声明即信任——只做"未撤销 ∧
      是模拟盘 ∧ 测对 app/版本"的基础卫生，**不要求 HMAC 签名/真实署名/有效期**。这是把过度的
      身份识别仪式旁路掉后的轻量档。
    - `operator_attested`/`technical_verified`（**严格路径，休眠**）：将来若驱动真实账户/生产，
      才需 HMAC 签名 + 真实署名（非占位）+ 有效期 + integrity_ok。逻辑保留、随时可启。

    任一档不满足 → 回退 `confirm_only`（安全默认）。
    """
    ev = env.get("evidence", {})
    # 与身份无关的基础卫生（两档共用）
    base = []
    if env.get("revoked"): base.append("revoked")
    if env.get("type") != "simulation": base.append("not_simulation")
    if device_pkg != ev.get("package"): base.append("package_mismatch")
    vr = ev.get("version_range") or {}
    try:
        _in = version_in_range(device_version, vr)
    except (KeyError, ValueError, TypeError):
        _in = False
    if not _in:
        base.append("version_out_of_range")

    if env.get("assurance_level") == "trusted_internal":
        # 轻量旁路：不校验签名/署名/有效期。
        reasons = list(base)
        return ("simulated_submit" if not reasons else "confirm_only"), reasons

    # 严格身份路径（休眠）
    reasons = list(base)
    if not integrity_ok: reasons.append("integrity_failed")
    if now_iso > ev.get("valid_until", ""): reasons.append("expired")
    if env.get("assurance_level") not in _STRICT_ASSURANCE:
        reasons.append("bad_assurance_level")
    if _is_placeholder_attestor(ev.get("attested_by")):
        # 占位/空署名不得达 simulated_submit（P0「不得自签」，见 .secrets/README.md）。
        reasons.append("attestation_placeholder")
    return ("simulated_submit" if not reasons else "confirm_only"), reasons
