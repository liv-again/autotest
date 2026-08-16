# 交易安全策略文档

**版本:** 1.0  
**日期:** 2026-07-29  
**有效范围:** App 自测（契约 schema + 交易安全基础 Plan 1）  

---

## 概述

本文档规范了 **App 自测交易流程的安全策略**，定义了三级执行模式、环境认证机制、逐笔硬校验、撤单闭环与恢复状态机。所有判定逻辑均在 `tools/safety/*` 中程序化实现，保证可测试性与可审计性。

---

## 第 1 部分：执行模式与权限升级

### 1.1 三级模式定义

交易执行分为三个权限等级：

| 模式 | 说明 | 是否实际提交 | 触发条件 |
|------|------|-----------|---------|
| **confirm_only** | 仅确认阶段，不允许最终提交 | 否 | 默认降级；环境认证失败；任何不合规 |
| **simulated_submit** | 模拟交易模式，允许虚拟提交 | 是（仅返回成功，不进交易所） | `env.yaml` 通过全部认证检验 |
| **live_submit** | 真实交易模式 | 是（真实投递交易所） | 策略层概念，执行路径首版不实现 |

**关于 `live_submit`：** 它是**策略层概念，执行路径首版不实现**。更进一步，本 Plan 的约束契约 [`tools/contracts/schemas/safety_constraint.schema.json`](../../../tools/contracts/schemas/safety_constraint.schema.json) 的 `mode` 枚举**只含 `confirm_only` / `simulated_submit`，主动排除 `live_submit`**——任何 `mode: live_submit` 的约束文档会被 schema 校验直接拒绝（由 `tests/contracts/test_schemas.py::test_safety_constraint_rejects_live_submit` 专门断言）。因此 `live_submit` 无法通过契约层进入执行流程。

**权限升级规则：** 只能从高限制到低限制；一旦发现不合规，立即回退至 `confirm_only`。

---

### 1.2 环境认证 → Mode 推导

> **2026-07-30 简化（团队内自测）**：新增轻量档 `assurance_level: trusted_internal`——用于团队内、已知模拟盘的自测：`verify_env` 只做基础卫生（未撤销 ∧ 是模拟盘 ∧ 测对 app/版本）即给 `simulated_submit`，**不要求 HMAC 签名/真实署名/有效期**（声明即信任）。`revoked:true` 仍是一键锁死。下述 `operator_attested` 严格路径（integrity 签名 + 真实署名 + 有效期 + 占位校验）**保留但休眠**，供将来驱动真实账户/生产时再启。

#### 认证机制

环境认证由 [`tools/safety/env_auth.py`](../../../tools/safety/env_auth.py) 实现，函数签名：

```python
def verify_env(env, device_pkg, device_version, now_iso, integrity_ok) -> (mode: str, reasons: list[str])
```

**输入参数：**
- `env` (dict): 校验过的环境配置文档（需先通过 `env.schema.json`）
- `device_pkg` (str): 设备实际安装的应用包名
- `device_version` (str): 设备实际安装的应用版本号，格式 `X.YY.ZZZ`
- `now_iso` (str): 当前时间，ISO 日期格式 `YYYY-MM-DD`
- `integrity_ok` (bool): 环境文件 HMAC 校验结果（§1.4）

**返回值：**
- `mode`: `"simulated_submit"` 如果所有检验通过；`"confirm_only"` 如果任何检验失败
- `reasons`: 失败理由列表（空列表表示通过）

#### 校验项与自动回退

环境认证逐项检验，**任一项失败自动回退至 `confirm_only`**：

1. **integrity_ok** → `"integrity_failed"`
   - 环境文件 HMAC 校验未通过（见 §1.4 防篡改）

2. **revoked 标记** → `"revoked"`
   - `env.yaml` 中 `revoked: true`，表示该环境已撤销

3. **type 类型** → `"not_simulation"`
   - 仅支持 `type: "simulation"`；`type: "live"` 等不支持

4. **package 包名** → `"package_mismatch"`
   - `env.evidence.package` 与设备实际包名不符

5. **版本区间** → `"version_out_of_range"`
   - 设备版本不在 `env.evidence.version_range` 范围内
   - 版本比较：`min ≤ device_version < max_exclusive`（点分数值格式）
   - 参见 `version_in_range()` 函数逻辑

6. **有效期** → `"expired"`
   - `now_iso > env.evidence.valid_until`（ISO 日期字典序）

7. **assurance_level** → `"bad_assurance_level"`
   - 仅认可 `"operator_attested"` 或 `"technical_verified"`
   - 其他值或缺失均拒

**示例：** 若设备版本超出范围，`verify_env()` 返回 `("confirm_only", ["version_out_of_range"])`，submit-guard 随后拒绝所有最终提交。

---

### 1.3 编排层硬门（mode 相容性）

> **编排层硬门（mode 相容性）**：运行时 mode 必须取 **min-privilege(`verify_env` 推导的 mode, `本轮安全约束.mode`)**——两者不相容（如环境认证失败/回退 confirm_only，但约束写了 simulated_submit）时 **STOP**，绝不以约束里更宽松的 mode 提交。`guard_submit` 只读 `constraint["mode"]`、不自证环境；`verify_env` 结果与约束 mode 的相容性由**编排层**强制（此 Plan 未接设备，故为编排层契约，Plan 2 接线时落地）。

---

## 第 2 部分：逐笔提交硬校验

### 2.1 Submit-Guard 架构

submit-guard 是 **最终提交前的硬校验关卡**，由 [`tools/safety/submit_guard.py`](../../../tools/safety/submit_guard.py) 实现：

```python
def guard_submit(order, account_hmac, constraint, quote_ctx, now_ts) -> (allow: bool, reasons: list[str])
```

**输入参数：**
- `order` (dict): 下单字段 `{code, price, qty, side}`
- `account_hmac` (str): 账户 HMAC 摘要，由调用方用 `secrets.account_hmac()` 预算
- `constraint` (dict): 本轮安全约束（来自 `safety_constraint.schema.json`）
- `quote_ctx` (dict): 实时行情上下文 `{ask1, bid1, up_limit, down_limit, max_staleness_s, quote_ts}`
- `now_ts` (float): 当前 Unix 时间戳（秒）

**返回值：**
- `allow` (bool): `True` 如果所有校验通过；`False` 任何校验失败
- `reasons` (list[str]): 原因列表；通过时返回 `["ok"]`；失败时返回具体原因

### 2.2 校验顺序与拒绝条件

控制流分两段：

- **阶段 1（模式）与阶段 2（字段缺失）是短路返回**——一旦命中，立即返回单条原因，后续不再检验。
- **阶段 3–5（账户 / 代码 / 数量 / 价格）是累积**——各条失败原因累加到同一个 `reasons` 列表，全部检验完后 `allow = (reasons 为空)`。因此单笔提交可能同时报出多条原因（如账户不允许 + 数量超限 + 价格市价性）。

#### 阶段 1：模式检验（短路返回）

```
if constraint["mode"] == "confirm_only":
    return (False, ["mode_confirm_only"])
```

**说明：** `confirm_only` 模式下禁止所有最终提交，这是兜底防护。

#### 阶段 2：字段完整性检验（短路返回）

检验必需字段 `code, price, qty, side` 不为空或 None：

```
for k in ("code", "price", "qty", "side"):
    if order.get(k) in (None, ""):
        return (False, ["field_missing"])
```

**说明：** 缺失字段、字符串空值均拒。

#### 阶段 3：账户 & 代码白名单（累积到 reasons）

- **账户 HMAC 校验：** `account_hmac not in constraint["account_allowlist_hmac"]` → `"account_not_allowed"`
  - 防止未授权账户提交
  
- **证券代码白名单：** `order["code"] not in constraint["code_allowlist"]` → `"code_not_allowed"`
  - 仅允许提前白名单的交易品种

#### 阶段 4：数量校验（累积到 reasons）

- **数量类型与正值：** `not isinstance(qty, int) or qty <= 0` → `"qty_invalid"`
  - 数量必须是正整数
  
- **数量上限：** `qty > constraint["qty_max"]` → `"qty_over_max"`
  - 单笔最大数量限制

#### 阶段 5：价格规则检验（累积到 reasons）

**price_rule 分派由 `guard_submit` 自身完成：**
- 若 `constraint["price_rule"] == "non_marketable"`，调用 `check_non_marketable()`（见下）；不通过 → append `"price_marketable:<why>"`。
- 若 `price_rule` 非 `"non_marketable"` 或缺失，`guard_submit` **自身** append `"unknown_price_rule:<value>"`（fail-closed，未知规则一律拒）。注意：此判定在 `guard_submit` 内，**不在** `check_non_marketable` 内。

调用 [`tools/safety/non_marketable.py`](../../../tools/safety/non_marketable.py) 判定：

```python
def check_non_marketable(price, side, quote, up_limit, down_limit, 
                         max_staleness_s, quote_ts, now_ts) -> (ok: bool, reason: str)
```

**价格非市价性规则（`check_non_marketable` 内部逻辑）：**

`check_non_marketable` 仅负责非市价性本身的判定，其逻辑如下：

1. **行情新鲜性检验**
   - 若 `now_ts - quote_ts > max_staleness_s`，拒绝 → `"quote_stale"`
   
2. **买入委托 (side="buy")**
   - 若存在卖1价 (`ask1`)，要求 `price < ask1` → `"ok"` 或 `"buy_price>=ask1"`
   - 若无卖盘，要求价格恰好在跌停价 (`price == down_limit`) → 使用 `math.isclose(abs_tol=1e-6)` 容差
   
3. **卖出委托 (side="sell")**
   - 若存在买1价 (`bid1`)，要求 `price > bid1` → `"ok"` 或 `"sell_price<=bid1"`
   - 若无买盘，要求价格恰好在涨停价 (`price == up_limit`) → 使用 `math.isclose(abs_tol=1e-6)` 容差
   
4. **坏 side** → `"bad_side"`

> **未知 price_rule 的拒绝不在此函数内**，而在 `guard_submit`（见阶段 5 上文）：当 `price_rule` 非 `non_marketable` 时，`guard_submit` 直接 append `"unknown_price_rule:<value>"`，根本不会调用 `check_non_marketable`。

**设计理由：** 非市价性保证不会以极端价格成交，是本地化风险防护。

### 2.3 拒绝示例

| 场景 | 原因字符串 |
|------|-----------|
| mode 为 confirm_only | `mode_confirm_only` |
| price 字段为空 | `field_missing` |
| 账户不在白名单 | `account_not_allowed` |
| 数量为 -10 | `qty_invalid` |
| 数量为 200，上限 100 | `qty_over_max` |
| 买价 96.2 等于卖1 | `price_marketable:buy_price>=ask1` |
| 行情超过 5s 陈旧 | `price_marketable:quote_stale` |

---

## 第 3 部分：撤单闭环与恢复

### 3.1 撤单闭环成功定义

**撤单闭环成功** 的完整定义：

```
(已撤 或 部撤) ∧ (无本轮run_orders产生的残留可撤委托)
```

即：

1. **撤单终态达成：** 本轮提交的委托被成功撤销，进入 `已撤` 或 `部撤` 状态
   - **测试环境部撤/全撤看柜台、不区分**，两者皆视为撤单成功

2. **无残留可撤委托：** 不存在属于本轮的、仍在可撤状态的委托

**注意：`已成` / 全部成交 不属于撤单成功。** 已成交的委托 = 无可撤（可撤空间已消失），是**另一种结果**，非撤单闭环成功。报告如实记录该委托已成交即可，不视为撤单失败，也不视为撤单成功。

**状态分类参考：**
- 撤单成功终态：`已撤`、`部撤`
- 成交结果（非撤单成功）：`已成`
- 非终态（可撤）：`已报`、`未报`、`部成`、`可撤`

### 3.2 恢复状态机

提交后，系统需识别残留委托并规划恢复。恢复由 [`tools/safety/recovery.py`](../../../tools/safety/recovery.py) 实现：

```python
def plan_recovery(run_orders, today_orders, window_s=120) -> dict
```

**输入参数：**
- `run_orders` (list): 本轮已提交的委托列表
  - 每项包含：`{code, side, qty, price, submit_ts, contract_no|None}`
  
- `today_orders` (list): 当日委托全量快照
  - 每项包含：`{code, side, qty, price, submit_ts, status, contract_no|None, ...}`
  - `status` ∈ {已报, 未报, 部成, 可撤, **已撤**, **部撤**, **已成**, ...}
  
- `window_s` (int): 时间窗口，默认 120s
  - 用于在无 contract_no 时，宽松匹配提交时间

**返回格式：**

```python
{
    "action": "CANCEL" | "STOP",
    "cancel": [matched_order_1, matched_order_2, ...],  # 待撤委托列表
    "stop_reason": None | "ambiguous_match:<code>"     # STOP 时的人工介入原因
}
```

#### 匹配规则

对每个 `run_order`，从 `today_orders` 中寻找唯一匹配：

**若存在 contract_no**（优先精确匹配）：
```
matched = [o for o in today_orders if o["contract_no"] == run_order["contract_no"]]
```

**否则，模糊匹配：**
```
code == run_order["code"] ∧
side == run_order["side"] ∧
qty == run_order["qty"] ∧
|price - run_order["price"]| < 1e-6 ∧
|submit_ts - run_order["submit_ts"]| ≤ 120s
```

#### 恢复决策

1. **多匹配 (len(matches) > 1) → STOP**
   - 歧义情况，转人工介入
   - 返回 `{"action": "STOP", "cancel": [], "stop_reason": "ambiguous_match:<code>"}`
   
2. **唯一匹配 + 可撤 (len(matches)==1 ∧ status ∈ {已报,未报,部成,可撤}) → CANCEL**
   - 加入撤销列表
   
3. **唯一匹配 + 终态 (len(matches)==1 ∧ status ∈ {已撤,部撤,已成}) → 无需撤销**
   - 已达终态，无需进一步操作
   
4. **无匹配 → 无需撤销**
   - 可能已成功成交或已被撤，无残留

#### 示例

**场景 1：唯一匹配 + 可撤 → CANCEL**
```
run_orders = [
    {code: "950025", side: "buy", qty: 100, price: 67.343, submit_ts: 1000, contract_no: None}
]
today_orders = [
    {code: "950025", side: "buy", qty: 100, price: 67.343, submit_ts: 1001, status: "已报", contract_no: "6"}
]
输出: {"action": "CANCEL", "cancel": [该委托], "stop_reason": None}
```

**场景 2：多匹配 → STOP（人工介入）**
```
run_orders = [同上]
today_orders = [
    {code: "950025", side: "buy", qty: 100, price: 67.343, submit_ts: 1001, status: "已报", contract_no: "6"},
    {code: "950025", side: "buy", qty: 100, price: 67.343, submit_ts: 1002, status: "已报", contract_no: "8"}
]
输出: {"action": "STOP", "cancel": [], "stop_reason": "ambiguous_match:950025"}
→ 人工判断应撤哪一个
```

---

## 第 4 部分：环境认证防篡改

### 4.1 防篡改机制

执行程序不得自签或伪造环境配置。防篡改由 [`tools/safety/secrets.py`](../../../tools/safety/secrets.py) 实现两个策略：

#### 策略 A：HMAC 完整性校验

```python
def env_integrity_ok(env_yaml_path: str, sig_path: str, key: bytes) -> bool
```

**流程：**
1. 读取 `env.yaml` 原文（二进制）
2. 用本地密钥 `key` 计算 HMAC-SHA256
3. 与 `*.sig` 文件内容比对（使用 `hmac.compare_digest()` 恒定时间比较）
4. 任何不匹配 → `False`，拒绝使用该环境

**失败回退：** 若 `env_integrity_ok()` 返回 `False`，则 `verify_env(..., integrity_ok=False)` 直接回退至 `confirm_only`。

#### 策略 B：Git 未提交检测（兜底）

```python
def is_git_committed(path: str) -> bool
```

**流程：**
1. 运行 `git status --porcelain -- <path>`
2. 若返回空字符串，表示路径无未提交变更
3. 若返回非空或命令失败，表示有变更或路径不在 git 中，拒绝

**使用场景：** 若无法进行 HMAC 校验（如密钥丢失），可作备选检测：拒绝使用工作区的未提交环境配置变更。

---

## 第 5 部分：完整性与通过率

### 5.1 BLOCKED_ENVIRONMENT 状态

`BLOCKED_ENVIRONMENT` 是**用例结果状态**：当环境降级导致用例无法真实提交/撤单时，该用例记为此状态，**不计入测试通过率**（既不算通过、也不算失败）。

**归属说明：** 其枚举值定义与计分逻辑归 **Plan 3 计分层**；**本 Plan 的 schema 不承载该枚举**。本文档仅在策略层声明其语义。

示例触发场景：
- 环境文件 HMAC 校验失败
- 应用版本不在许可范围
- 环境已被撤销 (revoked)

### 5.2 停止条件清单

以下情况下应立即停止测试。**本清单为 Plan 1 已实现的子集**（每项均有对应 `tools/safety` 程序化实现）：

| 条件 | 来源 | 说明 |
|------|------|------|
| 环境认证多项失败 | `verify_env()` | 任何不可逆的认证失败 |
| Submit-guard 拒绝 + mode=confirm_only | `guard_submit()` | 进入确认阶段，禁止最终提交 |
| 恢复歧义 (ambiguous_match) | `plan_recovery()` | 无法唯一确定应撤委托 → 转人工 |
| 环境文件防篡改失败 | `secrets.env_integrity_ok()` | 拒绝使用被篡改的配置 |
| Git 未提交变更检测失败 | `secrets.is_git_committed()` | 拒绝使用工作区未提交的配置 |

> **完整清单见 spec §5。** spec §5 的停止条件还包含以下项，**在本 Plan 尚无 `tools/safety` 实现**（待后续 Plan 实现）：
> - 资产/持仓异常突变
> - 本轮安全约束缺失 / 过期 / hash 不符（`source_selection_hash` / `constraint_hash` 校验）
> - 确认框字段与约束不符
> - app 版本触发 `revalidation_required`（需重新认证）

---

## 第 6 部分：账户隔离

### 6.1 账户 HMAC 计算

```python
def account_hmac(account_no: str, key: bytes) -> str
```

**流程：**
1. 用本地密钥 `key` 对账户号进行 HMAC-SHA256
2. 返回十六进制摘要（64 字符）
3. **摘要中不含原账户号信息**，可安全记录于约束文档中

**签名：**
```
account_hmac(account_no, key) → 64 位 hex 的 HMAC-SHA256 摘要（不含账号明文）
```

约束文档中仅记录该摘要值，不记录原账户号；`account_allowlist_hmac` 即由若干此类摘要组成。

---

## 第 7 部分：实现文件索引

所有程序化实现均位于 `tools/safety/` 模块：

| 文件 | 函数 | 用途 |
|------|------|------|
| [`env_auth.py`](../../../tools/safety/env_auth.py) | `verify_env()`, `version_in_range()` | 环境认证 → mode 推导 |
| [`non_marketable.py`](../../../tools/safety/non_marketable.py) | `check_non_marketable()` | 价格非市价性校验 |
| [`submit_guard.py`](../../../tools/safety/submit_guard.py) | `guard_submit()` | 逐笔提交硬校验 |
| [`recovery.py`](../../../tools/safety/recovery.py) | `plan_recovery()` | 提交后恢复状态机 |
| [`secrets.py`](../../../tools/safety/secrets.py) | `account_hmac()`, `env_integrity_ok()`, `is_git_committed()` | 秘密隔离与防篡改 |

所有实现均为纯函数、无副作用，完全可测试。

---

## 附录 A：模式转移图

```
初始
 ↓
环境认证 (verify_env)
 ├─ 全部通过 → mode = simulated_submit
 └─ 任何失败 → mode = confirm_only (自动回退)
 ↓
Submit-Guard (guard_submit)
 ├─ mode=confirm_only → 拒绝最终提交
 ├─ 字段/账户/代码/数量/价格 任一失败 → 拒绝最终提交
 └─ 全部通过 → 允许提交
 ↓
提交后恢复 (plan_recovery)
 ├─ 歧义匹配 → STOP (人工介入)
 └─ 唯一匹配 → CANCEL (撤销可撤委托)
```

---

## 附录 B：词汇表

| 术语 | 定义 |
|------|------|
| **confirm_only** | 确认阶段模式；禁止最终提交；是默认降级态 |
| **simulated_submit** | 模拟交易模式；允许虚拟提交；需 env.yaml 通过全部认证 |
| **live_submit** | 真实交易模式；策略层概念，执行路径首版不实现；被 safety_constraint schema 的 mode 枚举主动排除 |
| **非市价性 (non_marketable)** | 买价 < 卖1（或无卖盘时 = 跌停）∧ 卖价 > 买1（或无买盘时 = 涨停）∧ 行情新鲜 |
| **可撤状态** | 已报、未报、部成、可撤 |
| **终态** | 已撤、部撤、已成 |
| **HMAC** | 基于密钥的消息认证码；用于完整性校验与账户隐藏 |
| **残留委托** | 本轮提交但仍在非终态的委托 |

---

## 附录 C：版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-07-29 | 初稿；涵盖三级模式、环境认证、submit-guard、恢复机、防篡改 |
