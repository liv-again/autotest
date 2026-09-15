# workflow — 工作流（模块级规划、行级执行版）

## 主链路

```
解析Excel(分档) → 模块/页面组规划 → 冻结 selection → 组内复用导航、行级执行 → 行级页面/结果复核 + 独立截图取证 → 每条立即落盘 → 生成 results.json → 阻塞用例独立会话逐条 LLM 复测（可选）→ 异常行/模块异常集中分析 → 画像候选反哺 → 通用回填器写回标注Excel → 记性价比(metrics)
```

- **解析Excel(分档)**：按分档口径（见 `tiering.md`）过滤出本轮范围，默认只取 `优先级==high`。
- **模块/页面组规划**：LLM 每个 Sheet/模块只读取一次 Excel 和相关 App 画像，输出 `module_plan.json`；用例按一级至四级目录、入口和前置条件生成连续页面组，不能只按“个股详情页”等页面名称跨市场合并。
- **按 Excel 行逐条执行**：同一页面组内可以复用导航，但仍按源文件行号逐条执行；每行独立操作、独立复核和独立截图。公共导航不能替代业务动作，也不能广播上一行的页面状态或截图。
- **串行驱动手机**：首轮确定性执行器用 `tools/droid.py` 一次一步执行已校验 Agent 计划中的原语；目标页失败最多消费一次计划内 `recovery_navigation`。阻塞复测若启用 `--llm-retest`，则由独立 retester 会话重新读取原始 Excel、画像和实时截图/UI 树，逐步选择一个动作；旧计划只作审计参考。
- **目标页门禁顺序**：每行固定执行“当前状态复位 → 判断目标页面 → 是则执行当前行操作 → 否则执行公共导航 → 重新校验目标页面 → 进入成功才执行、进入失败阻塞且不执行”；导航后的页面断言必须在本行动作前完成。
- **异常集中分析**：页面组或模块完成后只把失败、阻塞、待验证和低置信度记录生成 `exception_queue.json` 交给 LLM；运行结束生成带证据的 `profile_feedback.json`，候选画像不得未经校验直接覆盖正式画像。
- **逐条落盘**：每条用例完成后追加 `execution_records.jsonl` 并更新 `execution_state.json`；暂停可以从最后一条已持久化行恢复。
- **元素树断言 + 截图取证**：断言优先用 `droid.py has "关键词"`（退出码判断，内部比对不看输出，规避终端乱码）；dump 找不到图标/自定义绘制目标时，按 `visual-targets.md` 使用截图和 `droid.py tap --bbox` 兜底；需要留痕的关键结果截图（`droid.py shot`），每个用例留 1 张最能说明问题的即可。
- **生成结果 → LLM逐行复核 → 质量门 → 逐条复测 → 回填标注Excel**：执行器必须在每个步骤结束后提供独立的 `action → observation → status` 事实链，公共导航写入顶层 `setup_trace`，不得混入用例 `actual`。先生成 `llm_review_queue.json`，由选定的复核 Agent 按 `tools/LLM_REVIEW_CONTRACT.md` 输出绑定运行、队列、执行记录和截图摘要且记录 Agent/模型/提示词版本的 `llm_reviews.json`，再用 `python tools/llm_review_results.py merge --input execution_records.json --queue llm_review_queue.json --reviews llm_reviews.json --out results.reviewed.json` 合并；任一绑定或证据指纹不一致都必须拒绝。之后调用 `python tools/build_results.py --input results.reviewed.json --out results.json`，由质量门拒绝空 `actual`、通用操作占位句、仅复述 action、跨用例大量复用结果及缺失 evidence。一个 sheet/模块完成后生成阻塞复测队列；兼容模式重放已校验计划，`--llm-retest` 模式则由独立会话逐条重新理解并执行未通过用例，再合并两轮结果。合并保留首轮与复测两轮历史，最终回填使用第二轮状态。最后调用 `python tools/annotate_excel.py --src cases.xlsx --results results.final.json --out annotated.xlsx --evidence-root <run> --strict`。严格回填会拒绝证据路径不存在、未匹配结果或 `matched` 数量不一致。
- **记性价比**：跑完一批用 `tools/metrics.py` 记 output token / 上下文税(cache_read) / 单行成本，写入 `runs/metrics.md`；超阈值会由 `metrics.py` 里接线的 `assess` 自动打印提醒（考虑新开精简会话/固化 Maestro）。

## 工具清单（都在 `tools/`）

| 工具 | 作用 |
|---|---|
| `droid.py` | adb 驱动助手：`current/screen/find/has/tap/type/key/swipe/shot/dump-xml`；`tap --bbox` 支持视觉模型返回框中心点击，是真机驱动与断言的唯一入口。 |
| `module_planner.py` | 每个 Sheet/模块只解析一次 Excel，生成 `module_plan.json`；保留完整字段、`source_order` 和逐行执行计划。 |
| `profile_feedback.py` | 从页面组和逐行证据生成画像候选；默认只写 `profile_feedback.json`，不直接覆盖正式 `profile.yaml`。 |
| `llm_review_queue.py` / `llm_review_results.py` | 生成逐行 LLM 页面/结果复核队列，并将结构化 verdict 合并回结果；LLM 不能覆盖确定性阻塞。 |
| `execution_journal.py` | 逐条追加 `execution_records.jsonl`、更新 `execution_state.json`，支持暂停恢复和完成后生成正式执行记录。 |
| `exception_queue.py` | 从模块结果提取失败/阻塞/待验证/低置信度用例，生成供 LLM 集中分析的 `exception_queue.json`。 |
| `annotate_excel.py` | 通用 Excel 回填 CLI/API：按行号/用例 ID/名称定位，追加或更新 AI 列、内联证据并生成汇总；不负责决定本轮筛选范围。 |
| `retest_results.py` | 首轮 sheet/模块完成后生成未通过用例的单条复测队列，并将复测结果合并为最终结果；保留 `attempts` 两轮历史，要求复测定位和证据完整。 |
| `metrics.py` | `now`/`tokens SINCE UNTIL`：按会话 transcript 算 token/成本；`context_tax_metrics`/`remind` 是 D2 上下文税阈值提醒的真实触发点。 |
| `prereq_extract.py` | 测前按带极性规则表从 `prerequisites.yaml` 的 `known_codes` 解出「本轮前置」（缺码高亮），一次性备齐测试数据。 |
| `derive_docs.py` | 把 `profile.yaml`/`prerequisites.yaml` 派生成 `apps/<app>/{画像.md,前置条件.md,速览.md}`（勿手改 md，改 yaml 再生成）。 |
| `reback.py` | 测后把本轮结果按声明标识字段（`key`/`alias`/`code`）结构化 `upsert` 回 `profile.yaml`/`prerequisites.yaml`；写盘前做 schema 校验，失败不写。 |
| `lint_profile.py` | 查重复 key/code、跨产物复制、stale（`last_verified` 过旧却仍标 verified）、md 相对 yaml 的派生漂移。 |
| `safety/*` | `env_auth.verify_env`（环境认证→mode）、`submit_guard.guard_submit`（逐笔硬校验）、`recovery.plan_recovery`（撤单闭环/恢复）、`secrets.*`（HMAC/防篡改）——见 `safety-policy.md`。 |

## 环境自检（开工前顺手过一遍）

- **设备探测用 `python tools/droid.py wait-device`**：内置 30s 间隔 × 最多 3 次重试，3 次未连接即返回退出码 1 → **停止任务，不无限等待**（不要手动 `adb devices` 干等）。
- 命令统一加 `PYTHONUTF8=1` 前缀（尤其涉及中文输出/重定向时，见 `pitfalls.md`）。
- 目标 App 的 `FLAG_SECURE` 已关闭（否则截图 0 字节）。
