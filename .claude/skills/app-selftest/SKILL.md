---
name: app-selftest
description: AI 驱动 Android App 用 Excel 用例做业务自测——用户给一份 Excel 用例 + 一个 Android app（如国金证券北交所ETF用例）要求驱动真机跑通并出报告时使用。覆盖分档筛选、按屏驱动断言取证、结构化反哺画像、交易安全护栏与上下文税控制。
---

# app-selftest

## 触发条件

用户给一份 **Excel 用例 + 一个 Android app**，要求 AI 驱动真机把用例跑一遍、出结果（如"帮我测一下国金证券这批北交所ETF用例"）。命中即用本 skill；不要临场重新摸索工作流/坑/分档口径——那些已经沉淀，见下方 references 与 `apps/<app>/`。

## 加载顺序（生命周期，spec §六）

1. **测前 · 设备匹配**：读 `apps/<app>/app.yaml`，用设备实际 package/version 匹配 `verified_versions`/`compatibility`；不在范围（未知包或版本越界）→ `revalidation_required`，**停止**，不静默套用旧画像。
2. **测前 · 加载画像上下文**：读 `apps/<app>/profile.yaml`（机器权威）派生出的 `apps/<app>/画像.md`/`速览.md` 建立入口/能力/已验证链路上下文——**只加载相关的、早加载**，别现场摸索。**画像缺失 / 大面积 unverified / 用例命中未收录入口 → 先走 explore mode（`references/explore.md`）把路径探索出来**：日常按需只探当次需要的（轨一），独立探索任务仅用于新 App 建画像与大版本重验证（轨二）。
3. **测前 · 备前置**：跑 `tools/prereq_extract.py` 对本轮 Excel 出「本轮前置」，缺码/歧义高亮，让用户一次性补齐，别测中反复打断。
4. **测前 · 冻结 selection**：按分档口径（见 `references/tiering.md`）默认只选 `high`，用户确认 `scope_hash` 后冻结，范围可审计。
5. **测前 · 定 mode**：读 `apps/<app>/env.yaml` 走 `tools/safety/env_auth.verify_env`——**团队内自测默认走轻量档 `assurance_level: trusted_internal`**（已知模拟盘，声明即信任：未撤销 ∧ 是模拟盘 ∧ 测对 app/版本 → `simulated_submit`，**无需 HMAC 签名/署名/有效期**）；`operator_attested`/`technical_verified` 是**严格路径（休眠，供将来测真账户/生产）**，另需签名+真实署名+有效期。任一基础卫生不符或 `revoked:true` → **自动回退 `confirm_only`**。生成本轮安全约束（缺失/hash 不符 → 拒启下单）。
6. **测中 · 驱动 + 取证**：照 `references/workflow.md` 的工作流（解析Excel分档→按屏分组→串行驱动→断言+截图→回填）执行；下单类经 `tools/safety/submit_guard.py` 硬校验，`simulated_submit` 模式下走撤单闭环。
7. **测后 · 结构化反哺**：结束快照+残留校验后，用 `tools/reback.py` 的 `reback_run`（按声明标识字段 upsert，写盘前 schema 校验）把本轮结果合回 `profile.yaml`/`prerequisites.yaml`（带 `last_verified`+`evidence_run`），再用 `tools/derive_docs.py` 重新派生 `画像.md`/`前置条件.md`/`速览.md`；跑 `tools/lint_profile.py` 查重复/跨产物复制/stale/漂移；`tools/metrics.py` 记本批 output/上下文税。

## 护栏

- **hook 硬护栏（自动生效，不用手动遵守）**：`.claude/hooks/guard_git_add.py`（PreToolUse 拦 `git add .`/`-A`/`--all` 与秘密路径提交，见 git-commit skill 硬规则）；`.claude/hooks/context_tax_reminder.py` 的 `assess` 被 `tools/metrics.py` 复用，每批 metrics 算完超阈值即打印提醒（不是 Stop hook——Stop payload 无实时 token/cache）。
- **交易安全硬护栏（权威文档 `references/safety-policy.md`，不重写、照读）**：三级模式（`confirm_only`/`simulated_submit`/`live_submit` 首版不实现执行路径）· `env_auth.verify_env` 环境认证 → mode 推导、任一不符自动回退 `confirm_only` · `submit_guard.guard_submit` 逐笔硬校验（模式/字段/账户白名单/代码白名单/数量上限/`non_marketable` 价格规则）· `recovery.plan_recovery` 撤单闭环与恢复状态机、歧义匹配一律 STOP 转人工 · `BLOCKED_ENVIRONMENT`——环境降级导致无法真实提交/撤单的用例独立标记、**不计入通过率**。
- **禁止事项（仅严格路径 `operator_attested`，测真账户/生产时才涉及）**：执行程序绝不可自签/伪造该认证（只能由负责人本人填真实署名并用 `.secrets` 密钥签 `.sig`，见 `.secrets/README.md`）；禁止把 `apps/<app>/env.yaml.example` 改名当真认证用。**团队内自测的 `trusted_internal` 档不涉及签名**，仅声明信任已知模拟盘；要临时锁死改 `revoked:true` 即回退 `confirm_only`。

## 薄索引（不内联坐标，坐标运行时动态取）

- `apps/<app>/画像.md`（功能支持矩阵 + 入口地图 + 已验证链路）、`前置条件.md`（已知码/账户能力）、`速览.md`（一页速查）——**均由 `tools/derive_docs.py` 从 `profile.yaml`/`prerequisites.yaml` 派生，勿手改**；要改先改 yaml 再重新派生。
- 具体控件坐标/resource-id 会随交互与版本漂移，**别死记**——运行时用 `python tools/droid.py find "文字"` 现场取，入口地图只给"去哪找"，不代替 `find`。
- 分档/坑清单/工作流细节见 `references/{workflow.md,tiering.md,pitfalls.md}`；交易安全权威见 `references/safety-policy.md`；**路径半自动探索（换 App / 画像缺失 / 版本重验证）见 `references/explore.md`**。
- **接入新自测需求（同 app/换 app）+ 对外分发（模板仓/skill/Maestro）见 `references/onboarding.md`。**
- **新 App 骨架生成用 `tools/init_app.py`**（`--seed-from` 白标种子；不生成 env.yaml，认证永远人工），勿手写 profile.yaml。
