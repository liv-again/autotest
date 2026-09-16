# Agent 结构化动作计划

`module_planner.py` 只读取和归一化 Excel/profile。选定的 Agent 读取生成的上下文后，
为本轮要执行的每一条 Excel 行输出一个 `cases` 项。执行器不再解析
`action` 字符串。

上下文同时提供完整的 `app_profile_entries` 和按页面组筛选的 `profile_hints`；后者只是
便利索引，不能因为为空就忽略画像中的明确路径。

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
      "navigation_source": "profile",
      "navigation_status": "verified",
      "navigation_policy": "required",
      "profile_entry_key": "quote.global_indices",
      "navigation": [
        {"type": "tap_text", "text": "行情"}
      ],
      "recovery_navigation": [
        {"type": "tap_text", "text": "行情"}
      ],
      "target_page": {
        "description": "港股模块首页",
        "all_text": ["港股"],
        "selected_text": ["港股"],
        "all_ids": ["ganggu_page"],
        "gate_mode": "exact",
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
可选的 `gate_mode` 为 `exact`、`composite` 或 `weak`；`weak` 只能作为诊断条件，
不能让执行器跳过已声明导航，也不能证明画像路径有效。对于同屏多页签，必须优先使用
`selected_text`/`selected_ids`；对于无 selected 属性的页面，使用稳定的 `all_ids` 或显式
`gate_mode=composite` 的组合条件，不能用任一公共文字作为唯一门禁。
其中 `selected_text`/`selected_ids` 用于多个页签同时可见但只有一个被选中的页面，
执行器会读取 UIAutomator 的 `selected` 属性进行硬校验。它是硬页面门禁，不等价
于业务预期判断。业务结果必须由 Agent 读取执行后的截图、`page_observation`、
`execution_trace`、`observation_trace` 与 Excel `expected` 后写入逐行 `llm_reviews.json`。

### 导航来源和画像回馈

每个有导航的用例必须填写 `navigation_source`、`navigation_status` 和
`navigation_policy`：画像存在明确路径时使用 `profile`（并填写 `profile_entry_key`）；
画像没有路径时，使用本行以及 TC 前的一级至四级目录、入口、前置条件和步骤推理完整路线，
标记为 `llm_inferred` + `unverified`；两者混合时使用 `profile_plus_llm`。执行器只消费
低层 `navigation`，不重新解析 Excel 或画像自然语言。导航后的目标页门禁成功会写入
`navigation_trace`，`profile_feedback.json` 会生成带 route steps、来源、状态和证据的画像候选；
候选仍需审核后才能更新正式 profile。

`observe` 是动作轨迹中的观察证据请求，不是 `execution_mode`。执行结果同时记录
`execution_mode`、`execution_trace`、`observation_requested` 和 `observation_trace`；
`action_trace` 可以保留完整审计轨迹，但执行门禁只把 `execution_trace` 作为真实操作依据。
观察请求不直接决定最终 status；如果没有真实执行事件，执行门禁不会把纯观察认定为已执行，结果只能留在待验证/阻塞链路。

## 首轮阻塞项延迟复测

完整执行默认启用 `--auto-retest-blocked`。首轮所有行完成后，执行器会在同一
运行目录生成 `blocked_retest_queue.json`，只选择首轮状态为 `blocked` 的行，
并在 `blocked-retest/` 子目录逐条重新执行一次。兼容模式继续使用首轮已经校验的
Agent action plan；若显式传入 `--llm-retest`，则每条由独立的 retester 会话重新读取
原始 Excel 用例、App 画像和每条 Case 开始时的实时截图/UI 树，一次返回并执行完整 Case Plan，旧计划只作
历史审计参考。两种模式都会重新执行公共 setup、产生新的截图/UI 观察和动作轨迹。
第二轮结束后写入 `execution_records.retested.json`，其中 `attempts` 保留首轮与复测
两份记录，最终可见状态以第二轮为准。

该流程最多运行一轮，不会递归复测。需要诊断首轮原始行为时可显式传入
`--no-auto-retest-blocked`。如果首轮没有阻塞项，只写入状态为 `not_needed` 的
`blocked_retest_summary.json`，不会再次启动 App。

## 异常与复测

首轮执行器不会为单个动作启动第二个运行时 Agent；目标页恢复只允许消费 action plan
中已校验的 `recovery_navigation`。如果阻塞队列显式启用 `--llm-retest`，则进入
`tools/agent_session.py` 定义的独立桌面 Agent 会话：它逐轮读取原始 Excel、画像、
当前截图/UI 树和上一轮事实，决定一个动作并等待新的观察，再继续规划。规划、复测、
复核默认使用同一 Agent/model 绑定，但 session_id 不同；复核会话始终只读，不能继续
操作设备。动作、页面门禁或证据失败会写入逐行轨迹和异常队列。完整运行首轮结束后，
默认将 `blocked` 行放入 `blocked_retest_queue.json`，在隔离的 `blocked-retest/`
目录中重新 setup 并复测一轮，再合并为 `execution_records.retested.json`。LLM 在测后
读取截图、UI 树、轨迹和 Excel 预期，生成逐行复核结论；复核不能覆盖确定性门禁。

### 规划、复测和复核的会话绑定

`execution_manifest.agent_binding` 记录三个角色的 Agent、模型、提示词版本和会话策略：

| 角色 | 默认 Agent/model 来源 | 会话边界 |
| --- | --- | --- |
| `planner` | action plan 的 `planner` | 每个 Sheet/模块新会话 |
| `retester` | 继承 planner；可用 `SIXGILL_RETEST_AGENT_NAME`/`SIXGILL_RETEST_MODEL` 显式覆盖 | 一个复测 Queue 共用一个长期会话 |
| `reviewer` | 继承 planner；可用 `SIXGILL_REVIEW_AGENT_NAME`/`SIXGILL_REVIEW_MODEL` 显式覆盖 | 独立只读会话 |

桌面宿主通过 `SIXGILL_AGENT_SESSION_FACTORY=module:function` 或进程内注册
`register_agent_session_factory()` 提供会话实现。完整运行的自动延迟复测在独立子进程中执行，
因此必须使用环境变量；进程内注册适用于嵌入式 runner 或显式复测队列。核心执行器不启动
CLI、不调用 provider SDK，也不会把规划会话继续用于复测或复核。

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
