# 项目级 LLM 执行规则

本文件适用于本仓库内所有 App、Excel 文件、Sheet、模块和测试批次。凡是“用 Excel 用例驱动 Android App 并输出结果”的任务，都必须先读取本文件，再读取 `.claude/skills/app-selftest/` 或 `.codex/skills/app-selftest/` 下的自测规范。

跨 App/Sheet/模块的复盘经验统一维护在 `.claude/skills/app-selftest/references/execution-lessons.md`（镜像路径为 `.codex/skills/app-selftest/references/execution-lessons.md`），执行前必须读取；应用的 `test_notes.yaml` 只能补充专属页面身份和控件证据。

跨 App 的规划技巧机器可读权威源为 `tools/generic_planning_knowledge.yaml`；运行 `tools/agent_plan.py context` 时会自动注入 `generic_planning_knowledge`，当前选定的 Agent 在生成动作计划时必须读取并仅对适用用例采用。

规划阶段的默认模型配置位于 `tools/agent_model_config.yaml`，当前桌面运行时默认使用精确模型 ID `gpt-5.6-luna`。如明确切换到其他 Agent/runtime，可通过 `SIXGILL_PLANNER_MODEL` 覆盖；action plan 的 `planner.model` 必须记录实际使用的模型 wire ID，不能填写界面显示名称或旧别名。

## 模块级规划，页面分组，行级执行，结果集中复核

- LLM 每个模块或 Sheet 只解析一次，生成 `module_plan.json`；计划必须按一级至四级目录、入口和前置条件生成连续页面组，不能只按“个股详情页”等页面名称合并不同市场。
- 页面组只复用导航上下文；LLM 可参考 App 画像规划组内路径，但 Plan 是可修订建议，不能因为 Plan 失败直接阻塞本来可达的用例。
- 执行器按 `source_order`/Excel 实际行号升序逐条执行，每行是一个独立执行单元；不得因为多个用例落在同一页面而合并业务动作。
- 每行必须独立完成：目标组上下文确认 → 目标页校验 → 本行动作 → 本行断言/观察 → 独立证据 → 立即落盘。
- 同组行可以复用已验证的导航和页面，但每行仍必须重新校验目标页；页面被上一行改变时，先恢复组锚点。不同层级目录、入口、市场、方向或前置数据不得跨组复用。
- 目标页校验失败时，执行器最多执行一次 action plan 中已声明的 `recovery_navigation`，并重新通过原目标页门禁；计划仍失败就阻塞本行，不由执行器临场猜测新的动作。
- 模块完成后，LLM 只接收失败、阻塞、待验证和低置信度记录做集中分析；通过用例不重复发送完整 UI 树和截图。

## 状态与动作硬约束

- 每条用例开始前归一竖屏，清理键盘、弹窗、搜索、排序、详情页和页面栈，并确认前置页面。
- “横屏切换竖屏”目标是竖屏，“竖屏切换横屏”目标是横屏；方向必须按目标状态解析，不能用宽泛的文本包含判断。
- 目标页门禁必须固定为：当前状态复位 → 判断是否为目标页面；是则执行当前行操作；否则只执行公共导航 → 重新校验目标页面；进入成功才执行，进入失败立即阻塞且不得执行本行动作。
- 目标子页面入口查找必须按“当前可见区域 → 回到顶部 → 向下查找底部区域”的有限顺序执行；点击后立即复核目标页。同一行包含多个入口时，每个入口前都要恢复来源页，禁止在第一个子页面上继续点击来源页入口。
- setup 只负责前置导航，不能重复当前行的业务动作；“退出搜索”只执行返回/取消，“取消排序”不能被通用“取消”分支抢先处理。
- 动作必须命中明确处理器。动作未识别、控件找不到、页面状态不符或断言未完成时，必须标记 `⛔阻塞`/`🟡待验证`，不能用 `observe` 兜底后判定通过。
- 同一动作最多自动重试 1 次；第二次仍失败就保留两次轨迹并进入异常队列，不得无限点击、返回或重启。

## 结果与恢复

- 每条结果必须带 `sheet`、`row`、`case_id`、`source_order`、`execution_order`、独立 evidence 和 `action → observation → status` 事实链；`actual` 应包含基于真实 `action_trace` 生成的可读执行步骤，以及截图可见的文字/标题/结果。
- 原始 UI 树只能作为 `page_observation` 等底层证据，不能单独填入“AI实测结果”；不得复制 Excel 操作描述，截图无法确认的内容必须明确标为无法确认。`AI实测结果` 必须按“AI执行步骤 → 操作结果 → 判断理由”三段输出，判断理由必须说明为何判定通过、不通过、待验证或阻塞。
- 每完成一行就追加到 `execution_records.jsonl` 并更新 `execution_state.json`；禁止等整批结束后才一次性写结果。
- 暂停或异常时，保留最后一条已持久化行，恢复时只补跑未完成行；不能读取旧轮次结果补齐当前轮次。
- Sheet/模块首轮结束后，执行器默认把首轮阻塞用例放入同目录的延迟复测队列，逐条重新 setup 并复测一次；复测仍阻塞就保留最终状态，不自动无限复测。LLM 复核后产生的失败、部分通过或待验证项仍可按需使用通用复测队列显式复测。
- 只有完整性门和结果质量门通过后才生成正式最终报告。只有截图、manifest 和暂停状态时，只能生成证据型中间报告。
- 结果质量门允许同一页面的观察事实在不同用例中重复，但前提是每条记录具备自己的 action/expected 上下文和独立 evidence；没有上下文的重复 actual 仍必须拦截。
- 每轮完成后生成 `profile_feedback.json`；它只包含带证据、版本和执行行号的画像候选，未经 `reback_run`、schema 和 lint 校验不得直接覆盖正式画像。

## 当前临时脚本的使用限制

`tools/_run_three_sheets.py` 仅用于本次问题复盘和兼容旧任务。正式执行必须先由当前选定的 Agent 读取 `tools/agent_plan.py context` 产出的上下文，生成并校验 `agent_action_plan.json`，再以 `--action-plan` 启动；执行器只调用计划中的低层动作。`--legacy-deterministic` 仅用于迁移诊断。不得重新引入“只解析用例名称+操作描述、未识别动作 observe 通过、末尾一次性写结果”等旧行为。

执行器通过 `--app` 选择 `apps/<slug>/app.yaml`；没有 `adapter.py` 的 App 必须走 `tools.app_adapter.GenericAdapter`，不得在通用执行器中新增券商专用包名、坐标或页面判断。
