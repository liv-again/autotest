# 前置条件引擎 Spike 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建半自动"前置条件引擎"——从 Excel 用例标题(+关键词)按**带极性规则**映射出每条用例需要的标的属性/账户能力/代码（`本轮前置`），测前一次性备齐，并用国金 728 数据验收召回/误报/未识别/极性区分。

**Architecture:** 纯函数引擎 + JSON Schema 契约 + 规则表(YAML 机器权威)。`prereq_rules.yaml` 是带极性的规则表；`tools/prereq/{rules,extract,metrics}.py` 是加载/匹配/验收三个纯模块；`tools/prereq_extract.py` 是 CLI，产出 `本轮前置.yaml` + 派生 `.md`(缺码高亮)。复用 Plan 1 的 `tools/contracts/validate.py`。设备无关、可复现、TDD。

**Tech Stack:** Python 3.14 · PyYAML · jsonschema(Draft 2020-12) · pytest。复用 `tools/contracts/validate.py`(`validate(doc, schema_name)->list[str]`、`load_and_validate(path, schema_name)->(doc, errors)`；schema 目录 `tools/contracts/schemas/`)。

## Global Constraints

- **提交格式**：`302968 feat <desc>` / `302968 test <desc>`；**测试文件与实现分属不同提交**；`git add <显式文件>`(禁 `git add .`)；提交体加 `Co-Authored-By: Claude Code | claude-opus-4-8 | code`(测试批用 `| test`)。
- **schema**：新 schema 放 `tools/contracts/schemas/<name>.schema.json`，Draft 2020-12，风格对齐现有(顶层 `$schema` + `required` + `properties` + `enum`，扁平、`items` 内联，不用 `$defs`/`$ref`)；用现成 `validate.py` 校验，测试用 `{name}_valid.yaml`/`{name}_invalid.yaml` fixture。
- **测试运行**：一律从仓根 `python -m pytest`（`pyproject.toml` 已设 `pythonpath=["."]`）。Windows 读中文用 `PYTHONUTF8=1`。
- **match.none 命名映射**：`match.none`(全不含)**即实现 spec §4.5 的 `not_capability`**——"否定 = 该能力需求不存在"的**结构化否定**（非字符串 negation）。这是有意的命名决定，非偏离 spec。**注**：本实现把 spec 的 `not_capability` 收敛为**标题关键词排除**（`none` 列表里的词命中即判该规则不适用）；语义等价目标 = **避免误命中**，机制上是**文本排除**而非"能力否定"的结构化推理——spike 阶段以关键词排除近似，真实语料接入后若不足再升级为能力级判定。
- **polarity 三分**(spec §4.5)：`positive`(需属性为真) / `negative_property`(需负向属性作测试数据 + `expected_capability` 记预期失败) / `no_prereq`(显式无需专门前置)。**绝不用字符串 negation 做否定判断。**
- **召回优先于精度**：宁可多报(误报)也不漏(spec §一.3 默认只测 high 但备码要全)；未识别/冲突**必须显式列出，不静默丢弃**。
- **可追溯**：每条 identified 用例必须带 `matched_rule_ids`(tc_id→rule_id 可追溯)。
- **反哺升版**：人工修正规则时改 `prereq_rules.yaml` 并升 `version`，不是只改本轮输出（本 plan 建立结构，反哺脚本在 Plan 3）。

---

## File Structure

- `tools/contracts/schemas/prereq_rules.schema.json` — 规则表 schema（带极性）
- `tools/contracts/schemas/prereq_request.schema.json` — `本轮前置` 输出 schema
- `tools/prereq_rules.yaml` — 国金规则数据（机器权威，跨 app 用 `applies_to` 限定）
- `tools/prereq/__init__.py`
- `tools/prereq/rules.py` — 规则加载/校验/索引/过滤
- `tools/prereq/extract.py` — 核心匹配 + 极性 + 多规则优先级 + 冲突/未识别 + 追溯
- `tools/prereq/metrics.py` — 验收指标（召回/误报/未识别/人工补充/极性正确率/追溯率）
- `tools/prereq_extract.py` — CLI 入口，产出 `本轮前置.yaml` + 派生 `.md`
- `tests/fixtures/prereq_rules_valid.yaml` / `prereq_rules_invalid.yaml`
- `tests/fixtures/prereq_request_valid.yaml` / `prereq_request_invalid.yaml`
- `tests/fixtures/guojin_728_cases.yaml` — 代表性用例（spike 输入）
- `tests/fixtures/guojin_728_gold.yaml` — 期望前置（spike 金标准）
- `tests/prereq/__init__.py`
- `tests/prereq/test_rules.py` / `test_extract.py` / `test_metrics.py` / `test_cli.py`

---

## 数据模型（所有任务共用，务必字段一致）

**规则 `prereq_rules.yaml`**：
```yaml
version: 1
rules:
  - id: basic-bjse-etf
    applies_to: {app: "*", market: "北交所"}
    match: {all: ["买入"], any: [], none: ["融资", "融券", "大宗"]}
    requires: {instrument: {market: "北交所", product: "ETF"}, account: "any"}
    polarity: no_prereq
    priority: 1
    confidence: high
    provenance: "国金728实测"
```

**输出 `本轮前置.yaml`(prereq_request)**：
```yaml
rules_version: 1
generated_from: {rules_sha256: "…", cases_count: 12}
cases:
  - tc_id: "TC-001"
    title: "北交所ETF 限价买入"
    matched_rule_ids: ["basic-bjse-etf"]
    status: "identified"          # identified | unidentified | conflict
    required_instruments: [{market: "北交所", product: "ETF"}]
    required_account: "any"
    polarity: "no_prereq"
    needed_codes: []              # 空 = 缺码高亮（Plan 2 CLI 用 prerequisites 解析；spike 阶段可空）
    provenance: ["国金728实测"]
unidentified: ["TC-099"]
conflicts: [{tc_id: "TC-050", rule_ids: ["a", "b"], note: "instrument.financing_eligible true vs false"}]
summary: {identified: 10, unidentified: 1, conflict: 1, missing_codes: ["TC-001"]}
```

---

### Task 1: 规则表 schema + 国金规则数据

**Files:**
- Create: `tools/contracts/schemas/prereq_rules.schema.json`
- Create: `tools/prereq_rules.yaml`
- Create: `tests/fixtures/prereq_rules_valid.yaml`, `tests/fixtures/prereq_rules_invalid.yaml`
- Create: `tests/prereq/__init__.py`, `tests/prereq/test_rules.py`（本 plan 的 prereq schema 校验单独放 `tests/prereq/test_rules.py`，不改动既有 `tests/contracts/`；此任务仅测 schema 接受/拒绝）
- Test: `tests/prereq/test_rules.py`

**Interfaces:**
- Produces: schema 名 `prereq_rules`；`tools/prereq_rules.yaml` 顶层 `{version:int, rules:[rule]}`；rule 字段见"数据模型"。

- [ ] **Step 1: 写失败测试** `tests/prereq/__init__.py`(空) 与 `tests/prereq/test_rules.py`：

```python
import os
from tools.contracts.validate import load_and_validate

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")

def test_prereq_rules_valid_passes():
    _, errs = load_and_validate(os.path.join(FIX, "prereq_rules_valid.yaml"), "prereq_rules")
    assert errs == []

def test_prereq_rules_invalid_reports():
    # 缺 polarity + bad confidence enum
    _, errs = load_and_validate(os.path.join(FIX, "prereq_rules_invalid.yaml"), "prereq_rules")
    assert errs

def test_shipped_rules_file_valid():
    _, errs = load_and_validate("tools/prereq_rules.yaml", "prereq_rules")
    assert errs == []
```

- [ ] **Step 2: 跑测试确认失败**：`PYTHONUTF8=1 python -m pytest tests/prereq/test_rules.py -q` → FAIL(no schema / no fixture)。

- [ ] **Step 3: 写 schema** `tools/contracts/schemas/prereq_rules.schema.json`：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["version", "rules"],
  "properties": {
    "version": {"type": "integer", "minimum": 1},
    "rules": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "applies_to", "match", "requires", "polarity", "priority", "confidence", "provenance"],
        "properties": {
          "id": {"type": "string", "minLength": 1},
          "applies_to": {
            "type": "object",
            "required": ["app", "market"],
            "properties": {"app": {"type": "string"}, "market": {"type": "string"}}
          },
          "match": {
            "type": "object",
            "properties": {
              "all": {"type": "array", "items": {"type": "string"}},
              "any": {"type": "array", "items": {"type": "string"}},
              "none": {"type": "array", "items": {"type": "string"}}
            }
          },
          "requires": {
            "type": "object",
            "properties": {
              "instrument": {"type": "object"},
              "account": {"enum": ["any", "普通", "信用"]}
            }
          },
          "polarity": {"enum": ["positive", "negative_property", "no_prereq"]},
          "expected_capability": {"type": "object"},
          "priority": {"type": "integer"},
          "confidence": {"enum": ["high", "medium", "low"]},
          "provenance": {"type": "string"}
        }
      }
    }
  }
}
```

- [ ] **Step 4: 写 fixtures**。`prereq_rules_valid.yaml`：含 2 条规则(一 `no_prereq`、一 `positive`)。`prereq_rules_invalid.yaml`：一条规则**缺 `polarity`** 且 `confidence: 高`(非枚举)。

- [ ] **Step 5: 写国金规则数据** `tools/prereq_rules.yaml`（据 `profiles/测试数据代码需求清单.md` 第一节 12 行 + 画像功能矩阵，至少含以下 9 条，字段完整）：

```yaml
version: 1
rules:
  - id: basic-bjse-etf
    applies_to: {app: "*", market: "北交所"}
    match: {all: ["买入"], any: [], none: ["融资", "融券", "还款", "还券", "大宗"]}
    requires: {instrument: {market: "北交所", product: "ETF"}, account: "any"}
    polarity: no_prereq
    priority: 1
    confidence: high
    provenance: "国金728实测:需求清单#1"
  - id: sell-needs-holding
    applies_to: {app: "*", market: "北交所"}
    match: {all: ["卖出"], any: [], none: ["融券", "还款", "大宗"]}
    requires: {instrument: {market: "北交所", product: "ETF", has_holding: true}, account: "any"}
    polarity: positive
    priority: 5
    confidence: high
    provenance: "国金728实测:需求清单#3(950015持仓1300)"
  - id: market-order-needs-depth
    applies_to: {app: "*", market: "北交所"}
    match: {all: ["市价"], any: [], none: []}
    requires: {instrument: {market: "北交所", product: "ETF", orderbook_depth: true}, account: "any"}
    polarity: positive
    priority: 6
    confidence: high
    provenance: "国金728实测:需求清单#4([63600]无盘口深度驳回)"
  - id: iopv-needs-nav
    applies_to: {app: "*", market: "北交所"}
    match: {all: ["IOPV"], any: [], none: []}
    requires: {instrument: {market: "北交所", product: "ETF", has_nav: true}, account: "any"}
    polarity: positive
    priority: 6
    confidence: high
    provenance: "国金728实测:需求清单#2(950001有净值,950025无)"
  - id: rz-collateral
    applies_to: {app: "*", market: "北交所"}
    match: {all: ["担保品"], any: ["买入", "卖出"], none: []}
    requires: {instrument: {market: "北交所", product: "ETF", collateral_eligible: true}, account: "信用"}
    polarity: positive
    priority: 8
    confidence: high
    provenance: "国金728实测:需求清单#5a(950025担保品)"
  - id: rz-buy-eligible
    applies_to: {app: "*", market: "北交所"}
    match: {all: ["融资买入"], any: [], none: ["非融资"]}
    requires: {instrument: {market: "北交所", product: "ETF", financing_eligible: true}, account: "信用"}
    polarity: positive
    priority: 10
    confidence: high
    provenance: "国金728实测:需求清单#5b(融资标的池)"
  - id: rz-buy-negative-instrument
    applies_to: {app: "*", market: "北交所"}
    match: {all: ["非融资标的"], any: ["不可融资", "融资买入"], none: []}
    requires: {instrument: {market: "北交所", product: "ETF", financing_eligible: false}}
    expected_capability: {financing_buy: "rejected"}
    polarity: negative_property
    priority: 12
    confidence: medium
    provenance: "国金728实测:需求清单#5b负向"
  - id: subscription-field
    applies_to: {app: "*", market: "北交所"}
    match: {all: ["认购"], any: ["认购状态", "认购起止"], none: []}
    requires: {instrument: {market: "北交所", product: "ETF", in_subscription: true}, account: "any"}
    polarity: positive
    priority: 7
    confidence: medium
    provenance: "国金728实测:需求清单#6"
  - id: no-permission-fail
    applies_to: {app: "*", market: "北交所"}
    match: {all: ["无权限"], any: [], none: []}
    requires: {account: "普通"}
    expected_capability: {委托: "rejected"}
    polarity: negative_property
    priority: 9
    confidence: medium
    provenance: "国金728实测:需求清单#7(全权限账户触发不了)"
```

- [ ] **Step 6: 跑测试确认通过**：`PYTHONUTF8=1 python -m pytest tests/prereq/test_rules.py -q`（`test_shipped_rules_file_valid` + 两个 fixture 测试全绿）。

- [ ] **Step 7: 提交**（实现与 fixture 一批，测试单独一批）：

```bash
git add tools/contracts/schemas/prereq_rules.schema.json tools/prereq_rules.yaml tests/fixtures/prereq_rules_valid.yaml tests/fixtures/prereq_rules_invalid.yaml
git commit -m "302968 feat 前置规则表schema+国金规则数据(带极性)" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | code'
git add tests/prereq/__init__.py tests/prereq/test_rules.py
git commit -m "302968 test 前置规则schema校验单测" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | test'
```

---

### Task 2: 本轮前置输出 schema

**Files:**
- Create: `tools/contracts/schemas/prereq_request.schema.json`
- Create: `tests/fixtures/prereq_request_valid.yaml`, `tests/fixtures/prereq_request_invalid.yaml`
- Test: `tests/prereq/test_rules.py`（追加 2 个测试）

**Interfaces:**
- Produces: schema 名 `prereq_request`；结构见"数据模型"输出块。`cases[].status ∈ {identified, unidentified, conflict}`；`polarity` 同规则枚举。

- [ ] **Step 1: 写失败测试**（追加到 `tests/prereq/test_rules.py`）：

```python
def test_prereq_request_valid_passes():
    _, errs = load_and_validate(os.path.join(FIX, "prereq_request_valid.yaml"), "prereq_request")
    assert errs == []

def test_prereq_request_invalid_reports():
    # cases[0].status = "maybe"(非枚举)
    _, errs = load_and_validate(os.path.join(FIX, "prereq_request_invalid.yaml"), "prereq_request")
    assert errs
```

- [ ] **Step 2: 跑测试确认失败**。

- [ ] **Step 3: 写 schema** `tools/contracts/schemas/prereq_request.schema.json`：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["rules_version", "cases", "unidentified", "conflicts", "summary"],
  "properties": {
    "rules_version": {"type": "integer"},
    "generated_from": {"type": "object"},
    "cases": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["tc_id", "title", "matched_rule_ids", "status", "required_instruments", "required_account", "polarity", "needed_codes"],
        "properties": {
          "tc_id": {"type": "string"},
          "title": {"type": "string"},
          "matched_rule_ids": {"type": "array", "items": {"type": "string"}},
          "status": {"enum": ["identified", "unidentified", "conflict"]},
          "required_instruments": {"type": "array", "items": {"type": "object"}},
          "required_account": {"type": "string"},
          "polarity": {"enum": ["positive", "negative_property", "no_prereq", "unknown"]},
          "needed_codes": {"type": "array", "items": {"type": "string"}},
          "provenance": {"type": "array", "items": {"type": "string"}}
        }
      }
    },
    "unidentified": {"type": "array", "items": {"type": "string"}},
    "conflicts": {"type": "array", "items": {"type": "object"}},
    "summary": {"type": "object"}
  }
}
```

- [ ] **Step 4: 写 fixtures**。`prereq_request_valid.yaml`：1 identified case + 空 unidentified/conflicts + summary。`prereq_request_invalid.yaml`：cases[0].status=`maybe`。

- [ ] **Step 5: 跑测试确认通过**。

- [ ] **Step 6: 提交**：

```bash
git add tools/contracts/schemas/prereq_request.schema.json tests/fixtures/prereq_request_valid.yaml tests/fixtures/prereq_request_invalid.yaml
git commit -m "302968 feat 本轮前置输出schema" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | code'
git add tests/prereq/test_rules.py
git commit -m "302968 test 本轮前置schema校验单测" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | test'
```

---

### Task 3: 规则加载/索引/过滤（`tools/prereq/rules.py`）

**Files:**
- Create: `tools/prereq/__init__.py`(空), `tools/prereq/rules.py`
- Test: `tests/prereq/test_rules.py`（追加）

**Interfaces:**
- Produces:
  - `load_rules(path) -> dict`：读 yaml → 过 `prereq_rules` schema（有错抛 `ValueError`，消息含错误列表）→ 检查 `id` 唯一（重复抛 `ValueError`）→ 返回 doc。
  - `index_rules(doc) -> dict[str, dict]`：`id -> rule`。
  - `rules_for(doc, app_slug, market) -> list[dict]`：过滤 `applies_to.app in ("*", app_slug)` 且 `applies_to.market == market`。

- [ ] **Step 1: 写失败测试**：

```python
import pytest
from tools.prereq.rules import load_rules, index_rules, rules_for

def test_load_rules_ok():
    doc = load_rules("tools/prereq_rules.yaml")
    assert doc["version"] >= 1 and len(doc["rules"]) >= 9

def test_load_rules_duplicate_id_raises(tmp_path):
    p = tmp_path / "dup.yaml"
    p.write_text(
        "version: 1\nrules:\n"
        "  - {id: x, applies_to: {app: '*', market: 北交所}, match: {all: [a]}, requires: {account: any}, polarity: no_prereq, priority: 1, confidence: high, provenance: t}\n"
        "  - {id: x, applies_to: {app: '*', market: 北交所}, match: {all: [b]}, requires: {account: any}, polarity: no_prereq, priority: 1, confidence: high, provenance: t}\n",
        encoding="utf-8")
    with pytest.raises(ValueError):
        load_rules(str(p))

def test_rules_for_filters_market_and_app():
    doc = load_rules("tools/prereq_rules.yaml")
    got = rules_for(doc, "guojin", "北交所")
    assert got and all(r["applies_to"]["market"] == "北交所" for r in got)
    assert index_rules(doc)["basic-bjse-etf"]["polarity"] == "no_prereq"
```

- [ ] **Step 2: 跑测试确认失败**。

- [ ] **Step 3: 写实现** `tools/prereq/rules.py`：

```python
from tools.contracts.validate import load_and_validate

def load_rules(path):
    doc, errs = load_and_validate(path, "prereq_rules")
    if errs:
        raise ValueError(f"prereq_rules schema errors: {errs}")
    ids = [r["id"] for r in doc["rules"]]
    dups = {i for i in ids if ids.count(i) > 1}
    if dups:
        raise ValueError(f"duplicate rule ids: {sorted(dups)}")
    return doc

def index_rules(doc):
    return {r["id"]: r for r in doc["rules"]}

def rules_for(doc, app_slug, market):
    out = []
    for r in doc["rules"]:
        a = r["applies_to"]
        if a["app"] in ("*", app_slug) and a["market"] == market:
            out.append(r)
    return out
```

- [ ] **Step 4: 跑测试确认通过**。

- [ ] **Step 5: 提交**：

```bash
git add tools/prereq/__init__.py tools/prereq/rules.py
git commit -m "302968 feat 前置规则加载/索引/过滤(唯一id校验)" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | code'
git add tests/prereq/test_rules.py
git commit -m "302968 test 规则加载/过滤单测" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | test'
```

---

### Task 4: 核心匹配 + 单规则映射（`tools/prereq/extract.py`）

**Files:**
- Create: `tools/prereq/extract.py`
- Test: `tests/prereq/test_extract.py`

**Interfaces:**
- Consumes: `rules.rules_for`, rule 结构。
- Produces:
  - `case_text(case) -> str`：`case["title"] + " " + " ".join(case.get("keywords", []))`。
  - `match_rule(case, rule) -> bool`：`all(k in text for k in match.all)` ∧ `(not any-list or any(k in text))` ∧ `(no none-keyword in text)`。缺失的 all/any/none 视为空。
  - `extract(cases, rules_doc, app_slug="*", market="北交所") -> dict`：返回 `prereq_request` dict（Task 5 补全极性/冲突/未识别；本任务先支持"0 或 1 命中"，多命中先取 priority 最高，不做冲突判定）。

> **召回口径（F7，Task 4/6/报告共用）**：**纯展示类 `no_prereq` 用例**（如"简报价字段/详情页字段显示"，标题不含任何 `match.all` 关键词）**匹配不到任何规则时会落入 `unidentified`，这是预期行为**——它们本就不需备码，不影响下单闭环。**召回率(Task 6)只统计"需备码用例"**（gold 中 `polarity ∈ {positive, negative_property}` 的 tc_id）；`no_prereq`/展示类的未识别既不计入召回分母、也不算漏报。报告里把这类 `unidentified` 单列为"无需备码/展示类"，与"需人工补规则"的未识别区分标注。

- [ ] **Step 1: 写失败测试**：

```python
from tools.prereq.rules import load_rules
from tools.prereq.extract import case_text, match_rule, extract

RULES = load_rules("tools/prereq_rules.yaml")

def test_match_all_and_none():
    idx = {r["id"]: r for r in RULES["rules"]}
    buy = {"title": "北交所ETF 限价买入"}
    assert match_rule(buy, idx["basic-bjse-etf"]) is True
    rz = {"title": "融资买入 北交所ETF"}
    assert match_rule(rz, idx["basic-bjse-etf"]) is False  # none 含"融资"

def test_extract_single_hit_basic():
    cases = [{"tc_id": "TC-001", "title": "北交所ETF 限价买入"}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert c["status"] == "identified"
    assert c["matched_rule_ids"] == ["basic-bjse-etf"]
    assert c["polarity"] == "no_prereq"
    assert c["required_account"] == "any"

def test_extract_sell_needs_holding():
    cases = [{"tc_id": "TC-003", "title": "北交所ETF 限价卖出"}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert "sell-needs-holding" in c["matched_rule_ids"]
    assert c["required_instruments"][0].get("has_holding") is True
```

- [ ] **Step 2: 跑测试确认失败**。

- [ ] **Step 3: 写实现** `tools/prereq/extract.py`：

```python
from tools.prereq.rules import rules_for

def case_text(case):
    return case["title"] + " " + " ".join(case.get("keywords", []))

def match_rule(case, rule):
    t = case_text(case)
    m = rule.get("match", {})
    if not all(k in t for k in m.get("all", [])):
        return False
    anys = m.get("any", [])
    if anys and not any(k in t for k in anys):
        return False
    if any(k in t for k in m.get("none", [])):
        return False
    return True

def _primary(matched):
    return sorted(matched, key=lambda r: r["priority"], reverse=True)[0]

def extract(cases, rules_doc, app_slug="*", market="北交所"):
    pool = rules_for(rules_doc, app_slug, market)
    out_cases, unidentified = [], []
    for case in cases:
        matched = [r for r in pool if match_rule(case, r)]
        if not matched:
            out_cases.append({
                "tc_id": case["tc_id"], "title": case["title"],
                "matched_rule_ids": [], "status": "unidentified",
                "required_instruments": [], "required_account": "any",
                "polarity": "unknown", "needed_codes": [], "provenance": [],
            })
            unidentified.append(case["tc_id"])
            continue
        p = _primary(matched)
        inst = p["requires"].get("instrument")
        out_cases.append({
            "tc_id": case["tc_id"], "title": case["title"],
            "matched_rule_ids": [r["id"] for r in matched], "status": "identified",
            "required_instruments": [inst] if inst else [],
            "required_account": p["requires"].get("account", "any"),
            "polarity": p["polarity"], "needed_codes": [],
            "provenance": [p["provenance"]],
        })
    return {
        "rules_version": rules_doc["version"],
        "generated_from": {"cases_count": len(cases)},
        "cases": out_cases, "unidentified": unidentified, "conflicts": [],
        "summary": {"identified": len(out_cases) - len(unidentified),
                    "unidentified": len(unidentified), "conflict": 0, "missing_codes": []},
    }
```

- [ ] **Step 4: 跑测试确认通过**。

- [ ] **Step 5: 提交**：

```bash
git add tools/prereq/extract.py
git commit -m "302968 feat 前置提取:核心匹配(all/any/none)+单规则映射" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | code'
git add tests/prereq/test_extract.py
git commit -m "302968 test 核心匹配+单规则映射单测" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | test'
```

---

### Task 5: 极性三分 + 多规则优先级 + 冲突 + 未识别 + 追溯

**Files:**
- Modify: `tools/prereq/extract.py`
- Test: `tests/prereq/test_extract.py`（追加）

**Interfaces:**
- Produces:
  - `_incompatible(a, b) -> bool`：两规则 `requires` 冲突——同一 `instrument` 属性键取值相反（如 `financing_eligible` 一 true 一 false），或 `account` 一 `普通` 一 `信用`。
  - `extract` 升级：多命中中若最高优先级并列且存在 `_incompatible` → `status="conflict"`，加入 `conflicts`（仍取一个 primary 记录，note 说明冲突键）；`negative_property` 的 primary 需把 `expected_capability` 带进 case（加 `expected_capability` 字段）；`summary.conflict` 计数。`matched_rule_ids` 始终为**全部**命中 id（追溯）。

- [ ] **Step 1: 写失败测试**（追加）：

```python
def test_extract_financing_positive():
    cases = [{"tc_id": "TC-010", "title": "融资买入 北交所ETF", "keywords": ["融资标的"]}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert c["polarity"] == "positive"
    assert c["required_account"] == "信用"
    assert c["required_instruments"][0].get("financing_eligible") is True

def test_extract_negative_property_carries_expected():
    cases = [{"tc_id": "TC-011", "title": "非融资标的 不可融资买入"}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert c["polarity"] == "negative_property"
    assert c["required_instruments"][0].get("financing_eligible") is False
    assert c.get("expected_capability", {}).get("financing_buy") == "rejected"

def test_extract_traceability_all_ids():
    cases = [{"tc_id": "TC-012", "title": "担保品买入 北交所ETF"}]
    req = extract(cases, RULES, app_slug="guojin")
    c = req["cases"][0]
    assert "rz-collateral" in c["matched_rule_ids"]
    assert c["status"] in ("identified", "conflict")

def test_extract_conflict_flagged():
    # 构造两条并列最高优先级、互斥的规则
    doc = {"version": 1, "rules": [
        {"id": "p-true", "applies_to": {"app": "*", "market": "北交所"},
         "match": {"all": ["X"]}, "requires": {"instrument": {"financing_eligible": True}},
         "polarity": "positive", "priority": 10, "confidence": "high", "provenance": "t"},
        {"id": "p-false", "applies_to": {"app": "*", "market": "北交所"},
         "match": {"all": ["X"]}, "requires": {"instrument": {"financing_eligible": False}},
         "polarity": "negative_property", "priority": 10, "confidence": "high", "provenance": "t"},
    ]}
    req = extract([{"tc_id": "TC-050", "title": "X 用例"}], doc, app_slug="guojin")
    assert req["cases"][0]["status"] == "conflict"
    assert req["conflicts"] and req["conflicts"][0]["tc_id"] == "TC-050"
    assert req["summary"]["conflict"] == 1

def test_extract_unidentified_listed():
    req = extract([{"tc_id": "TC-099", "title": "完全不相关的东西"}], RULES, app_slug="guojin")
    assert req["cases"][0]["status"] == "unidentified"
    assert "TC-099" in req["unidentified"]
```

- [ ] **Step 2: 跑测试确认失败**。

- [ ] **Step 3: 改实现**——**用下面完整 `extract.py` 覆盖 Task 4 版本**（保留 `case_text`/`match_rule`/`_primary` 签名不变，新增 `_incompatible`/`_conflict_note`，`extract` 升级为极性三分 + 多规则优先级 + 冲突/未识别 + 追溯；import 齐全，无伪代码）：

```python
from itertools import combinations

from tools.prereq.rules import rules_for


def case_text(case):
    return case["title"] + " " + " ".join(case.get("keywords", []))


def match_rule(case, rule):
    t = case_text(case)
    m = rule.get("match", {})
    if not all(k in t for k in m.get("all", [])):
        return False
    anys = m.get("any", [])
    if anys and not any(k in t for k in anys):
        return False
    if any(k in t for k in m.get("none", [])):
        return False
    return True


def _primary(matched):
    return sorted(matched, key=lambda r: r["priority"], reverse=True)[0]


def _incompatible(a, b):
    """两规则 requires 是否互斥：同名 instrument 属性取值相反，或 account 一普通一信用。"""
    ia = a["requires"].get("instrument", {})
    ib = b["requires"].get("instrument", {})
    for k in set(ia) & set(ib):
        if ia[k] != ib[k]:
            return True
    aa = a["requires"].get("account")
    ab = b["requires"].get("account")
    if aa and ab and aa != ab and "any" not in (aa, ab):
        return True
    return False


def _conflict_note(tops):
    """并列最高优先级规则集里若存在互斥对，返回冲突说明串；否则 None。"""
    for a, b in combinations(tops, 2):
        if _incompatible(a, b):
            ia = a["requires"].get("instrument", {})
            ib = b["requires"].get("instrument", {})
            keys = [k for k in set(ia) & set(ib) if ia[k] != ib[k]]
            if keys:
                k = keys[0]
                return f"instrument.{k}: {ia[k]} vs {ib[k]} (polarity/requires 不相容)"
            return (f"account: {a['requires'].get('account')} vs "
                    f"{b['requires'].get('account')} (polarity/requires 不相容)")
    return None


def extract(cases, rules_doc, app_slug="*", market="北交所"):
    pool = rules_for(rules_doc, app_slug, market)
    out_cases, unidentified, conflicts = [], [], []
    conflict_count = 0
    for case in cases:
        matched = [r for r in pool if match_rule(case, r)]
        if not matched:
            out_cases.append({
                "tc_id": case["tc_id"], "title": case["title"],
                "matched_rule_ids": [], "status": "unidentified",
                "required_instruments": [], "required_account": "any",
                "polarity": "unknown", "needed_codes": [], "provenance": [],
            })
            unidentified.append(case["tc_id"])
            continue

        top = max(r["priority"] for r in matched)
        tops = [r for r in matched if r["priority"] == top]
        note = _conflict_note(tops) if len(tops) > 1 else None

        p = _primary(matched)
        inst = p["requires"].get("instrument")
        rec = {
            "tc_id": case["tc_id"], "title": case["title"],
            "matched_rule_ids": [r["id"] for r in matched],   # 全部命中 = 追溯
            "status": "conflict" if note else "identified",
            "required_instruments": [inst] if inst else [],
            "required_account": p["requires"].get("account", "any"),
            "polarity": p["polarity"], "needed_codes": [],
            "provenance": [p["provenance"]],
        }
        if p.get("expected_capability"):                       # negative_property 带预期失败
            rec["expected_capability"] = p["expected_capability"]
        out_cases.append(rec)

        if note:
            conflict_count += 1
            conflicts.append({
                "tc_id": case["tc_id"],
                "rule_ids": [r["id"] for r in tops],
                "note": note,
            })

    identified = sum(1 for c in out_cases if c["status"] == "identified")
    return {
        "rules_version": rules_doc["version"],
        "generated_from": {"cases_count": len(cases)},
        "cases": out_cases,
        "unidentified": unidentified,
        "conflicts": conflicts,
        "summary": {
            "identified": identified,
            "unidentified": len(unidentified),
            "conflict": conflict_count,
            "missing_codes": [],
        },
    }
```

> **冲突形状**严格用数据模型的 `{tc_id, rule_ids, note}`：`rule_ids` = 并列最高优先级集合、`note` 说明冲突键（如 `instrument.financing_eligible: True vs False (polarity/requires 不相容)`）。冲突用例仍产出一个 primary 记录供人工看，但 `status="conflict"` 且不计入 `summary.identified`。

- [ ] **Step 4: 跑测试确认通过**：`PYTHONUTF8=1 python -m pytest tests/prereq/test_extract.py -q`。

- [ ] **Step 5: 提交**：

```bash
git add tools/prereq/extract.py
git commit -m "302968 feat 前置提取:极性三分+多规则优先级+冲突/未识别+追溯" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | code'
git add tests/prereq/test_extract.py
git commit -m "302968 test 极性/冲突/未识别/追溯单测" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | test'
```

---

### Task 6: 验收指标 + 728 金标 + 阈值门（spike 判决）

**Files:**
- Create: `tools/prereq/metrics.py`
- Create: `tests/fixtures/guojin_728_cases.yaml`, `tests/fixtures/guojin_728_gold.yaml`
- Test: `tests/prereq/test_metrics.py`

**Interfaces:**
- Produces:
  - `_label(case) -> tuple`：`(polarity, required_account, frozenset(instrument.items()))` 用主 instrument。
  - `score(request, gold) -> dict`：返回 `{recall, false_positive_rate, unidentified_count, manual_supplement, polarity_accuracy, traceability}`。
    - 需备码集(gold) = gold 里 `polarity ∈ {positive, negative_property}` 的 tc_id。
    - `recall` = 正确识别的需备码用例数 / 需备码 gold 总数（"正确"= status==identified ∧ `_label(pred)==_label(gold)`）。
    - `false_positive_rate` = 预测需备码但 gold 为 `no_prereq`(或 label 不符) 的数 / 预测需备码总数。
    - `unidentified_count` = `len(request["unidentified"])`。
    - `manual_supplement` = 需备码 gold 中未被正确识别的数(=漏报/未识别)。
    - `polarity_accuracy` = **gold 有标注 且 引擎已 `identified`** 的 tc_id 中 polarity 一致的比例（**须为 1.0**）。分母**排除 `unidentified`**（未识别是漏报，由 `recall`/`unidentified_count` 度量，不算极性分类错误——否则一个漏报会伪装成极性错误）；`positive↔negative_property` 的混淆才是本指标要卡死的危险项。
    - `traceability` = identified 用例中 `matched_rule_ids` 非空比例（**须为 1.0**）。

- [ ] **Step 1: 写失败测试** `tests/prereq/test_metrics.py`：

```python
import os, yaml
from tools.prereq.rules import load_rules
from tools.prereq.extract import extract
from tools.prereq.metrics import score

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")

def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return yaml.safe_load(f)

def test_spike_acceptance_thresholds():
    cases = _load("guojin_728_cases.yaml")["cases"]
    gold = _load("guojin_728_gold.yaml")["gold"]
    req = extract(cases, load_rules("tools/prereq_rules.yaml"), app_slug="guojin")
    m = score(req, gold)
    assert m["recall"] >= 0.9, m                 # 召回优先不漏（含 1 条 paraphrase 漏识别，10/11≈0.909）
    assert m["polarity_accuracy"] == 1.0, m      # positive/negative_property 绝不混淆
    assert m["traceability"] == 1.0, m           # tc_id→rule_id 全可追溯
    # 误报率：召回优先，误报仅设宽松上界（TC-017 过触发样本被计为误报，1/11≈0.09）
    assert m["false_positive_rate"] <= 0.34, m
    # 未识别必须显式列出（不静默丢）：实质校验规则覆盖不到的用例确实进了列表
    assert "TC-099" in req["unidentified"], req["unidentified"]
    assert m["unidentified_count"] >= 1, m
```

- [ ] **Step 2: 跑测试确认失败**。

> **Spike 诚实化 caveat（F4，写进 fixture 头注释 + 报告）**：本 spike 用的 `guojin_728_*.yaml` 是**蒸馏自 `profiles/测试数据代码需求清单.md` 的合成 fixture**（仓内**无 Excel 原件**，无真实 728 标题语料）。因此**验收门裁决的是「引擎机制」——极性区分 / 多规则优先级 / 未识别显式化 / tc_id→rule_id 可追溯——不主张、也不代表真实语料上的召回率**。真实 Excel 语料接入后（Plan 3 迁移完成或拿到原始标题），**必须用真语料复测本门**，届时阈值与规则覆盖可能需要重标。
>
> 为避免"照抄规则关键词必过"的过拟合，fixture **内置 3 条 paraphrase/对抗用例**（TC-015/016/017），标题**不逐字复制**规则关键词，用于检验规则对真实措辞的鲁棒性并暴露覆盖缺口：
> - **TC-015**（近义、仍命中）：`信用账户融资买入委托` — "融资买入" 作为子串仍在 → `rz-buy-eligible` 正确命中，示范鲁棒。
> - **TC-016**（近义、覆盖缺口 → `unidentified`）：`标的不在融资池的买入委托` — 语义等于"非融资标的买入"，但无 `非融资标的`/`融资买入` 关键词 → **落入 unidentified**，是真实漏识别，把 recall 从 1.0 拉到 ≈0.909（仍 ≥0.9），这正是门"不再必过"的体现，也是 `manual_supplement` 要抓的项。
> - **TC-017**（对抗、过触发 → 误报）：`北交所ETF 市价档位 字段展示` — 纯展示"市价"字段却触发 `market-order-needs-depth` → 被过报为 positive；**不列入 gold**，故计为 1 条误报（FPR≈0.09 ≤ 0.34 宽松上界），示范"召回优先、允许少量过报"。

- [ ] **Step 3: 写金标 fixtures**。`guojin_728_cases.yaml` 顶层 `{cases: [...]}`（含代表用例 + 3 条 paraphrase/对抗）；`guojin_728_gold.yaml` 顶层 `{gold: {tc_id: {polarity, required_account, instrument: {...}}}}`。**必须包含的用例**（据 `测试数据代码需求清单.md` + 对抗集）：

```yaml
# guojin_728_cases.yaml
# 本文件为蒸馏合成 fixture（无真实Excel语料），验收裁决引擎机制、不主张真实召回；真语料接入后复测。
cases:
  - {tc_id: "TC-001", title: "北交所ETF 限价买入"}                       # no_prereq
  - {tc_id: "TC-002", title: "北交所ETF 详情页 简报价字段"}              # 纯展示→预期 unidentified，不计召回
  - {tc_id: "TC-003", title: "北交所ETF 限价卖出", keywords: ["可卖持仓"]}
  - {tc_id: "TC-004", title: "北交所ETF 市价买入 提交成功", keywords: ["盘口深度"]}
  - {tc_id: "TC-005", title: "IOPV 线 溢价率 字段显示"}
  - {tc_id: "TC-006", title: "担保品买入 北交所ETF"}
  - {tc_id: "TC-007", title: "担保品卖出 北交所ETF"}
  - {tc_id: "TC-010", title: "融资买入 北交所ETF", keywords: ["融资标的"]}
  - {tc_id: "TC-011", title: "非融资标的 不可融资买入"}
  - {tc_id: "TC-013", title: "认购状态 及 认购起止日期 字段"}
  - {tc_id: "TC-014", title: "无权限 委托失败 提示"}
  # —— 以下 3 条为 paraphrase/对抗集（标题不照抄关键词）——
  - {tc_id: "TC-015", title: "信用账户融资买入委托"}                     # 近义仍命中(子串)
  - {tc_id: "TC-016", title: "标的不在融资池的买入委托"}                 # 近义→覆盖缺口→unidentified
  - {tc_id: "TC-017", title: "北交所ETF 市价档位 字段展示"}             # 对抗→过触发→误报
  - {tc_id: "TC-099", title: "某个完全不在规则覆盖内的诡异用例"}         # 纯噪声→unidentified
```

```yaml
# guojin_728_gold.yaml  （no_prereq/纯展示用例可省略；只列需备码 gold + 1 条 no_prereq 对照）
# 注：TC-017 故意不列——它是对抗过触发样本，引擎过报为 positive 但真值无需备码，缺席即被计为误报（宽松上界内）。
gold:
  TC-001: {polarity: "no_prereq", required_account: "any", instrument: {market: "北交所", product: "ETF"}}
  TC-003: {polarity: "positive", required_account: "any", instrument: {market: "北交所", product: "ETF", has_holding: true}}
  TC-004: {polarity: "positive", required_account: "any", instrument: {market: "北交所", product: "ETF", orderbook_depth: true}}
  TC-005: {polarity: "positive", required_account: "any", instrument: {market: "北交所", product: "ETF", has_nav: true}}
  TC-006: {polarity: "positive", required_account: "信用", instrument: {market: "北交所", product: "ETF", collateral_eligible: true}}
  TC-007: {polarity: "positive", required_account: "信用", instrument: {market: "北交所", product: "ETF", collateral_eligible: true}}
  TC-010: {polarity: "positive", required_account: "信用", instrument: {market: "北交所", product: "ETF", financing_eligible: true}}
  TC-011: {polarity: "negative_property", required_account: "any", instrument: {market: "北交所", product: "ETF", financing_eligible: false}}
  TC-013: {polarity: "positive", required_account: "any", instrument: {market: "北交所", product: "ETF", in_subscription: true}}
  TC-014: {polarity: "negative_property", required_account: "普通", instrument: {}}
  TC-015: {polarity: "positive", required_account: "信用", instrument: {market: "北交所", product: "ETF", financing_eligible: true}}   # 近义仍应命中
  TC-016: {polarity: "negative_property", required_account: "any", instrument: {market: "北交所", product: "ETF", financing_eligible: false}}  # 近义漏识别→拉低recall
```

> **门为何仍能过（可复算）**：需备码 gold（`polarity ∈ {positive,negative_property}`）共 **11** 条（TC-003/004/005/006/007/010/011/013/014/015/016）；除 **TC-016**（paraphrase 覆盖缺口→unidentified）外全部正确识别 → `recall = 10/11 ≈ 0.909 ≥ 0.9`、`manual_supplement = 1`。预测需备码共 **11** 条，其中 **TC-017**（对抗过触发、gold 缺席）计 1 误报 → `false_positive_rate = 1/11 ≈ 0.09 ≤ 0.34`。`polarity_accuracy` 分母只取"gold 有标注 且 已 identified"的 tc_id（TC-016 因 unidentified 被排除）→ 全部一致 = 1.0。`unidentified = [TC-002, TC-016, TC-099]`（`TC-099 ∈` 其中）。
>
> **注意**：`_label` 用 gold 的 `instrument` 与 pred 的 `required_instruments[0]` 比对。规则数据(Task 1)与金标(此处)必须协调一致——若某用例 recall 不达标，**先改规则(升 version)**，把金标视为需求真值。这正是 spike 要暴露的"规则覆盖是否够"；paraphrase 集则是要暴露"关键词规则对真实措辞是否够鲁棒"。

- [ ] **Step 4: 写实现** `tools/prereq/metrics.py`：

```python
def _label(entry):
    inst = None
    ri = entry.get("required_instruments") or []
    if ri:
        inst = ri[0]
    elif "instrument" in entry:
        inst = entry["instrument"]
    inst = inst or {}
    return (entry.get("polarity"), entry.get("required_account", "any"),
            frozenset((k, _hashable(v)) for k, v in inst.items()))

def _hashable(v):
    return tuple(sorted(v.items())) if isinstance(v, dict) else v

def score(request, gold):
    preds = {c["tc_id"]: c for c in request["cases"]}
    need = {"positive", "negative_property"}
    gold_need = {tid: g for tid, g in gold.items() if g.get("polarity") in need}

    correct = 0
    for tid, g in gold_need.items():
        p = preds.get(tid)
        if p and p["status"] == "identified" and _label(p) == _label(g):
            correct += 1
    recall = correct / len(gold_need) if gold_need else 1.0
    manual_supplement = len(gold_need) - correct

    # 误报：预测需备码但 gold 无标注(过报) 或 label 不符。召回优先→仅设宽松上界(测试断言 <=0.34)。
    pred_need = [c for c in request["cases"] if c["polarity"] in need]
    fp = 0
    for c in pred_need:
        g = gold.get(c["tc_id"])
        if not g or _label(c) != _label(g):
            fp += 1
    false_positive_rate = fp / len(pred_need) if pred_need else 0.0

    # 极性正确率分母：gold 有标注 且 引擎已 identified；排除 unidentified(那是漏报,归 recall,不算极性错误)。
    both = [tid for tid in gold
            if tid in preds and preds[tid]["status"] == "identified"]
    pol_ok = sum(1 for tid in both if gold[tid].get("polarity") == preds[tid]["polarity"])
    polarity_accuracy = pol_ok / len(both) if both else 1.0

    ident = [c for c in request["cases"] if c["status"] == "identified"]
    trace_ok = sum(1 for c in ident if c["matched_rule_ids"])
    traceability = trace_ok / len(ident) if ident else 1.0

    return {"recall": recall, "false_positive_rate": false_positive_rate,
            "unidentified_count": len(request["unidentified"]),
            "manual_supplement": manual_supplement,
            "polarity_accuracy": polarity_accuracy, "traceability": traceability}
```

> `polarity_accuracy` 分母仅取"gold 有标注 **且** 引擎已 `identified`"的 tc_id：`unidentified`(如 TC-016 paraphrase 缺口) 是漏报、由 `recall`/`manual_supplement` 度量，**不**混进极性分母（否则一次漏识别会伪装成极性错误、冤枉这个门）。为覆盖 `no_prereq` 对照，gold 里已含 TC-001(no_prereq)。若某 no_prereq 未列 gold，则不计入极性分母（安全）。

- [ ] **Step 5: 跑测试确认通过**。若某阈值不达标 → 调 `tools/prereq_rules.yaml`(升 version) 或 cases/gold 使之协调，直到全绿——**这是 spike 的价值：暴露规则覆盖缺口**。

- [ ] **Step 6: 提交**：

```bash
git add tools/prereq/metrics.py tests/fixtures/guojin_728_cases.yaml tests/fixtures/guojin_728_gold.yaml
git commit -m "302968 feat 前置引擎验收指标(召回/误报/极性/追溯)+国金728合成金标" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | code'
git add tests/prereq/test_metrics.py
git commit -m "302968 test spike验收阈值门(召回>=0.9/误报<=0.34/极性=100%/追溯=100%)" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | test'
```

---

### Task 7: CLI 入口 + `本轮前置.yaml/.md` 派生（缺码高亮）

**Files:**
- Create: `tools/prereq_extract.py`
- Test: `tests/prereq/test_cli.py`

**Interfaces:**
- Consumes: `rules.load_rules`, `extract.extract`, `contracts.validate.validate`。
- Produces:
  - `run(cases_path, rules_path, out_yaml, out_md, prerequisites_path=None, app_slug="guojin", market="北交所") -> dict`：读 cases(yaml `{cases:[...]}`)→ `extract` → 解析 `needed_codes` + 判缺码 → 写 `out_yaml`(过 `prereq_request` schema)+ 派生 `out_md`(缺码行标 `⚠️缺码`，`no_prereq`/未识别/冲突分区单列)。返回 request dict。
  - `main(argv=None)`：`--cases --rules --out-yaml --out-md [--prerequisites] [--app] [--market]`，`argparse`；打印 summary，返回 0。
- **缺码判定（严格定义）**：`need_code = (case.polarity ∈ {positive, negative_property}) 且 needed_codes == []` → 该行标 `⚠️缺码` 并进 `summary.missing_codes`。**`no_prereq`（如 TC-001/基础买入、纯展示类）与 `unknown`（未识别）永不进 `missing_codes`**——它们本就不需专门备码；未识别另在"未识别"区提示人工。
- **`needed_codes` 解析（属性子集匹配）**：仅当给 `prerequisites_path` 时生效。读 `prerequisites.yaml` 的 `known_codes[]`(每条形如 `{code, name, attributes:{...}}`)；对某 case，一个 code **命中** 当且仅当 **该 case 的某个 `required_instruments` 里的每个 `(k, v)` 都满足 `code.attributes.get(k) == v`**（属性子集匹配，键取 `market/product/has_holding/orderbook_depth/has_nav/collateral_eligible/financing_eligible/in_subscription` 等，与规则 `requires.instrument` **同一套属性词表**）。命中的 code 去重排序填入 `needed_codes`；无命中 → 留空 → 判缺码。
- 说明：spike 阶段 `apps/guojin/prerequisites.yaml` 尚未迁移(Plan 3 Task 6 产出)，故 `prerequisites_path` 可选；不给时 `known_codes` 视为空 → 所有需备码用例 `needed_codes` 全空 → md 全标 `⚠️缺码`（符合"测前把缺码交给用户补"的语义）。Plan 3 迁出后，`known_codes.attributes` 的词表须与本 plan 规则的 `requires.instrument` 键对齐（Plan 3 迁移职责），届时缺码高亮自动减少。

- [ ] **Step 1: 写失败测试** `tests/prereq/test_cli.py`：

```python
from tools.contracts.validate import load_and_validate
from tools.prereq_extract import run


def _write_cases(tmp_path, body):
    p = tmp_path / "cases.yaml"
    p.write_text(body, encoding="utf-8")
    return p


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
    # 结构断言：产出过 schema + summary 形状
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
    prereq.write_text(   # 只提供满足 has_nav 的码，不提供 has_holding 的码
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
```

- [ ] **Step 2: 跑测试确认失败**。

- [ ] **Step 3: 写实现**——**完整 `tools/prereq_extract.py`（import 齐全、可跑，无占位）**：

```python
import argparse
import sys

import yaml

from tools.contracts.validate import validate
from tools.prereq.extract import extract
from tools.prereq.rules import load_rules

NEED_POL = {"positive", "negative_property"}


def _load_known_codes(prerequisites_path):
    if not prerequisites_path:
        return []
    with open(prerequisites_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return doc.get("known_codes", []) or []


def _resolve_needed_codes(required_instruments, known_codes):
    """属性子集匹配：code 命中某 required_instrument ⇔ 该 instrument 的每个 (k,v) 都在 code.attributes 里且相等。"""
    out = []
    for code in known_codes:
        attrs = code.get("attributes", {}) or {}
        for inst in required_instruments:
            if inst and all(attrs.get(k) == v for k, v in inst.items()):
                out.append(code["code"])
                break
    return sorted(set(out))


def _fmt_attrs(case):
    parts = []
    for inst in case.get("required_instruments", []):
        if inst:
            parts.append(",".join(f"{k}={v}" for k, v in inst.items()))
    acct = case.get("required_account", "any")
    if acct and acct != "any":
        parts.append(f"account={acct}")
    return "; ".join(p for p in parts if p) or "-"


def render_md(req):
    s = req["summary"]
    lines = [
        "# 本轮前置（自动派生，勿手改；改规则/清单再生成）",
        "",
        f"- 规则版本: {req['rules_version']}",
        f"- 概览: identified={s.get('identified', 0)} / "
        f"unidentified={s.get('unidentified', 0)} / "
        f"conflict={s.get('conflict', 0)} / 缺码={len(s.get('missing_codes', []))}",
        "",
        "## 需备码清单",
        "| TC | 标题 | 状态 | 所需属性 | 已解析码 |",
        "|---|---|---|---|---|",
    ]
    for c in req["cases"]:
        if c["polarity"] not in NEED_POL:
            continue
        codes = c.get("needed_codes") or []
        code_cell = "、".join(codes) if codes else "⚠️缺码"
        lines.append(
            f"| {c['tc_id']} | {c['title']} | {c['status']} | {_fmt_attrs(c)} | {code_cell} |"
        )
    lines += ["", "## 无需专门前置（no_prereq）"]
    lines += [f"- {c['tc_id']} {c['title']}"
              for c in req["cases"] if c["polarity"] == "no_prereq"] or ["- （无）"]
    lines += ["", "## 未识别（需人工确认规则覆盖）"]
    lines += [f"- {tid}" for tid in req["unidentified"]] or ["- （无）"]
    lines += ["", "## 冲突（多规则不相容，需人工裁决）"]
    lines += [f"- {cf['tc_id']} rules={cf['rule_ids']} note={cf['note']}"
              for cf in req["conflicts"]] or ["- （无）"]
    lines.append("")
    return "\n".join(lines)


def run(cases_path, rules_path, out_yaml, out_md,
        prerequisites_path=None, app_slug="guojin", market="北交所"):
    with open(cases_path, encoding="utf-8") as f:
        cases = (yaml.safe_load(f) or {}).get("cases", []) or []
    rules_doc = load_rules(rules_path)
    req = extract(cases, rules_doc, app_slug=app_slug, market=market)

    known = _load_known_codes(prerequisites_path)
    missing = []
    for c in req["cases"]:
        if c["polarity"] in NEED_POL:
            c["needed_codes"] = _resolve_needed_codes(c["required_instruments"], known)
            if not c["needed_codes"]:
                missing.append(c["tc_id"])
        else:                        # no_prereq / unknown(未识别) 永不判缺码
            c["needed_codes"] = []
    req["summary"]["missing_codes"] = missing

    errs = validate(req, "prereq_request")
    if errs:
        raise ValueError(f"prereq_request schema errors: {errs}")

    with open(out_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(req, f, allow_unicode=True, sort_keys=False)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(render_md(req))
    return req


def main(argv=None):
    ap = argparse.ArgumentParser(description="从用例集派生本轮前置(yaml+md,缺码高亮)")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--rules", default="tools/prereq_rules.yaml")
    ap.add_argument("--out-yaml", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--prerequisites", default=None)
    ap.add_argument("--app", default="guojin")
    ap.add_argument("--market", default="北交所")
    a = ap.parse_args(argv)
    req = run(a.cases, a.rules, a.out_yaml, a.out_md,
              prerequisites_path=a.prerequisites, app_slug=a.app, market=a.market)
    s = req["summary"]
    print(f"identified={s['identified']} unidentified={s['unidentified']} "
          f"conflict={s['conflict']} missing_codes={s['missing_codes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> md 派生结构：标题 + 概览(summary) + 需备码表(`no_prereq`/`unknown` 不入表；缺码行填 `⚠️缺码`) + `no_prereq` 区 + 未识别区 + 冲突区。yaml 用 `yaml.safe_dump(..., allow_unicode=True, sort_keys=False)` 保中文与字段序，写盘前 `validate(req, "prereq_request")` 硬断言过 schema。

- [ ] **Step 4: 跑测试确认通过**。

- [ ] **Step 5: 全量回归**：`PYTHONUTF8=1 python -m pytest -q`（Plan 1 的 52 + 本 plan 全绿）。

- [ ] **Step 6: 提交**：

```bash
git add tools/prereq_extract.py
git commit -m "302968 feat 前置提取CLI+本轮前置yaml/md派生(缺码高亮/属性子集解码)" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | code'
git add tests/prereq/test_cli.py
git commit -m "302968 test 前置CLI端到端单测(缺码判定/no_prereq不误判)" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | test'
```

---

## Self-Review（作者自检，已过；含复审修订）

1. **Spec 覆盖**：§8.3(spike 召回/误报/未识别/极性)✅Task6；§4.5(带极性规则表/three-way/not_capability→none/升version)✅Task1+5+Global；§4.2(prerequisites 解析 needed_codes)✅Task7(可选,Plan3 迁移后接线)；§L2(半自动映射→本轮前置yaml+md)✅Task7；§一.2(测前一次性备齐,缺码高亮)✅Task7。
2. **占位扫描**：无 TBD/TODO/伪代码；Task 4/5(`extract`) 与 Task 7(`prereq_extract`) 均给**完整可跑函数体、import 齐全**；金标 fixture 与规则数据均给出具体内容。
3. **类型一致**：`extract` 输出字段 = `prereq_request.schema` 字段(含 `polarity: unknown` 枚举给未识别、可选 `expected_capability`)；`conflicts[]` 严格 `{tc_id, rule_ids, note}`；`score` 用 `required_instruments[0]` 与 gold `instrument` 对齐；`load_rules`→`rules_for`→`match_rule`/`extract`→`metrics`→CLI(`run`) 链路名一致。
4. **验收门为实（非恒真）**：recall≥0.9(paraphrase 缺口把它压到≈0.909)、polarity_accuracy==1.0(分母排除 unidentified)、traceability==1.0、`false_positive_rate<=0.34`(宽松上界,过触发样本≈0.09)、`"TC-099" in unidentified` 且 `unidentified_count>=1`——每条都可被 fixture 里的对抗样本触动。
5. **诚实化**：无真实 Excel 语料；728 fixture 为蒸馏合成，验收裁决**引擎机制**而非真实召回；内置 3 条 paraphrase/对抗用例防"照抄必中"；caveat 已入 Task 6 与 fixture 头注释，真语料接入后须复测。
6. **范围**：单一子系统(前置引擎)，独立可测；与 Plan 3(迁移/反哺) 解耦——Plan 3 迁移出 `apps/guojin/prerequisites.yaml` 后，CLI 的 `--prerequisites` 才有真实码可解析，届时 `needed_codes` 自动填充、缺码高亮减少。
