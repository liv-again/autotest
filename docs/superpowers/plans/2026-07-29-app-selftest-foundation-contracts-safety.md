# App 自测 · 契约 schema + 交易安全基础 Implementation Plan (Plan 1 / §8 步骤 1–2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立自测项目的数据契约（YAML/JSON Schema 校验）与交易安全基础（环境认证 / non_marketable 程序化判定 / submit-guard 硬校验 / 恢复状态机 / 秘密隔离），全部为纯逻辑 + pytest 单测，不接设备。

**Architecture:** 契约用 JSON Schema 文件声明、`tools/contracts/validate.py` 统一加载校验。安全层是**纯函数**（输入=已由 droid 适配器抽取的下单页字段/行情/账户 HMAC，输出=放行/拒绝+原因），设备 I/O 是后续计划的薄适配器，本计划只做可单测的判定逻辑。测试驱动（先写失败测试→最小实现→通过→提交）。

**Tech Stack:** Python 3.14；`PyYAML`（YAML 解析）、`jsonschema`（契约校验）、`pytest`（测试）；stdlib `hashlib`/`hmac`（HMAC）、`subprocess`（git 未提交检测）、`datetime`。

## Global Constraints

- **提交严格走项目 git-commit skill**：分支 `claude-branch-setup`（非 fix 分支 → 禁用 `fix`）；前缀 `302968 <type>`；**Test 文件必须与非 Test 文件拆到不同批次单独提交**（Test 批前缀 `302968 test`、Co-Authored-By 第三段 `test`；非 Test 批前缀 `302968 feat`、第三段 `code`）；每批 `Co-Authored-By: Claude Code | claude-opus-4-8 | <code|test>`；显式 `git add <本批文件>`，**禁用 `git add .`**；每批 ≤4000 行且 ≤40 文件。
- **秘密隔离**：完整账号**绝不入仓**；仓内只 `alias`+类型+脱敏尾号；`.secrets/` 与 `runs/**/snapshots/` 一律 git-ignore；`run.yaml` 不记完整账号/资产。
- **交易安全默认**：仅当 `env.yaml` operator_attested 逐项校验通过时 `mode=simulated_submit`；否则一律 `confirm_only`（不点最终确认）。`mode=confirm_only` 时 submit-guard 恒拒最终提交。
- **撤单成功终态** = `已撤` 或 `部撤`（测试环境两者皆成功，不区分）。
- **`non_marketable` 是程序化判定**（非语义标签）：买价 < 卖1（或无卖盘时 = 跌停）∧ 卖价 > 买1（或无买盘时 = 涨停）∧ 行情新鲜（≤ max_staleness）。
- **执行程序不得自签/伪造 `env.yaml`**：校验其 HMAC（`.secrets` 本地密钥）或拒绝使用工作区未提交变更。
- **BLOCKED_ENVIRONMENT** 状态独立、不计入通过率（本计划仅在 schema 中预留该枚举值；计分逻辑在 Plan 3）。

---

### Task 1: 契约校验基础设施 + env / app schema

**Files:**
- Create: `tools/contracts/__init__.py`
- Create: `tools/contracts/validate.py`
- Create: `tools/contracts/schemas/env.schema.json`
- Create: `tools/contracts/schemas/app.schema.json`
- Test: `tests/contracts/test_validate.py`
- Create: `tests/fixtures/env_valid.yaml`, `tests/fixtures/env_invalid.yaml`

**Interfaces:**
- Produces: `validate(doc: dict, schema_name: str) -> list[str]`（返回错误消息列表，空=通过）；`load_and_validate(path: str, schema_name: str) -> tuple[dict, list[str]]`。schema_name ∈ 文件名去 `.schema.json`（如 `"env"`）。

- [ ] **Step 1: 安装依赖并建包骨架**

Run: `python -m pip install PyYAML jsonschema pytest`
Create empty `tools/contracts/__init__.py`; create `tests/contracts/__init__.py` (empty).

- [ ] **Step 2: 写失败测试**

```python
# tests/contracts/test_validate.py
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
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/contracts/test_validate.py -v`
Expected: FAIL（`ModuleNotFoundError: tools.contracts.validate`）

- [ ] **Step 4: 写 env / app schema**

```json
// tools/contracts/schemas/env.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["type", "assurance_level", "evidence", "revoked"],
  "properties": {
    "type": {"enum": ["simulation", "live", "unknown"]},
    "assurance_level": {"enum": ["operator_attested", "technical_verified"]},
    "revoked": {"type": "boolean"},
    "evidence": {
      "type": "object",
      "required": ["attested_by", "attested_at", "valid_until", "package", "version_range", "account_aliases"],
      "properties": {
        "attested_by": {"type": "string"},
        "attested_at": {"type": "string"},
        "valid_until": {"type": "string"},
        "package": {"type": "string"},
        "version_range": {
          "type": "object",
          "required": ["min", "max_exclusive"],
          "properties": {"min": {"type": "string"}, "max_exclusive": {"type": "string"}}
        },
        "account_aliases": {"type": "array", "items": {"type": "string"}}
      }
    }
  }
}
```

```json
// tools/contracts/schemas/app.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["slug", "packages", "verified_versions", "compatibility", "test_accounts"],
  "properties": {
    "slug": {"type": "string"},
    "aliases": {"type": "array", "items": {"type": "string"}},
    "packages": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    "verified_versions": {"type": "array", "items": {"type": "object", "required": ["version", "verified_at"]}},
    "compatibility": {"type": "object", "required": ["min", "max_exclusive"]},
    "test_accounts": {"type": "array", "items": {"type": "object", "required": ["alias", "type", "mask"]}},
    "environment": {"type": "string"},
    "profile": {"type": "string"},
    "prerequisites": {"type": "string"}
  }
}
```

- [ ] **Step 5: 写 validate.py 最小实现**

```python
# tools/contracts/validate.py
import json, os
import yaml
from jsonschema import Draft202012Validator

_SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schemas")

def _load_schema(name):
    p = os.path.join(_SCHEMA_DIR, f"{name}.schema.json")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"no schema: {name}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def validate(doc, schema_name):
    v = Draft202012Validator(_load_schema(schema_name))
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in sorted(v.iter_errors(doc), key=lambda e: list(e.path))]

def load_and_validate(path, schema_name):
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return doc, validate(doc, schema_name)
```

- [ ] **Step 6: 写 fixtures**

```yaml
# tests/fixtures/env_valid.yaml
type: simulation
assurance_level: operator_attested
revoked: false
evidence:
  attested_by: shenjie
  attested_at: "2026-07-29"
  valid_until: "2026-10-29"
  package: com.hexin.plat.android.GuoJinZXGSecurity
  version_range: {min: "8.05.001", max_exclusive: "8.06.000"}
  account_aliases: [pt, xy]
```
```yaml
# tests/fixtures/env_invalid.yaml  (缺 evidence.package)
type: simulation
assurance_level: operator_attested
revoked: false
evidence:
  attested_by: shenjie
  attested_at: "2026-07-29"
  valid_until: "2026-10-29"
  version_range: {min: "8.05.001", max_exclusive: "8.06.000"}
  account_aliases: [pt, xy]
```

- [ ] **Step 7: 运行测试确认通过**

Run: `python -m pytest tests/contracts/test_validate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 8: 提交（Test 与非 Test 分两次）**

```bash
git add tools/contracts/__init__.py tools/contracts/validate.py tools/contracts/schemas/env.schema.json tools/contracts/schemas/app.schema.json tests/fixtures/env_valid.yaml tests/fixtures/env_invalid.yaml
git commit -m "302968 feat 契约校验基础设施+env/app schema" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | code"
git add tests/contracts/__init__.py tests/contracts/test_validate.py
git commit -m "302968 test 契约校验单测" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | test"
```

---

### Task 2: selection / run / 本轮安全约束 schema

**Files:**
- Create: `tools/contracts/schemas/selection.schema.json`
- Create: `tools/contracts/schemas/run.schema.json`
- Create: `tools/contracts/schemas/safety_constraint.schema.json`
- Test: `tests/contracts/test_schemas.py`
- Create: fixtures `tests/fixtures/{selection,run,safety_constraint}_{valid,invalid}.yaml`

**Interfaces:**
- Consumes: `validate` from Task 1.
- Produces: 三个可校验 schema；`safety_constraint` 含 `mode ∈ {confirm_only, simulated_submit}`（**不含 live_submit——首版不实现执行路径**）、`code_allowlist`、`qty_max`、`price_rule`、`account_allowlist_hmac`、`constraint_hash`、`source_selection_hash`、`expires_at`、`env_ref`。

- [ ] **Step 1: 写失败测试（表驱动，good+bad 各一 fixture）**

```python
# tests/contracts/test_schemas.py
import os, pytest
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
    assert errs  # mode=live_submit 不在枚举
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/contracts/test_schemas.py -v`
Expected: FAIL（schema 文件不存在 → ValidationError/FileNotFoundError）

- [ ] **Step 3: 写 selection.schema.json**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["selected_ids", "skipped_ids", "incidental_ids", "priority_source", "scope_hash"],
  "properties": {
    "selected_ids": {"type": "array", "items": {"type": "string"}},
    "skipped_ids": {"type": "array", "items": {"type": "object", "required": ["id", "reason"]}},
    "incidental_ids": {"type": "array", "items": {"type": "string"}},
    "priority_source": {"type": "string"},
    "scope_hash": {"type": "string"}
  }
}
```

- [ ] **Step 4: 写 run.schema.json**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["input_excel", "app", "device", "git_commit", "prereq_rules", "mode", "status"],
  "properties": {
    "input_excel": {"type": "object", "required": ["path", "sha256"]},
    "app": {"type": "object", "required": ["package", "version", "versionCode", "apk_sha256"]},
    "device": {"type": "object", "required": ["serial", "os", "resolution"]},
    "git_commit": {"type": "string"},
    "prereq_rules": {"type": "object", "required": ["version", "sha256"]},
    "selected_case_ids": {"type": "array", "items": {"type": "string"}},
    "skipped_case_ids": {"type": "array", "items": {"type": "string"}},
    "mode": {"enum": ["confirm_only", "simulated_submit"]},
    "safety_constraint_hash": {"type": "string"},
    "start_time": {"type": "string"}, "end_time": {"type": "string"},
    "status": {"enum": ["running", "done", "aborted"]},
    "recovery_point": {"type": ["string", "null"]}
  }
}
```

- [ ] **Step 5: 写 safety_constraint.schema.json**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["mode", "env_ref", "code_allowlist", "qty_max", "price_rule",
               "account_allowlist_hmac", "constraint_hash", "source_selection_hash", "expires_at"],
  "properties": {
    "mode": {"enum": ["confirm_only", "simulated_submit"]},
    "env_ref": {"type": "string"},
    "code_allowlist": {"type": "array", "items": {"type": "string"}},
    "qty_max": {"type": "integer", "minimum": 1},
    "price_rule": {"enum": ["non_marketable"]},
    "account_allowlist_hmac": {"type": "array", "items": {"type": "string"}},
    "constraint_hash": {"type": "string"},
    "source_selection_hash": {"type": "string"},
    "expires_at": {"type": "string"}
  }
}
```

- [ ] **Step 6: 写 fixtures**（每 schema 一 valid + 一 invalid；另加 `safety_constraint_live.yaml`，`mode: live_submit`）

各 valid fixture 按上面 required 字段填齐真实样例值；invalid fixture 各删一个 required 字段。`safety_constraint_live.yaml` 用完整合法字段但 `mode: live_submit`。

- [ ] **Step 7: 运行确认通过**

Run: `python -m pytest tests/contracts/test_schemas.py -v`
Expected: PASS

- [ ] **Step 8: 提交（分两次）**

```bash
git add tools/contracts/schemas/selection.schema.json tools/contracts/schemas/run.schema.json tools/contracts/schemas/safety_constraint.schema.json tests/fixtures/selection_valid.yaml tests/fixtures/selection_invalid.yaml tests/fixtures/run_valid.yaml tests/fixtures/run_invalid.yaml tests/fixtures/safety_constraint_valid.yaml tests/fixtures/safety_constraint_invalid.yaml tests/fixtures/safety_constraint_live.yaml
git commit -m "302968 feat selection/run/安全约束 schema" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | code"
git add tests/contracts/test_schemas.py
git commit -m "302968 test selection/run/安全约束 schema 单测" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | test"
```

---

### Task 3: 秘密与认证完整性 `secrets.py`

**Files:**
- Create: `tools/safety/__init__.py`
- Create: `tools/safety/secrets.py`
- Test: `tests/safety/test_secrets.py`
- Modify: `.gitignore`（新建或追加）

**Interfaces:**
- Produces:
  - `account_hmac(account_no: str, key: bytes) -> str`（hex）
  - `env_integrity_ok(env_yaml_path: str, sig_path: str, key: bytes) -> bool`（HMAC 校验 env 正文）
  - `is_git_committed(path: str) -> bool`（工作区无未提交变更；用于"拒用未提交认证"降级路径）

- [ ] **Step 1: 写失败测试**

```python
# tests/safety/test_secrets.py
from tools.safety.secrets import account_hmac, env_integrity_ok
import os, hmac, hashlib, tempfile

KEY = b"test-key"

def test_account_hmac_stable_and_secret():
    h = account_hmac("***5183", KEY)
    assert h == hmac.new(KEY, b"***5183", hashlib.sha256).hexdigest()
    assert "***5183" not in h

def test_env_integrity_detects_tamper(tmp_path):
    env = tmp_path / "env.yaml"; env.write_text("type: simulation\n", encoding="utf-8")
    sig = tmp_path / "env.sig"
    good = hmac.new(KEY, env.read_bytes(), hashlib.sha256).hexdigest()
    sig.write_text(good, encoding="utf-8")
    assert env_integrity_ok(str(env), str(sig), KEY) is True
    env.write_text("type: simulation\nrevoked: false\n", encoding="utf-8")  # tamper
    assert env_integrity_ok(str(env), str(sig), KEY) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/safety/test_secrets.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现**

```python
# tools/safety/secrets.py
import hmac, hashlib, subprocess

def account_hmac(account_no, key):
    return hmac.new(key, account_no.encode("utf-8"), hashlib.sha256).hexdigest()

def env_integrity_ok(env_yaml_path, sig_path, key):
    with open(env_yaml_path, "rb") as f:
        body = f.read()
    expect = hmac.new(key, body, hashlib.sha256).hexdigest()
    try:
        with open(sig_path, encoding="utf-8") as f:
            got = f.read().strip()
    except FileNotFoundError:
        return False
    return hmac.compare_digest(expect, got)

def is_git_committed(path):
    out = subprocess.run(["git", "status", "--porcelain", "--", path],
                         capture_output=True, text=True).stdout.strip()
    return out == ""
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/safety/test_secrets.py -v`
Expected: PASS

- [ ] **Step 5: 写 `.gitignore`**

```gitignore
.secrets/
runs/**/snapshots/
*.private.png
```

- [ ] **Step 6: 提交（分两次）**

```bash
git add tools/safety/__init__.py tools/safety/secrets.py .gitignore
git commit -m "302968 feat 秘密隔离+认证HMAC完整性" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | code"
git add tests/safety/__init__.py tests/safety/test_secrets.py
git commit -m "302968 test secrets 单测" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | test"
```
（`tests/safety/__init__.py` 为空文件）

---

### Task 4: `non_marketable` 程序化判定

**Files:**
- Create: `tools/safety/non_marketable.py`
- Test: `tests/safety/test_non_marketable.py`

**Interfaces:**
- Produces: `check_non_marketable(price, side, quote, up_limit, down_limit, max_staleness_s, quote_ts, now_ts) -> tuple[bool, str]`。`side ∈ {"buy","sell"}`；`quote={"ask1": float|None, "bid1": float|None}`；返回 `(ok, reason)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/safety/test_non_marketable.py
from tools.safety.non_marketable import check_non_marketable as chk

Q = {"ask1": 96.200, "bid1": None}

def test_buy_below_ask_ok():
    ok, _ = chk(67.343, "buy", Q, up_limit=125.063, down_limit=67.343, max_staleness_s=5, quote_ts=100, now_ts=101)
    assert ok

def test_buy_at_or_above_ask_rejected():
    ok, r = chk(96.200, "buy", Q, 125.063, 67.343, 5, 100, 101)
    assert not ok and "ask" in r

def test_sell_above_bid_ok():
    ok, _ = chk(125.063, "sell", {"ask1": None, "bid1": 96.0}, 125.063, 67.343, 5, 100, 101)
    assert ok

def test_no_ask_requires_down_limit():
    ok, _ = chk(67.343, "buy", {"ask1": None, "bid1": None}, 125.063, 67.343, 5, 100, 101)
    assert ok
    ok2, r = chk(80.0, "buy", {"ask1": None, "bid1": None}, 125.063, 67.343, 5, 100, 101)
    assert not ok2 and "down_limit" in r

def test_stale_quote_rejected():
    ok, r = chk(67.343, "buy", Q, 125.063, 67.343, 5, 100, 110)
    assert not ok and "stale" in r
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/safety/test_non_marketable.py -v`
Expected: FAIL

- [ ] **Step 3: 写实现**

```python
# tools/safety/non_marketable.py
def check_non_marketable(price, side, quote, up_limit, down_limit, max_staleness_s, quote_ts, now_ts):
    if now_ts - quote_ts > max_staleness_s:
        return False, "quote_stale"
    if side == "buy":
        ask1 = quote.get("ask1")
        if ask1 is not None:
            return (price < ask1, "ok" if price < ask1 else "buy_price>=ask1")
        return (price == down_limit, "ok" if price == down_limit else "no_ask_and_not_down_limit")
    if side == "sell":
        bid1 = quote.get("bid1")
        if bid1 is not None:
            return (price > bid1, "ok" if price > bid1 else "sell_price<=bid1")
        return (price == up_limit, "ok" if price == up_limit else "no_bid_and_not_up_limit")
    return False, "bad_side"
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/safety/test_non_marketable.py -v`
Expected: PASS

- [ ] **Step 5: 提交（分两次）**

```bash
git add tools/safety/non_marketable.py
git commit -m "302968 feat non_marketable 程序化判定" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | code"
git add tests/safety/test_non_marketable.py
git commit -m "302968 test non_marketable 单测" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | test"
```

---

### Task 5: 环境认证 → mode 推导 `env_auth.py`

**Files:**
- Create: `tools/safety/env_auth.py`
- Test: `tests/safety/test_env_auth.py`

**Interfaces:**
- Consumes: 校验过的 env dict（Task 1 schema）。
- Produces: `verify_env(env, device_pkg, device_version, now_iso, integrity_ok) -> tuple[str, list[str]]` 返回 `(mode, reasons)`，`mode ∈ {"simulated_submit","confirm_only"}`。含 `version_in_range(v, rng) -> bool`（点分数值比较，`min ≤ v < max_exclusive`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/safety/test_env_auth.py
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/safety/test_env_auth.py -v`
Expected: FAIL

- [ ] **Step 3: 写实现**

```python
# tools/safety/env_auth.py
def _parse_ver(v):
    return tuple(int(x) for x in str(v).split("."))

def version_in_range(v, rng):
    return _parse_ver(rng["min"]) <= _parse_ver(v) < _parse_ver(rng["max_exclusive"])

def verify_env(env, device_pkg, device_version, now_iso, integrity_ok):
    reasons = []
    if not integrity_ok: reasons.append("integrity_failed")
    if env.get("revoked"): reasons.append("revoked")
    if env.get("type") != "simulation": reasons.append("not_simulation")
    ev = env.get("evidence", {})
    if device_pkg != ev.get("package"): reasons.append("package_mismatch")
    if not version_in_range(device_version, ev["version_range"]): reasons.append("version_out_of_range")
    if now_iso > ev.get("valid_until", ""): reasons.append("expired")
    if env.get("assurance_level") not in ("operator_attested", "technical_verified"):
        reasons.append("bad_assurance_level")
    mode = "simulated_submit" if not reasons else "confirm_only"
    return mode, reasons
```
（说明：`valid_until` 用 ISO 日期串，字典序比较对 `YYYY-MM-DD` 等价于时间序，安全。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/safety/test_env_auth.py -v`
Expected: PASS

- [ ] **Step 5: 提交（分两次）**

```bash
git add tools/safety/env_auth.py
git commit -m "302968 feat 环境认证→mode推导+版本区间" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | code"
git add tests/safety/test_env_auth.py
git commit -m "302968 test env_auth 单测" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | test"
```

---

### Task 6: submit-guard 硬校验（最终提交前）

**Files:**
- Create: `tools/safety/submit_guard.py`
- Test: `tests/safety/test_submit_guard.py`

**Interfaces:**
- Consumes: `check_non_marketable`（Task 4）；本轮安全约束 dict（Task 2 schema）。
- Produces: `guard_submit(order, account_hmac, constraint, quote_ctx, now_ts) -> tuple[bool, list[str]]`。`order={"code","price","qty","side"}`（下单页可 dump 字段）；`quote_ctx={"ask1","bid1","up_limit","down_limit","max_staleness_s","quote_ts"}`。`account_hmac` 由调用方用 `secrets.account_hmac` 预算。

- [ ] **Step 1: 写失败测试**

```python
# tests/safety/test_submit_guard.py
from tools.safety.submit_guard import guard_submit

CONS = {"mode": "simulated_submit", "code_allowlist": ["950025"], "qty_max": 100,
        "price_rule": "non_marketable", "account_allowlist_hmac": ["HMAC_XY"]}
QC = {"ask1": 96.2, "bid1": None, "up_limit": 125.063, "down_limit": 67.343,
      "max_staleness_s": 5, "quote_ts": 100}
ORDER = {"code": "950025", "price": 67.343, "qty": 100, "side": "buy"}

def test_all_pass_allows():
    ok, r = guard_submit(ORDER, "HMAC_XY", CONS, QC, now_ts=101)
    assert ok and r == ["ok"]

def test_confirm_only_never_submits():
    ok, r = guard_submit(ORDER, "HMAC_XY", {**CONS, "mode": "confirm_only"}, QC, 101)
    assert not ok and "mode_confirm_only" in r

def test_account_not_allowed():
    ok, r = guard_submit(ORDER, "HMAC_OTHER", CONS, QC, 101)
    assert not ok and "account_not_allowed" in r

def test_code_not_allowed():
    ok, r = guard_submit({**ORDER, "code": "950015"}, "HMAC_XY", CONS, QC, 101)
    assert not ok and "code_not_allowed" in r

def test_qty_over_max():
    ok, r = guard_submit({**ORDER, "qty": 200}, "HMAC_XY", CONS, QC, 101)
    assert not ok and "qty_over_max" in r

def test_marketable_price_rejected():
    ok, r = guard_submit({**ORDER, "price": 96.2}, "HMAC_XY", CONS, QC, 101)
    assert not ok and any("price_marketable" in x for x in r)

def test_missing_field_rejected():
    ok, r = guard_submit({**ORDER, "price": None}, "HMAC_XY", CONS, QC, 101)
    assert not ok and "field_missing" in r
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/safety/test_submit_guard.py -v`
Expected: FAIL

- [ ] **Step 3: 写实现**

```python
# tools/safety/submit_guard.py
from tools.safety.non_marketable import check_non_marketable

def guard_submit(order, account_hmac, constraint, quote_ctx, now_ts):
    if constraint.get("mode") == "confirm_only":
        return False, ["mode_confirm_only"]
    for k in ("code", "price", "qty", "side"):
        if order.get(k) in (None, ""):
            return False, ["field_missing"]
    reasons = []
    if account_hmac not in constraint.get("account_allowlist_hmac", []):
        reasons.append("account_not_allowed")
    if order["code"] not in constraint.get("code_allowlist", []):
        reasons.append("code_not_allowed")
    if order["qty"] > constraint.get("qty_max", 0):
        reasons.append("qty_over_max")
    if constraint.get("price_rule") == "non_marketable":
        ok, why = check_non_marketable(order["price"], order["side"], quote_ctx,
            quote_ctx["up_limit"], quote_ctx["down_limit"],
            quote_ctx["max_staleness_s"], quote_ctx["quote_ts"], now_ts)
        if not ok:
            reasons.append("price_marketable:" + why)
    return (not reasons, reasons or ["ok"])
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/safety/test_submit_guard.py -v`
Expected: PASS

- [ ] **Step 5: 提交（分两次）**

```bash
git add tools/safety/submit_guard.py
git commit -m "302968 feat submit-guard 最终提交前硬校验" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | code"
git add tests/safety/test_submit_guard.py
git commit -m "302968 test submit_guard 单测" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | test"
```

---

### Task 7: 提交后恢复状态机 `recovery.py`

**Files:**
- Create: `tools/safety/recovery.py`
- Test: `tests/safety/test_recovery.py`

**Interfaces:**
- Produces: `plan_recovery(run_orders, today_orders, window_s=120) -> dict`。`run_orders`=本轮提交过的委托列表 `{code,side,qty,price,submit_ts,contract_no|None}`；`today_orders`=当日委托快照 `{code,side,qty,price,submit_ts,status}`（`status ∈ 已撤/部撤/已成/已报/未报/可撤...`）。返回 `{"action": "CANCEL"|"STOP", "cancel": [...], "stop_reason": str|None}`。唯一匹配的可撤委托→列入 cancel；歧义（多匹配）→ STOP。

- [ ] **Step 1: 写失败测试**

```python
# tests/safety/test_recovery.py
from tools.safety.recovery import plan_recovery

RO = {"code": "950025", "side": "buy", "qty": 100, "price": 67.343, "submit_ts": 1000, "contract_no": None}

def test_unique_cancelable_match_planned():
    today = [{"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1001,"status":"已报"}]
    out = plan_recovery([RO], today)
    assert out["action"] == "CANCEL" and len(out["cancel"]) == 1

def test_ambiguous_match_stops():
    today = [
      {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1001,"status":"已报"},
      {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1002,"status":"已报"}]
    out = plan_recovery([RO], today)
    assert out["action"] == "STOP" and "ambiguous" in out["stop_reason"]

def test_no_match_no_cancel():
    out = plan_recovery([RO], [])
    assert out["action"] == "CANCEL" and out["cancel"] == []

def test_terminal_status_not_cancelable():
    today = [{"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1001,"status":"已撤"}]
    out = plan_recovery([RO], today)
    assert out["cancel"] == []

def test_contract_no_uniquely_matches():
    ro = {**RO, "contract_no": "6"}
    today = [
      {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1001,"status":"已报","contract_no":"6"},
      {"code":"950025","side":"buy","qty":100,"price":67.343,"submit_ts":1002,"status":"已报","contract_no":"8"}]
    out = plan_recovery([ro], today)
    assert out["action"] == "CANCEL" and len(out["cancel"]) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/safety/test_recovery.py -v`
Expected: FAIL

- [ ] **Step 3: 写实现**

```python
# tools/safety/recovery.py
_CANCELABLE = {"已报", "未报", "部成", "可撤"}   # 非终态可撤；已撤/部撤/已成=终态不再撤

def _match(o, ro, window_s):
    if ro.get("contract_no") and o.get("contract_no"):
        return o["contract_no"] == ro["contract_no"]
    return (o["code"] == ro["code"] and o["side"] == ro["side"]
            and o["qty"] == ro["qty"] and abs(o["price"] - ro["price"]) < 1e-6
            and abs(o["submit_ts"] - ro["submit_ts"]) <= window_s)

def plan_recovery(run_orders, today_orders, window_s=120):
    cancel = []
    for ro in run_orders:
        matches = [o for o in today_orders if _match(o, ro, window_s)]
        if len(matches) > 1:
            return {"action": "STOP", "cancel": [], "stop_reason": f"ambiguous_match:{ro['code']}"}
        if len(matches) == 1 and matches[0]["status"] in _CANCELABLE:
            cancel.append(matches[0])
    return {"action": "CANCEL", "cancel": cancel, "stop_reason": None}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/safety/test_recovery.py -v`
Expected: PASS

- [ ] **Step 5: 提交（分两次）**

```bash
git add tools/safety/recovery.py
git commit -m "302968 feat 提交后恢复状态机(残留识别/撤销/歧义停机)" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | code"
git add tests/safety/test_recovery.py
git commit -m "302968 test recovery 单测" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | test"
```

---

### Task 8: 安全策略文档 `safety-policy.md`

**Files:**
- Create: `.claude/skills/app-selftest/references/safety-policy.md`

**Interfaces:** 无代码；把 spec §5 落成人读权威策略（三级模式、env 认证、submit-guard、撤单闭环终态=已撤/部撤、恢复机、BLOCKED_ENVIRONMENT、停止条件）。指向 `tools/safety/*` 各程序化实现。

- [ ] **Step 1: 写策略文档**

内容要点（逐条写全，引用实现文件）：
- 三级模式 `confirm_only`(默认降级) / `simulated_submit`(operator_attested 通过) / `live_submit`(首版仅 schema、不实现)。
- 环境认证校验项与自动回退（`env_auth.verify_env`）。
- submit-guard 逐笔硬校验字段与拒绝条件（`submit_guard.guard_submit` + `non_marketable`）。
- 撤单闭环成功 = `已撤`/`部撤`（不区分）+ 无本 run 可撤残留。
- 恢复状态机（`recovery.plan_recovery`，歧义→人工）。
- `BLOCKED_ENVIRONMENT` 不计入通过率。
- 停止条件清单（照 spec §5）。
- 认证防篡改（`secrets.env_integrity_ok` 或拒用未提交变更 `secrets.is_git_committed`）。

- [ ] **Step 2: 提交**

```bash
git add .claude/skills/app-selftest/references/safety-policy.md
git commit -m "302968 feat 交易安全策略文档(safety-policy)" -m "Co-Authored-By: Claude Code | claude-opus-4-8 | code"
```

---

## Self-Review（写完后自查，已执行）

**1. Spec 覆盖**：§4.1 app.yaml→Task1；§4.6 env 认证→Task1(schema)+Task5(逻辑)；§4.3 run/§4.4 selection/§4.7 本轮安全约束→Task2；§4.8 秘密隔离→Task3；§5 submit-guard→Task6、non_marketable→Task4、撤单闭环/恢复机→Task7、BLOCKED_ENVIRONMENT→schema 预留(计分在 Plan3)、认证防篡改→Task3；§5 策略文档→Task8。profile/prerequisites/prereq_rules/prereq_result 属 Plan2/3，本计划不覆盖（已在开头声明）。
**2. 占位符扫描**：无 TBD/TODO；每步有真实测试+实现代码。
**3. 类型一致**：`check_non_marketable` 签名 Task4 定义、Task6 调用一致；`verify_env`/`guard_submit`/`plan_recovery` 返回类型在各自 Interfaces 声明并被测试对齐；`account_hmac`(Task3) 供 Task6 调用方预算，接口一致。
