# Agent 会话接入契约

`tools/agent_session.py` 是执行器和桌面 Agent 宿主之间的 provider-neutral 边界。它不
启动 CLI、不调用网络 API，也不写死 Codex、Trae、OpenCode 或 Claude。宿主只需要提供
一个 factory：

```python
def create_session(binding: dict, initial_context: dict):
    return session

class Session:
    def request(self, payload: dict) -> dict:
        ...

    def close(self) -> None:
        ...
```

可以通过进程内的 `register_agent_session_factory(create_session)` 注册，也可以设置：

```text
SIXGILL_AGENT_SESSION_FACTORY=your_host_module:create_session
```

如果使用完整运行的 `--auto-retest-blocked --llm-retest`，延迟复测会由独立子进程启动，
因此必须使用环境变量方式；进程内注册只适用于把 runner 嵌入桌面宿主或直接运行显式
`--retest-queue` 的场景。

每次调用 `create_agent_session()` 都会生成新的 `session_id`。执行角色的默认绑定如下：

| 角色 | Agent/model | 会话 |
| --- | --- | --- |
| `planner` | action plan 中实际记录的规划 Agent/model | 每个 Sheet/模块新会话 |
| `retester` | 默认继承 planner | 一个 blocked retest queue 共用一个长期会话 |
| `reviewer` | 默认继承 planner | 独立只读会话 |

角色级覆盖必须显式使用 `SIXGILL_RETEST_AGENT_NAME`/`SIXGILL_RETEST_MODEL` 或
`SIXGILL_REVIEW_AGENT_NAME`/`SIXGILL_REVIEW_MODEL`。旧的 `SIXGILL_AGENT_*` 只在
action plan 没有实际 planner 身份时作为全局回退，不能覆盖一个已绑定的 planner。

## Whole-case 复测请求

使用 `_run_three_sheets.py --llm-retest --retest-queue <queue.json>` 时，宿主在
`create_session()` 的 `initial_context` 中一次性收到完整执行规范、App 配置、Profile、
prerequisites、execution lessons、pitfalls、module_plan 和 agent_context。之后每个 Case
只收到一个 `request_type=llm_retest_plan` 请求，包含原始 Excel 行字段、首轮结果和本次
新采集的截图/UI 树。旧 action plan 不属于当前动作 authority；截图和实时 UI 树优先于
首轮结果。

宿主必须一次返回现有 Runner Action Contract 的完整 Case Plan：

```json
{
  "schema_version": "1.0",
  "request_type": "llm_retest_plan",
  "case_id": "股指-row-010",
  "session_id": "session-...",
  "navigation": [{"type": "tap_text", "text": "行情"}],
  "actions": [{"type": "tap_text", "text": "股指"}],
  "target_page": {
    "description": "股指页面",
    "selected_text": ["股指"],
    "all_ids": ["gz_index_container"],
    "gate_mode": "composite"
  },
  "expected_observations": ["页面显示主要指数行情"]
}
```

Runner 会先校验完整计划，再连续执行 navigation、target page gate 和全部 actions，最后
采集 page observation 与 evidence。Retest Planner 不返回 pass/fail，也不负责最终测试结论；
所有计划、动作和会话事实都会写入 `llm_retest`、`retest_plans`、`steps`、`action_trace`
与复测证据目录，最终结果继续交给现有 LLM Review。

如果宿主不可用、计划协议不合法、目标页门禁失败或执行/证据采集失败，执行器安全地保留
`⛔阻塞`，不会猜测动作，也不会回退到旧的 runtime recovery CLI。
