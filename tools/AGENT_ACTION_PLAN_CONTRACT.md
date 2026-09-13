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

## 运行时异常恢复

正常路径执行时不会为每个动作再次调用 LLM。若目标页门禁、动作执行或
App 配置的临时覆盖层信号触发异常，且启动执行器时提供了
`--recovery-agent-command`（或设置 `SIXGILL_RECOVERY_AGENT_COMMAND`），
执行器会进入当前 Excel 行的用例级接管，把当前行的 Excel 原文、原动作计划、
失败轨迹、当前 UI 树、异常截图和此前接管轮次通过 stdin 交给该 Agent。Agent
必须只返回一个 JSON 对象：

```json
{
  "schema_version": "1.0",
  "plan_type": "agent_runtime_recovery",
  "agent": {
    "name": "实际使用的 Agent",
    "model": "实际使用的模型",
    "prompt_version": "agent-recovery-v1"
  },
  "decision": "retry_current_action",
  "reset": "none",
  "replay_safety": "safe",
  "diagnosis": "检测到遮挡页面的临时弹窗",
  "reason": "弹窗遮挡了目标控件，关闭后可重新确认目标页",
  "actions": [
    {"type": "tap_text", "text": "知道了"}
  ]
}
```

本次运行的 `agent_action_plan.planner.agent` 和 `planner.model` 是默认的
Agent/model 绑定。执行器会将其传给运行时异常恢复和后续逐行复核；只有明确设置
`SIXGILL_RUNTIME_AGENT_NAME/MODEL` 或 `SIXGILL_AGENT_NAME/MODEL` 时，才按角色覆盖。
规划、恢复和复核仍使用各自的 `prompt_version`，并在执行清单中记录实际绑定来源。

`decision` 只能是 `retry_current_action`、`restart_case` 或 `blocked`；一次恢复
动作完成不代表整条用例完成。执行器会继续执行原计划剩余动作；若再次发现
弹窗、页面跑偏、超时或其他阻塞，会在同一个用例接管会话中再次调用 Agent，
并把前面轮次的诊断、动作和结果一并带入上下文。
`reset` 只能是 `none`、`module` 或 `cold_start`。恢复动作仍复用上述低层
动作白名单，单轮最多 16 个；接管轮次默认最多 5 次、恢复动作总数最多 64 个。要求重试时必须明确
`replay_safety: safe`，无法确认业务动作是否已经生效时必须返回 `blocked`。
同一行的业务动作最多允许一次运行时重放；达到上限后即阻塞。
执行器恢复后会重新通过原计划的目标页门禁，不能由 Agent 绕过硬门禁或修改
本行预期页面。

Agent 命令通过 stdin 接收请求，并且必须只在 stdout 输出 JSON；诊断日志请输出
到 stderr。每次请求和响应会保存到运行目录的
`runtime_recovery_trace.jsonl`，每行记录还会保存 `runtime_recovery` 和恢复截图。
每次成功恢复还会在 `profile_feedback.json` 的 `recovery_candidates` 中留下
`diagnosis`、`recovery_actions`、恢复截图、目标页观察和重试轨迹；这类记录是
画像候选，不会自动覆盖正式 `profile.yaml`。

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
