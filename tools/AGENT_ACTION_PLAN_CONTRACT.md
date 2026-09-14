# Agent 结构化动作计划

`module_planner.py` 只读取和归一化 Excel/profile。选定的 Agent 读取生成的上下文后，
为本轮要执行的每一条 Excel 行输出一个 `cases` 项。执行器不再解析
`action` 字符串。

规划上下文还包含 `generic_planning_knowledge`。它来自
`tools/generic_planning_knowledge.yaml`，适用于所有 App。Agent 规划横屏列表、列表滑动、
目标元素查找或列表排序用例时，必须读取其中适用的规则，并把要求的前后观察写入
`observe`/`expected_observations`；不适用的规则不要强行加入计划。

最小结构：

```json
{
  "schema_version": "1.0",
  "plan_type": "agent_action_plan",
  "planner": {
    "agent": "实际使用的 Agent 名称",
    "model": "实际使用的模型 wire ID",
    "prompt_version": "agent-actions-v1"
  },
  "source": {
    "path": "cases.xlsx",
    "sha256": "源 Excel 的 SHA-256"
  },
  "app_profile": {
    "path": "apps/guotou/profile.yaml",
    "sha256": "本轮读取的 profile.yaml SHA-256"
  },
  "cases": [
    {
      "case_id": "港股-row-002",
      "sheet": "港股",
      "row": 2,
      "page_group_id": "港股-agent-group-001",
      "page_group_key": "港股|行情|港股首页",
      "navigation": [
        {"type": "tap_text", "text": "行情"}
      ],
      "recovery_navigation": [
        {"type": "tap_text", "text": "行情"}
      ],
      "target_page": {
        "description": "港股模块首页",
        "all_text": ["港股"],
        "any_ids": ["ganggu_page", "title_bar_middle"],
        "orientation": "portrait"
      },
      "actions": [
        {"type": "tap_text", "text": "恒生指数"}
      ],
      "expected_observations": ["进入恒生指数相关页面"]
    }
  ]
}
```

规划上下文会提供 `planner_model_defaults`。默认模型配置在
`tools/agent_model_config.yaml`，当前值为 `gpt-5.6-luna`；也可以通过
`SIXGILL_PLANNER_MODEL` 为当前进程覆盖。`planner.model` 必须填写规划 Agent
实际使用的精确模型 ID，不能把界面显示名或不可用的旧模型名写入计划。

允许的动作类型只有：`tap_text`、`tap_id`、`tap_xy`、`tap_bbox`、
`type_text`、`key`、`swipe`、`rotate`、`wait`、`observe`、`assert_text`、
`assert_id`。`navigation` 是公共导航，`actions` 是当前行的业务动作，
不能把当前行的业务点击放进 `navigation`，也不能提供任意 shell/ADB 命令。

`target_page` 只接受 UI 树可观察条件：`all_text`、`any_text`、`not_text`、
`all_ids`、`any_ids`、`not_ids`、`selected_text`、`selected_ids` 和 `orientation`。
其中 `selected_text`/`selected_ids` 用于多个页签同时可见但只有一个被选中的页面，
执行器会读取 UIAutomator 的 `selected` 属性进行硬校验。它是硬页面门禁，不等价
于业务预期判断。业务结果必须由 Agent 读取执行后的截图、`page_observation`、
`action_trace` 与 Excel `expected` 后写入逐行 `llm_reviews.json`。

## 首轮阻塞项延迟复测

完整执行默认启用 `--auto-retest-blocked`。首轮所有行完成后，执行器会在同一
运行目录生成 `blocked_retest_queue.json`，只选择首轮状态为 `blocked` 的行，
并在 `blocked-retest/` 子目录逐条重新执行一次。第二轮继续使用首轮已经校验的
Agent action plan，但不调用运行时恢复 Agent；每条重新执行公共 setup，并产生
新的截图、UI 观察和动作轨迹。第二轮结束后写入
`execution_records.retested.json`，其中 `attempts` 保留首轮与复测两份记录，
最终可见状态以第二轮为准。

该流程最多运行一轮，不会递归复测。需要诊断首轮原始行为时可显式传入
`--no-auto-retest-blocked`。如果首轮没有阻塞项，只写入状态为 `not_needed` 的
`blocked_retest_summary.json`，不会再次启动 App。

## 异常与复测

执行器不会为单个动作启动第二个运行时 Agent。目标页恢复只允许消费
action plan 中已校验的 `recovery_navigation`；动作、页面门禁或证据失败会写入
逐行轨迹和异常队列。完整运行首轮结束后，默认将 `blocked` 行放入
`blocked_retest_queue.json)，在隔离的 `blocked-retest/` 目录中重新 setup 并复测一轮，
再合并为 `execution_records.retested.json`。LLM 在测后读取截图、UI 树、轨迹和
Excel 预期，生成逐行复核结论；复核不能覆盖确定性门禁。
生成和校验：

```powershell
python tools/agent_plan.py context `
  --source <cases.xlsx> `
  --profile apps/<app>/profile.yaml `
  --out <run>/agent_context.json

python tools/agent_plan.py validate `
  --source <cases.xlsx> `
  --plan <run>/agent_action_plan.json
```

计划绑定源 Excel 的 SHA-256。Excel、计划或执行范围不一致时，执行器在
设备动作前拒绝启动。
