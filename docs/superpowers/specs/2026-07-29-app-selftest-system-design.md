# App 自测项目 · 系统设计（skeleton + 契约 + 安全）· v5

> 目标：把"AI 驱动真机做 Excel 用例业务自测"升级成**可持续进化、可归档、可复现、可提供测试服务**的项目——相同 app 不重复踩链路坑、测前一次性备齐前置、测中不跑偏不烧 token、测后反哺画像越用越快，且**驱动真实交易操作时有可信、可授权、可复现的硬安全边界**。
> 沉淀自 2026-07-28/29 国金证券北交所ETF 自测（含两融）。v5 依据四轮架构 review 补齐：三级交易模式（测试运营默认 simulated_submit）+ 分级环境认证(operator_attested，防篡改) + 秘密隔离 + 约束独立授权 + 机器权威源(YAML)/派生(MD) + 恢复状态机 + BLOCKED_ENVIRONMENT + non_marketable 程序化判定。

---

## 一、为什么做（国金这轮暴露的成本 + 安全风险）
1. **入口摸索** → 画像治，但要"早加载、只加载相关的"。
2. **前置码反复要人介入** → 测前一次性备齐。
3. **范围跑偏**（73 驱动行 ~49 是 low/middle）→ 默认只测 high 且可审计。
4. **上下文税**（cache_read 占 62%）→ 沉淀集中/另开会话。
0. **[P0] 真实交易风险**：驱动真实委托/撤单/两融，**任何向真实柜台提交的委托都无法保证不成交**（快速行情/集合竞价/停牌恢复/价格规则差异/对手方撮合/提交后崩溃断网）。"跌停买涨停卖+事后撤单"只是补救、不是保证。→ 必须分级授权 + **分级环境认证(operator_attested)** + 逐笔硬校验；**认证失效则自动回退 `confirm_only`(不点最终确认)**。

## 二、已定架构决策
- **承载 = A 轻混合**：**Skill** 主干（加载+流程+护栏钩子+薄索引）；**机器权威源 = YAML**（`profile.yaml`/`prerequisites.yaml`），**Markdown 派生供人读**；**memory** 只放发现指针；**hook** 落地最机械/高价值护栏。
- **家 = A 就地升级**：`sixgill` 仓升级多 app 家；国金下沉 `apps/guojin/`；`tools/` 与 skill 跨 app 共用。
- **交易安全 = 三级模式，`simulated_submit` 为测试运营默认**（v4：本系统仅在测试环境运营，故不让技术指纹卡死交易闭环）：
  - `confirm_only`（降级态）：只驱动到确认框，**绝不点最终确认**（零真实提交、天然不成交）。环境认证缺失/过期/不匹配时**自动回退到此**。
  - `simulated_submit`（**测试运营默认**）：环境经**分级认证为 simulation**（含 `operator_attested` 人工一次性认证，见 §4.6）即允许自主提交+撤单闭环。逐笔仍过 submit-guard 硬校验。
  - `live_submit`（**首版仅入 schema/策略，不实现执行路径**）：真实生产交易才需要，须每轮独立人工批准、绑定账户/代码/数量/价格/有效期、禁 Agent 自签。本系统不测生产，故延后。

## 三、目录骨架
```
sixgill/（= 多app自测家）
  .claude/
    skills/app-selftest/
      SKILL.md
      references/{workflow.md, tiering.md, pitfalls.md, safety-policy.md}
    hooks/                       # git add . 拦截；上下文税预警=待spike
  tools/
    droid.py                     # +submit-guard（程序化硬校验）
    annotate_excel.py  metrics.py  prereq_extract.py  prereq_rules.yaml
    schemas/                     # 契约 schema：app/profile/prerequisites/run/selection/前置/安全约束
  apps/<app-slug>/
    app.yaml                     # 身份/包/verified_versions/账户alias(脱敏)/env ref
    env.yaml                     # ★长期环境认证(operator_attested，别名+认证，可入仓审计)
    profile.yaml                 # ★机器权威: 入口/能力/链路 + 验证元数据
    prerequisites.yaml           # ★机器权威: 账户能力/标的属性/已知码(脱敏)
    画像.md / 前置条件.md / 速览.md   # 全部脚本派生(供人读，勿手改)
    maestro/                     # 只引稳定标识
  runs/<timestamp>-<app-slug>-<run-id>/
    run.yaml                     # 机读清单(无完整账号/资产)
    selection.yaml               # 可审计范围
    本轮前置.yaml / .md
    本轮安全约束.yaml / .md        # mode/approval/expiry/hashes（缺失或未授权→拒启下单）
    snapshots/                   # ★private/ignored: 起止 资产/持仓/委托(脱敏后归档)
    report.md  shots/  metrics.md
  templates/  docs/superpowers/specs/
  .secrets/                      # ★git-ignored: 完整账号/HMAC key（不入仓）
  .gitignore                     # 忽略 .secrets/、runs/**/snapshots/、私密截图
```
复用 droid/annotate/metrics/maestro；新增 prereq 引擎/schemas/app-profile-prereq(yaml)/安全约束/环境认证(env.yaml)/skill/hooks。`profiles/*.md` → 迁 `apps/guojin/` 并转 yaml 权威。

## 四、数据契约（machine-readable，第二个 app 接入前先定）
### 4.1 `app.yaml`（P2 身份 + 版本 + 账户脱敏）
```yaml
slug: guojin
aliases: [国金, 国金证券]
packages: [com.hexin.plat.android.GuoJinZXGSecurity]
verified_versions: [{version: "8.05.001", verified_at: "2026-07-29"}]
compatibility: {min: "8.05.001", max_exclusive: "8.06.000"}   # 超范围→revalidation_required，不因版本更高自动接受
test_accounts:                        # 仓内只存别名+类型+脱敏尾号
  - {alias: pt, type: 普通, mask: "***5183"}
  - {alias: xy, type: 信用, mask: "***2927"}
# 完整账号在 .secrets/guojin.accounts.yaml（ignored）；submit-guard 用 HMAC 比对
environment: env.yaml               # 长期环境认证(operator_attested，见4.6；仅别名+认证，无完整账号，可入仓审计)
profile: profile.yaml
prerequisites: prerequisites.yaml
```
执行前用**设备实际 package/version 匹配**；未知包或版本 ∉ compatibility → **`revalidation_required` 停止**，不静默加载旧画像。

### 4.2 `profile.yaml` / `prerequisites.yaml`（P1 机器权威源）
- `profile.yaml`：`entries[]`(入口地图) / `capabilities[]`(能力矩阵) / `verified_chains[]`(已验证链路)，每条记录带 `key`(唯一)/`last_verified`/`app_version`/`evidence_run`/`status`(verified|stale|unverified)。
- `prerequisites.yaml`：`account_capabilities[]` / `instrument_properties[]` / `known_codes[]`(脱敏)。
- **画像.md/前置条件.md/速览.md 全部由脚本从这两个 yaml 派生**（schema 校验、唯一键、结构化 upsert、stale 查询都在 yaml 层做；Markdown 不承担结构化职责）。

### 4.3 `run.yaml`（P1 可复现，**不含完整账号/资产**）
`input_excel{path, sha256}` · `app{package, version, versionCode, apk_sha256}` · `device{serial, os, resolution}` · `git_commit` · `prereq_rules{version, sha256}` · `selected_case_ids[]` · `skipped_case_ids[]` · `mode` · `本轮安全约束_hash` · `start/end_time` · `status` · `recovery_point`。账户只记 alias。

### 4.4 `selection.yaml`（P2 可审计范围）
`selected_ids[]` · `skipped_ids[{id,reason}]` · `incidental_ids[]`(不计承诺覆盖) · `priority_source` · `scope_hash`(用户确认后)。

### 4.5 `prereq_rules.yaml`（P1 规则 schema，**带极性、治误判**）
关键修正：**区分「无需该能力」/「要求负向属性」/「预期失败」**，不做字符串 negation。
```yaml
- id: rz-buy-eligible
  applies_to: {app: "*", market: 北交所}
  match: {all: ["融资买入"], not_capability: ["无需融资权限"]}   # 否定=该能力需求不存在
  requires: {instrument: {financing_eligible: true}, account: 信用}
  polarity: positive
  priority: 10  ; confidence: high  ; provenance: 国金728实测  ; version: 1
- id: rz-buy-negative-instrument                                # 负向测试:需"非融资标的"作数据
  match: {all: ["非融资标的", "不可融资买入"]}
  requires: {instrument: {financing_eligible: false}}
  expected_capability: {financing_buy: rejected}                # 预期失败,不是"无需前置"
  polarity: negative_property
```
- 一 TC 命中多规则→按 `priority`；冲突标记。全不命中/歧义→标 **未识别** 进人工列表。
- 人工修正**反哺 `prereq_rules.yaml`(升 version)**，非只改本轮。
- **验收指标**(spike 必测)：召回率(优先不漏) / 误报率 / 未识别 TC 数 / 人工补充数 / `tc_id→rule_id` 可追溯。

### 4.6 环境认证 `apps/<app>/env.yaml`（P0，分级 · 长期 · 与本轮约束分离）
认证 = "此 App+账户 连接模拟盘"的**长期事实**，绑 app+账户+版本范围+有效期，**与本轮代码/价格无关**（换测试码不重认证）。分两级：
- `operator_attested`（**首版采用**）：项目负责人一次性人工确认为模拟盘即合法证据，允许 `simulated_submit`。
- `technical_verified`（可选增强，非首版阻塞项）：有网关指纹/券商环境 ID 时使用。
```yaml
type: simulation
assurance_level: operator_attested          # or technical_verified
evidence:
  attested_by: shenjie ; attested_at: "2026-07-29" ; valid_until: "2026-10-29"
  basis: ["项目运营约定仅使用国金模拟盘", "账户 pt/xy 为指定测试账户"]
  package: com.hexin.plat.android.GuoJinZXGSecurity
  version_range: {min: "8.05.001", max_exclusive: "8.06.000"}
  account_aliases: [pt, xy]
  # technical_verified 时可补: endpoint_fingerprint / broker_environment_id
revoked: false
```
**每轮不重认证**，只校验：包名匹配 ∧ App 版本仍在范围 ∧ 当前账户 HMAC ∈ 认证账户 ∧ 未过期 ∧ 未撤销(`revoked:false`)。**任一不符 → 自动回退 `confirm_only`**（不再武断归 live）。技术指纹缺失**不**降级为 live。
- **认证防篡改（执行程序不得自签/伪造）**：env.yaml 虽可入仓审计，但**首版二选一**——① 用 `.secrets/` 本地密钥对认证正文算 **HMAC**，执行前校验；或 ② 认证只能经**人工审批的独立提交**更新，且 **拒绝使用工作区中未提交(uncommitted)的认证变更**。执行程序不可自行修改 env.yaml。

### 4.7 `本轮安全约束.yaml`（P0 逐轮边界，与长期环境认证分离）
只含**本轮**代码/数量/价格/范围（长期环境认证在 `env.yaml`，不重复内联）：
```yaml
mode: confirm_only | simulated_submit    # live_submit 首版不实现执行路径
env_ref: apps/guojin/env.yaml            # 引用长期认证
code_allowlist: [...]  ; qty_max: 100  ; price_rule: non_marketable
account_allowlist_hmac: [...]            # 不存明文
constraint_hash: ...  ; source_selection_hash: ...  ; expires_at: ...   # 本轮短时效
```
- **mode 由环境认证推导**：`env.yaml` operator_attested 且逐项校验通过 → `simulated_submit`；否则 `confirm_only`。**simulated_submit 无需每轮人工批准**（认证是长期的，换测试码不重认证）。
- droid **只接受 schema 通过 + 未过期 + `source_selection_hash` 与 selection 一致 + mode 与 env 认证相容** 的约束。
- `live_submit`（延后）：将来若测生产才需独立人工批准记录、禁 Agent 自签。

### 4.8 秘密与隐私（P1）
- 仓内只 alias+类型+脱敏尾号；完整账号/HMAC key 入 `.secrets/`（git-ignored）。
- `snapshots/`（资产/持仓/委托截图含账户/资金/手机号）默认 **private/ignored**；定义脱敏、保留期限、归档权限。
- `run.yaml` 不记完整账号/资产数据。

## 五、交易安全策略（P0，`safety-policy.md` 权威 + 每轮 `本轮安全约束`）
- **`confirm_only`（降级态）**：驱动到确认框即止、不点最终确认——天然"不成交"保证；环境认证缺失/过期/不匹配时自动落到此。
- **`simulated_submit`（测试运营默认）**：`env.yaml` operator_attested 且逐项校验通过时启用；允许自主提交+撤单闭环+残留校验。逐笔仍过 submit-guard。
- **`live_submit`（首版不实现执行路径）**：仅保留在 schema/策略；将来测生产才须独立人工批准、禁 Agent 自签。
- **submit-guard（程序化硬校验，非视觉概率判断）**：确认框挡 dump，故 guard **在点『买入/卖出/撤单』打开确认框之前**，用 **accessibility/dump 可读的下单页字段**（`auto_stockcode`/`stockprice`/`stockvolume`+当前账户）程序化比对 `本轮安全约束`：账户 HMAC∈白名单 ∧ 代码∈allowlist ∧ 数量≤上限 ∧ **价格满足 `non_marketable` 程序化判定** ∧ mode 允许提交。**字段缺失/页面变化/读取低置信 → 拒绝提交**。确认框弹出后：**能 OCR/局部识别的字段程序化二次比对**（不达置信或读不到→转人工确认）；**视觉模型仅辅助证据，不单独授权最终提交**。
- **`non_marketable` 非主动成交规则（程序化可判，非语义标签）**：定义 `行情源`(下单页五档/涨跌停字段) · `方向`(买/卖) · `最小价位(tick)` · `涨跌停边界` · `行情最大陈旧时间(max_staleness)`。判定：**买单价格 ≤ 卖1−缓冲(或 = 跌停) ∧ 卖单价格 ≥ 买1+缓冲(或 = 涨停)**，且行情时间戳新鲜(≤max_staleness)。**行情缺失/过陈旧/价格不满足 → 拒绝提交**（不靠"挂了跌停就一定不成交"的假设）。
- **撤单闭环校验**（仅 submit 模式）：**撤单闭环成功 = 委托进入撤单成功终态（测试环境 `已撤`/`部撤` 均算撤单成功，部撤/全撤看柜台、不区分）∧ 无该 run 创建的可撤残留委托**（当日委托应仍在并显示终态，不要求从所有查询消失）。重点是校验"能撤单成功"。（`已成`/全部成交 = 无可撤，属另一种结果、非撤单成功，报告如实记录即可。）
- **BLOCKED_ENVIRONMENT 状态**：自动降级 `confirm_only` 后，**依赖真实提交/撤单的用例不得因执行到确认框就判通过**，一律标 `BLOCKED_ENVIRONMENT`——**不计入通过率**、报告显式提示"因环境未认证/降级而阻塞"。（区别于 ✅通过 / 🟡待数据 / ☑不适用。）
- **提交后恢复状态机**：提交后崩溃/断网/结果未知 → 进入明确恢复态；**执行下一笔委托前**，按 `账户+证券代码+方向+数量+价格+提交时间(±窗口)+合同号` 查当日委托，识别并撤销本轮残留委托；**无法唯一匹配 → STOP 转人工**（禁止盲目重试或继续下一笔）。
- **起止快照**：run 开始/结束存 资产/持仓/委托（private）；异常突变超阈值→停止。
- **停止条件（命中即 STOP+报警+记 recovery_point）**：非白名单/未知账户 · 环境认证缺失/过期/撤销/不匹配却尝试 simulated_submit（应自动回退 `confirm_only`）· 本轮约束缺失/过期/hash 不符 · 该撤未撤/残留 · 资产持仓异常突变 · 确认框字段与约束不符 · app 版本 revalidation_required · 提交后断网/崩溃(进恢复流程)。

## 六、生命周期数据流
| 阶段 | 动作 | 产物 |
|---|---|---|
| 测前 | 设备匹配 `app.yaml`(包/版本，超范围停) → 加载 profile 派生上下文 | — |
| 测前 | `prereq_extract` 出 `本轮前置`(缺码高亮) → 用户补 | 本轮前置 |
| 测前 | 冻结 `selection`(默认 high，用户确认 scope_hash)；定 `mode`(env.yaml operator_attested 逐项校验通过→`simulated_submit`，否则回退 `confirm_only`) → 生成 `本轮安全约束`(缺/hash 不符→拒启下单) | selection/安全约束 |
| 测中 | 起始快照(private) → 照 profile 导航 + 只测 selected + **下单类经 submit-guard** + (submit 模式)撤单闭环 | shots/snapshots |
| 测后 | 结束快照+残留校验 → annotate 回填 → **结构化 upsert 反哺 profile.yaml/prerequisites.yaml(带 last_verified/证据run)** → 派生 md/速览 → metrics/run.yaml 归档 | 结果Excel/更新yaml/run.yaml |

## 七、三层详解
### L1 App 画像（YAML 机器权威 + MD 派生 + 结构化 upsert）
- 权威 = `profile.yaml`/`prerequisites.yaml`/`app.yaml`/`prereq_rules.yaml`；`画像.md`/`前置条件.md`/`速览.md`/maestro 引用 = **派生/引用**。
- 反哺 = 对 yaml 按 `key` 结构化 **upsert**（更新 last_verified+证据，去重），schema/唯一键/stale 查询在 yaml 层；lint 检查重复/跨产物复制/stale。
- md 由脚本派生，**勿手改**（改 yaml 再生成）。

### L2 前置条件引擎（半自动，见 4.5；先 spike 测召回/误报）
`prereq_extract.py` 按带极性规则映射 → `本轮前置.yaml+md`；国金 728 spike 定型（防单样本过拟合）。

### L3 护栏（hook 硬 + skill 软，诚实分工）
- **hook 硬**：`git add .` 拦截；`.secrets/`/snapshots 泄漏检查；下单前 `本轮安全约束` 缺失/未授权则拒启。
- **待 spike（D2）**：上下文税预警——先验 hook 能否取实时 token/cache；拿不到→降级"每批 metrics.py + turn/action/时长阈值提醒"。
- **skill 软**：默认只测 high · 入口≤2 次即止 · 提交连贯 · 沉淀集中/另开会话 · 一屏多用例 · PYTHONUTF8。

## 八、实施顺序（契约/安全/spike 先行）
1. **契约 schema**：app/profile/prerequisites/run/selection/本轮前置/prereq_rules/本轮安全约束/trading_environment。
2. **交易安全**：`safety-policy.md`(三级模式) + `env.yaml` operator_attested 认证 + 逐项校验自动回退 + `droid submit-guard`(程序化) + 本轮约束/过期/hash 校验 + 秘密隔离(.secrets/.gitignore/脱敏)。`live_submit` 仅入 schema，不实现执行路径。
3. **prereq spike**：国金 728 测召回/误报/未识别/极性。
4. **hook 可行性 spike**：上下文税指标；定 D2。
5. **迁移+Skill**：`profiles/`→`apps/guojin/`(md→yaml 权威, git mv) + app.yaml + `env.yaml`(由负责人 operator_attested 认证国金模拟盘) + skill/references。
6. **反哺/派生/lint**：yaml upsert 反哺 + md/速览 派生 + lint。

## 九、分档 & 度量
默认只测 **high**(写 selection，用户确认)；low/middle 除非"全测"否则跳过、同屏顺手记 incidental(不计承诺覆盖)。分档 🟢/🟡/☑；一致性/视觉不降级。**状态含 `BLOCKED_ENVIRONMENT`（降级致无法真实提交/撤单的用例）——独立列示、不计入通过率、报告明确提示**，不得与 ✅通过混算。metrics.py 记 output/上下文税/单行成本 → runs/metrics.md。

## 十、开放决策（默认已拟，评审可推翻）
- **D1** 前置引擎：半自动+带极性规则表（先 spike 定复杂度）。
- **D2** 上下文税护栏：**待可行性 spike**（拿不到即降级 metrics.py）。
- **D3** 速览/画像 md：**脚本派生**（不手维护）。
- **D4** 仓名：暂不 rename。
- **D5** 国金默认 `mode`：由负责人对国金模拟盘做**一次 `operator_attested` 环境认证**（绑包名/版本范围/测试账户/有效期）后，**默认 `simulated_submit`**，恢复自主下单/撤单链；认证缺失/过期/撤销或包·版本·账户不匹配时**自动回退 `confirm_only`**。网关指纹/券商环境 ID = 可选增强(`technical_verified`)，不阻塞首版。

## 十一、非目标（这轮不做）
- 不做跨机器/云端 CI（Maestro 本地回归即可）。
- 不做纯自动前置提取（易误判）。
- 不重写 droid/annotate/metrics（仅参数化/归位/加 submit-guard）。
- **只在经认证的模拟盘运营**；测试默认 `simulated_submit`（挂非成交价+撤单闭环，不追求实际成交）；**`live_submit`(生产真实提交)执行路径首版不实现**，仅入 schema，将来测生产再做。
- 不动 iOS。
