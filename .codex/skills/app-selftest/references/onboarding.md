# 接入新自测需求 + 对外分发指南

> 本文回答两件事：**后续怎么把新的自测需求接进这套系统**、**这套系统怎么给别人用**。
> 配套：`SKILL.md`（主流程）、`workflow.md`（按屏驱动工作流）、`tiering.md`（分档）、`pitfalls.md`（坑）、`safety-policy.md`（交易安全）。

---

## 一、接入新自测需求

### A. 同一个 App（如国金）的新需求 —— 近乎零负担

1. **给一份 Excel 用例**（标好 `优先级` 列 high/middle/low）。默认只测 `high`（见 `tiering.md`）。
2. 系统测前自动：设备实际 `package`/`version` 匹配 `apps/guojin/app.yaml`（超 `compatibility` 范围→`revalidation_required` 停，不静默加载旧画像）→ 加载 `apps/guojin/profile.yaml` 的入口地图/能力矩阵/已验证链路（**照画像导航，不重踩链路坑**）。
3. 跑前置引擎备码：
   ```bash
   python tools/prereq_extract.py --cases <cases.yaml> --rules tools/prereq_rules.yaml \
       --prerequisites apps/guojin/prerequisites.yaml --out-yaml 本轮前置.yaml --out-md 本轮前置.md
   ```
   产出「本轮前置」把这批用例需要的标的属性/账户能力/已知码列出、**缺码高亮**（`⚠️缺码`）→ 你**测前一次性备齐**，避免测中反复介入。
4. 冻结 `selection`（默认 high，你确认 `scope_hash`）→ 定 `mode`（`env.yaml` 认证通过→`simulated_submit`，否则回退 `confirm_only`）→ 生成本轮安全约束。
5. 测中照 profile 导航、只测 selected、下单类过 `submit-guard`、（submit 模式）撤单闭环。
6. 测后 `annotate_excel.py` 回填 → **结构化 upsert 反哺** `profile.yaml`/`prerequisites.yaml`（`reback.py`，更新 `last_verified`+证据 run，去重）→ 派生 md/速览（`derive_docs.py`）→ `metrics.py` 归档。**越用画像越全、越快。**

> 你的动作只有两个：**给 Excel + 备齐缺码**。其余系统托管。

### B. 换一个 App（别的券商）—— 建骨架 + explore mode 探索一次

1. **建骨架**（`tools/init_app.py`，秒级完成）：生成过 schema 的 `app.yaml`/`profile.yaml`/`prerequisites.yaml` + 派生 md；**`env.yaml` 需人工创建**（认证永不自动生成，P0）。
   ```bash
   python tools/init_app.py <slug> --package <包名> --version <版本> [--seed-from apps/guojin]
   ```
   **生成后两个字段需人工补/确认**：
   - `test_accounts`（空数组 → 补脱敏账户别名+尾号，如 `{alias: pt, type: 普通, mask: "***5183"}`）——脱敏敏感数据，生成器刻意留空；
   - `compatibility.max_exclusive`（占位 `999.999.999` → 收紧到实际兼容上限）——否则版本越界校验形同虚设。
2. 国金是**同花顺/Hexin 白标**，同平台券商入口/resource-id 高度相似 → `--seed-from apps/guojin` 自动抄 16 条入口（标 `unverified`），explore mode 只做"确认+修差异"；异平台则空骨架起，靠 explore mode 的**导航图爬取**从零探索。
3. **跑 explore mode**（`references/explore.md`）：逐入口 `droid screen` → 归纳 path 串 → `reback_run` 写盘（`status: unverified`，全程 `confirm_only` 不点提交）→ `derive_docs` 派生 → `lint_profile` 检查。一轮下来即得"能导航但未经交易验证"的画像。
4. `tools/prereq_rules.yaml` 是**跨 app 的**（`applies_to.app: "*"`）→ 规则表直接复用；只补该 app 的 `prerequisites.yaml`（已知码/账户能力/标的属性）。
5. 第一轮当 spike 跑、边跑边 `reback` 反哺，之后就快。
6. 属性词表要一致：`prerequisites.yaml` 的 `known_codes[].attributes` 键必须与 `prereq_rules.yaml` 的 `requires.instrument` 键同一套（`market/product/has_nav/has_holding/collateral_eligible/financing_eligible/orderbook_depth/...`），否则 `needed_codes` 解析不出码（有 `tests/apps/test_prereq_integration.py` 守）。

---

## 二、对外分发（给别人用）

### 可共享层 vs 必须隔离层

| 层 | 内容 | 能否分发 |
|---|---|---|
| 通用工具 | `tools/`(droid/annotate/metrics/prereq/contracts/safety/derive/reback/lint)、`tools/contracts/schemas/`、`tools/prereq_rules.yaml`、`.claude/skills/app-selftest/`、`.claude/hooks/`、`maestro/` | ✅ 可 |
| App 数据 | `apps/<slug>/`（脱敏后的 profile/prerequisites/app.yaml + 派生 md） | ✅ 可（各自演进） |
| **敏感** | `.secrets/`(完整账号/`hmac.key`)、`env.yaml` 真实认证与 `.sig`、`runs/**/snapshots/`、旧文件/历史里的真实账号 | ❌ **绝不入包** |

### 三种分发形态（按对方需求选，可叠加）

- **A · 自测项目模板**（推荐给同团队/同类券商）：把通用层抽成干净模板仓（去掉 guojin 具体数据、**先做历史脱敏**）。对方 clone → 建自己 `apps/<app>/` → 认证 env → 给 Excel+备码 → 跑。画像随他们自己沉淀。
- **B · Claude Code skill**（推荐给"帮我跑一次"）：`app-selftest` skill 自包含（触发=给 Excel+Android app 要 AI 驱动自测）。对方装 skill 即用，不需懂内部。**"提供测试服务"最轻的形态。**
- **C · Maestro flow 包**（推荐给要确定性回归）：已 PASS 的稳定链路固化成 `maestro/*.yaml`，别人 CI 里**无 AI 也能回归**，最省钱最确定（只覆盖已固化链路）。

### 分发前的硬前置（务必）

1. **脱敏**：确保无完整账号（含 **git 历史清洗**：`git filter-repo` 把完整号→掩码；新 `apps/<slug>/` 产物本就脱敏）。
2. **各自认证**：别人**不能用你的 `env.yaml`**，必须对自己环境做 `operator_attested` 认证（真名署名+签名）；默认 `confirm_only` 兜底；`live_submit`（生产真实提交）首版不实现。
3. **三层信任别绕过**：hook 硬护栏（`git add .`/秘密路径拦截）+ skill 软护栏（默认 high/入口≤2 次/连贯提交）+ 交易安全策略（submit-guard/non_marketable/撤单闭环/恢复机）。

---

## 三、一句话建议

团队内复用走 **A（模板仓）**，对外提供服务走 **B（skill）**，回归自动化走 **C（Maestro）**——分发前先历史脱敏 + 让对方各自认证环境。
