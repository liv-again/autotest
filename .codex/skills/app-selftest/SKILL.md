---
name: app-selftest
description: AI 驱动 Android App 用 Excel 用例做业务自测——用户给一份 Excel 用例 + 一个 Android app（如国金证券北交所ETF用例）要求驱动真机跑通并出报告时使用。覆盖分档筛选、按屏驱动断言取证、结构化反哺画像、交易安全护栏与上下文税控制。
---

# app-selftest

## 触发条件

用户给一份 **Excel 用例 + 一个 Android app**，要求 AI 驱动真机把用例跑一遍、出结果（如"帮我测一下国金证券这批北交所ETF用例"）。命中即用本 skill；不要临场重新摸索工作流/坑/分档口径——那些已经沉淀，见下方 references 与 `apps/<app>/`。

## 加载顺序（生命周期，spec §六）

0. **测前 · 前置交付物检查**：确认 `app-selftest-prepare` 已产出 `selection.yaml` + `scope_hash` + 本轮前置清单（代码已备齐、路径已探明）；**缺失/过期 → 先跑前置任务 `app-selftest-prepare`，不要现场重新收集核对**（否则上下文税吃掉主任务，见 `runs/metrics.md`）。
1. **测前 · 设备探测 + 匹配**：先 `python tools/droid.py wait-device` 探测设备（内置 30s 间隔 × 最多 3 次，未连接返回退出码 1 → **停止任务，不无限等待**）；在线后读 `apps/<app>/app.yaml`，用设备实际 package/version 匹配 `verified_versions`/`compatibility`；不在范围（未知包或版本越界）→ `revalidation_required`，**停止**，不静默套用旧画像。
2. **测前 · 加载画像上下文**：读前置任务已核对/补全的 `apps/<app>/profile.yaml` 派生出的 `画像.md`/`速览.md` 建入口/能力/已验证链上下文——只加载相关的。若目标包含图标、图片或自定义绘制区域，且存在 `apps/<app>/visual_anchors.yaml`，同时按需加载与当前页面相关的视觉语义；具体流程见 `references/visual-targets.md`。**画像中仍缺/未收录入口 = 前置未完成 → 回前置任务补，别在主任务现场探索**。
3. **测前 · 备前置**：读前置任务产出的「本轮前置」清单（`本轮前置.yaml`/`.md`）——代码已解析/补齐；仍缺码 = 前置未完成，回前置任务。
4. **测前 · 冻结 selection**：用前置任务的 `scope_hash` 冻结范围，可审计，不临场扩大。
5. **测前 · 定 mode**：读 `apps/<app>/env.yaml` 走 `tools/safety/env_auth.verify_env`——**团队内自测默认走轻量档 `assurance_level: trusted_internal`**（已知模拟盘，声明即信任：未撤销 ∧ 是模拟盘 ∧ 测对 app/版本 → `simulated_submit`，**无需 HMAC 签名/署名/有效期**）；`operator_attested`/`technical_verified` 是**严格路径（休眠，供将来测真账户/生产）**，另需签名+真实署名+有效期。任一基础卫生不符或 `revoked:true` → **自动回退 `confirm_only`**。生成本轮安全约束（缺失/hash 不符 → 拒启下单）。
6. **测中 · 驱动 + 取证**：照 `references/workflow.md` 的工作流（模块级规划→页面级分组→组内复用导航→按 Excel 行号逐条驱动→行级页面/结果验证→独立截图→逐条落盘→回填）执行；下单类经 `tools/safety/submit_guard.py` 硬校验，`simulated_submit` 模式下走撤单闭环。LLM 在模块/页面组规划、组内执行决策、行级结果复核和异常行重规划中参与，但硬页面门禁不能被 LLM 覆盖。
7. **测后 · 结构化反哺**：结束快照+残留校验后，用 `tools/reback.py` 的 `reback_run`（按声明标识字段 upsert，写盘前 schema 校验）把本轮结果合回 `profile.yaml`/`prerequisites.yaml`（带 `last_verified`+`evidence_run`），再用 `tools/derive_docs.py` 重新派生 `画像.md`/`前置条件.md`/`速览.md`；跑 `tools/lint_profile.py` 查重复/跨产物复制/stale/漂移；`tools/metrics.py` 记本批 output/上下文税。

## 执行分工与硬约束

执行前先读取 `references/execution-lessons.md`。其中的页面契约、双向入口搜索、多入口独立复位、冷启动边界和结果质量门是跨 App/Sheet/模块的通用规则；`apps/<app>/test_notes.yaml` 只能补充应用特有的页面身份证据。

- **模块/页面组规划**：每个 Sheet/模块只由 LLM 读取一次 Excel 和相关 App 画像，生成 `module_plan.json`；必须依据一级至四级目录、入口和前置条件生成连续页面组，不能只按目标页面名称合并不同市场或模块。
- **行级执行**：确定性执行器消费规划，按 `source_order` 升序一次只执行一个 Excel 行；每行独立完成目标页确认、本行动作、页面/结果复核、独立 evidence 和结果落盘。页面相同不代表导航上下文相同。
- **导航复用边界**：同一连续页面组内可以复用导航上下文、resource-id 和已确认的定位策略；每行仍要重新校验目标页，页面被前一行改变时先恢复组锚点。不同组不得合并，`allow_page_batching` 仅表示组内导航复用，不表示合并业务动作。
- **状态隔离**：每条开始前归一竖屏，清理键盘、弹窗、搜索、排序、详情页和页面栈；横竖屏按目标方向解析，不能用“文本包含横屏”粗略判断。模块内只在首次进入或恢复失败时冷启动，普通行之间复用进程并软复位到模块根页面。
- **目标页门禁**：每行严格执行“状态复位 → 判断目标页面 → 已在目标页则执行本行操作；否则执行公共导航 → 重新校验目标页面 → 校验成功才执行、失败即阻塞且不执行本行动作”；公共导航不得混入本行 `action_trace` 或 `actual`。
- **冷启动边界**：模块切换、App 崩溃/卡死、页面状态无法在有限返回次数内恢复时才允许 `force-stop + launch`；不能把冷启动作为每条用例的默认前置。
- **动作失败口径**：未识别动作、控件未找到、前置页不符或断言未完成时标记 `⛔阻塞`/`🟡待验证`；禁止用 `observe` 兜底后判定通过；同一动作最多自动重试 1 次。
- **逐条恢复**：每条完成后立即追加 `execution_records.jsonl` 并更新 `execution_state.json`；暂停后只补跑未完成行，不读取旧结果补齐当前轮次。
- **异常集中分析**：模块完成后生成 `exception_queue.json`，只把失败/阻塞/待验证/低置信度记录交给 LLM 分析；目标页校验失败允许一次运行时重新规划，恢复仍失败才阻塞。
- **画像反哺**：运行结束生成 `profile_feedback.json`，由 LLM 根据证据审核导航上下文、页面特征和恢复路径；候选必须经过 `reback_run`、schema 和 lint 后才能升级到正式画像，单次异常不得直接覆盖。
- **复测边界**：首轮结束后对未通过用例逐条复测一轮；复测仍不通过就保留最终状态，不自动无限复测。

## 结果回填（测后必须执行）

每个已执行/阻塞/跳过的用例都要保留 `sheet` + `row`（Excel 实际行号；没有稳定 ID 时这是唯一定位键），并在运行目录生成 `results.json`。结果契约版本为 `2.0`：保留 `source_order`（Excel 原始顺序）与 `execution_order`（实际执行顺序），步骤结果必须带 `step_id`，不能依赖结果列表顺序回填。禁止把结果再写入 Python 源码或历史字典。格式如下：

```json
{
  "schema_version": "2.0",
  "cases": [
    {
      "sheet": "工作表名称",
      "row": 12,
      "case_id": "可选的用例 ID",
      "source_order": 12,
      "execution_order": 4,
      "status": "✅通过",
      "steps": [
        {
          "step_id": "工作表名称!12:S1",
          "step_index": 1,
          "row": 12,
          "action": "当前用例对应的操作",
          "expected": "当前步骤预期",
          "status": "✅通过",
      "actual": "真实 action_trace 生成的可读步骤 + 证据截图可见的操作结果",
      "page_observation": "可选的原始 UI 树摘要，仅作底层证据",
          "depends_on": []
        }
      ],
      "tier": "high",
      "evidence": ["shots/case-12.png"],
      "tested_at": "2026-08-25"
    }
  ]
}
```

`setup`/公共导航动作可以记录在顶层 `setup_trace` 中，但不得写入任何用例的 `actual`。同一 Excel 行包含多个步骤时，步骤结果在该行的 AI 实测结果单元格内按 `S1`、`S2` 换行回填；一个用例跨多行时，每个步骤必须提供对应的 `row/source_row`。没有 `steps` 的旧版单条结果只精确回填指定源行，不再向连续行广播。

结果必须由执行器提供逐步骤的 `action → observation → status` 事实链，`actual` 必须包含真实 `action_trace` 生成的可读步骤和证据截图可见的操作结果；原始 UI 树只能作为 `page_observation` 底层证据，不能单独填入 AI 实测结果，也不能抄写 Excel 操作描述。先完成逐行 LLM 复核，再用结果构建器执行结构和语义质量门；证据不足时由执行器标记 `🟡待数据`/`⛔阻塞` 并说明原因。构建器会拒绝空 `actual`、通用“已执行操作/已保留截图”占位句、仅复述 action 的结果，以及跨不同用例大量复用的相同结果；默认要求每个步骤或单条结果有可追溯 `evidence`。同一用例的 case-level evidence 会在步骤缺少独立证据时显式继承。

执行记录生成后必须进行逐行 LLM 复核：

```bash
python tools/llm_review_queue.py --input <run>/execution_records.json --out <run>/llm_review_queue.json
# LLM 按队列逐条读取截图和事实，输出 <run>/llm_reviews.json
python tools/llm_review_results.py merge --input <run>/execution_records.json --queue <run>/llm_review_queue.json --reviews <run>/llm_reviews.json --out <run>/results.reviewed.json
python tools/build_results.py --input <run>/results.reviewed.json --out <run>/results.json
python tools/profile_feedback.py --input <run>/results.reviewed.json --out <run>/profile_feedback.reviewed.json --app-slug <app> --app-version <version>
```

LLM 复核必须先判断目标页面，再判断动作效果和预期结果；确定性阻塞不能被覆盖。`llm_reviews.json` 必须绑定本次运行、当前队列、执行记录摘要和截图摘要，并记录 Agent、模型和提示词版本；不满足时不得合并。详细契约见 `tools/LLM_REVIEW_CONTRACT.md`。画像候选由执行器生成到 `profile_feedback.json`，只有审核后才允许通过 `reback_run` 反哺正式画像。

一个 sheet 或模块的首轮测试结束后，先对所有未通过用例做逐条复测。`retest_results.py plan` 默认选择 `fail/partial/blocked/pending/other`，排除 `pass` 和 `☑不适用`；执行器必须按队列一次只跑一个用例，每条复测都重新执行公共 setup，并把新的页面观察和 evidence 写入复测结果，不能把 setup 轨迹写进 `actual`：

```bash
python tools/retest_results.py plan --results <run>/results.json --scope sheet --scope-name <工作表名> --out <run>/retest_queue.json
# 逐条消费 queue.cases（只执行队列中的行，不重新跑完整 Sheet）
python tools/_run_three_sheets.py --source <用例文件.xls> --retest-queue <run>/retest_queue.json --output <run>/retest-run
# 执行器同时生成 retest_execution.json，直接合并复测结果
python tools/retest_results.py merge --results <run>/results.json --plan <run>/retest_queue.json --retest-results <run>/retest-run/retest_execution.json --out <run>/results.final.json
```

合并会要求计划中的用例全部有复测结果，最终可见状态以第二轮为准，同时在每个复测用例的 `attempts` 中保留首轮和复测两份完整记录；未通过用例必须先完成这一步，再回填 Excel。然后调用：

```bash
python tools/annotate_excel.py --src <用例文件.xlsx> --results <run>/results.final.json --out <run>/<用例文件名>_AI自测结果.xlsx --evidence-root <run> --strict
```

回填命令返回的 JSON 报告中 `matched` 数量必须与本轮结果数一致；若表头不是常见的“用例 ID/用例名称/步骤”等名称，先读取 Excel 结构并补充 `--header-row`、`--case-id-column` 或 `--case-name-column`。检查输出文件存在、源文件未被覆盖，再进入 `reback/derive/lint/metrics`。

## 护栏

- **hook 硬护栏（自动生效，不用手动遵守）**：`.claude/hooks/guard_git_add.py`（PreToolUse 拦 `git add .`/`-A`/`--all` 与秘密路径提交，见 git-commit skill 硬规则）；`.claude/hooks/context_tax_reminder.py` 的 `assess` 被 `tools/metrics.py` 复用，每批 metrics 算完超阈值即打印提醒（不是 Stop hook——Stop payload 无实时 token/cache）。
- **交易安全硬护栏（权威文档 `references/safety-policy.md`，不重写、照读）**：三级模式（`confirm_only`/`simulated_submit`/`live_submit` 首版不实现执行路径）· `env_auth.verify_env` 环境认证 → mode 推导、任一不符自动回退 `confirm_only` · `submit_guard.guard_submit` 逐笔硬校验（模式/字段/账户白名单/代码白名单/数量上限/`non_marketable` 价格规则）· `recovery.plan_recovery` 撤单闭环与恢复状态机、歧义匹配一律 STOP 转人工 · `BLOCKED_ENVIRONMENT`——环境降级导致无法真实提交/撤单的用例独立标记、**不计入通过率**。
- **禁止事项（仅严格路径 `operator_attested`，测真账户/生产时才涉及）**：执行程序绝不可自签/伪造该认证（只能由负责人本人填真实署名并用 `.secrets` 密钥签 `.sig`，见 `.secrets/README.md`）；禁止把 `apps/<app>/env.yaml.example` 改名当真认证用。**团队内自测的 `trusted_internal` 档不涉及签名**，仅声明信任已知模拟盘；要临时锁死改 `revoked:true` 即回退 `confirm_only`。

## 薄索引（不内联坐标，坐标运行时动态取）

- `apps/<app>/画像.md`（功能支持矩阵 + 入口地图 + 已验证链路）、`前置条件.md`（已知码/账户能力）、`速览.md`（一页速查）——**均由 `tools/derive_docs.py` 从 `profile.yaml`/`prerequisites.yaml` 派生，勿手改**；要改先改 yaml 再重新派生。
- `apps/<app>/visual_anchors.yaml`（App-specific 图标/图片语义）与 `apps/<app>/visual/`（参考图）——仅在 dump 树缺少目标时按需加载；视觉点击流程见 `references/visual-targets.md`。
- 具体控件坐标/resource-id 会随交互与版本漂移，**别死记**——运行时用 `python tools/droid.py find "文字"` 现场取，入口地图只给"去哪找"，不代替 `find`。
- 分档/坑清单/工作流细节见 `references/{workflow.md,tiering.md,pitfalls.md}`；交易安全权威见 `references/safety-policy.md`；**路径半自动探索（换 App / 画像缺失 / 版本重验证）见 `references/explore.md`**。
- **接入新自测需求（同 app/换 app）+ 对外分发（模板仓/skill/Maestro）见 `references/onboarding.md`。**
- **新 App 骨架生成用 `tools/init_app.py`**（`--seed-from` 白标种子；不生成 env.yaml，认证永远人工），勿手写 profile.yaml。
