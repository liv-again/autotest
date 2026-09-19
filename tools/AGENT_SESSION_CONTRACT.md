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

首轮自动阻塞复测已停用。必须先完成首轮 LLM 复核，再通过显式
`--retest-queue` 启动复测；该场景可以使用进程内注册或桌面宿主提供的环境变量方式。

每次调用 `create_agent_session()` 都会生成新的 `session_id`。执行角色的默认绑定如下：

| 角色 | Agent/model | 会话 |
| --- | --- | --- |
| `planner` | action plan 中实际记录的规划 Agent/model | 每个 Sheet/模块新会话 |
| `retester` | 默认继承 planner | 一个复测 Queue 共用一个长期会话 |
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

## 当前 Codex Agent 内联模式

如果当前任务由正在运行的 Codex Agent 直接编排，而不是由另一个桌面 Agent
宿主提供 factory，可以显式使用：

```powershell
python tools/_run_three_sheets.py `
  --retest-queue <queue.json> `
  --current-agent `
  --no-auto-retest-blocked `
  --output <run>
```

该模式不调用 `create_agent_session()`，也不创建子 Agent。Runner 为每个 Case
只写一个 `current_agent_case_plan` 请求到
`<run>/current-agent-bridge/requests/`，当前 Codex Agent 读取请求中的 Case、
Expected、首轮事实和实时观察后，把一个完整 Runner Case Plan 写到对应的
`responses/` 路径。Runner 校验该 Plan 后执行全部 navigation/actions，并记录
证据，再进入下一条 Case。

`session_context.json` 只在桥接目录初始化时写入一次，供当前 Agent 复用完整参考
资料；`session_manifest.json` 和 `CURRENT_REQUEST.json` 用于审计和交接。该模式
中的 `session_id` 是当前 Codex 线程的逻辑标识，不代表新建了独立 LLM 会话，也
不提供独立 reviewer。当前 Agent 负责规划，Runner 负责执行和取证。
如需记录当前 Agent 的实际模型，可设置 `SIXGILL_CURRENT_AGENT_NAME` 和
`SIXGILL_CURRENT_AGENT_MODEL`；未设置时名称默认为 `Codex`，模型沿用队列绑定。

当前 Agent 可以用辅助命令查看请求并提交计划：

```powershell
python tools/current_agent_bridge.py inspect `
  --bridge-dir <run>/current-agent-bridge `
  --full

python tools/current_agent_bridge.py submit `
  --bridge-dir <run>/current-agent-bridge `
  --request-id <CURRENT_REQUEST.json 中的 request_id> `
  --plan <当前 Agent 写出的 case_plan.json>
```
