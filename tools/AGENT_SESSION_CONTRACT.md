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
| `retester` | 默认继承 planner | 每条复测用例新会话 |
| `reviewer` | 默认继承 planner | 独立只读会话 |

角色级覆盖必须显式使用 `SIXGILL_RETEST_AGENT_NAME`/`SIXGILL_RETEST_MODEL` 或
`SIXGILL_REVIEW_AGENT_NAME`/`SIXGILL_REVIEW_MODEL`。旧的 `SIXGILL_AGENT_*` 只在
action plan 没有实际 planner 身份时作为全局回退，不能覆盖一个已绑定的 planner。

## 逐步复测请求

使用 `_run_three_sheets.py --llm-retest --retest-queue <queue.json>` 时，宿主每轮收到
一个 `request_type=llm_retest_turn` 请求。请求包含原始 Excel 行字段、相关画像提示、
`generic_planning_knowledge`、首轮结果、当前截图路径、当前 UI 树路径和最近动作历史。
旧 action plan 不属于当前动作 authority；截图和实时 UI 树优先于首轮结果。

宿主每次只能返回一个 `decision`：

```json
{
  "schema_version": "1.0",
  "request_type": "llm_retest_turn",
  "case_id": "股指-row-010",
  "session_id": "session-...",
  "turn": 1,
  "decision": "act",
  "intent": "recover",
  "action": {"type": "tap_bbox", "x1": 900, "y1": 80, "x2": 980, "y2": 160},
  "reason": "截图可见关闭按钮，当前 UI 树未提供稳定 resource-id，先按截图框点击",
  "visible_facts": ["页面顶部可见关闭图标"],
  "confidence": 0.86,
  "evidence": []
}
```

控件未在 UI 树找到但截图可见时，优先使用 `tap_bbox`/`tap_xy` 定位，再等待新的
截图/UI 树；下一轮应使用更新后的 UI 树重新绑定控件。`pass` 必须同时声明
`target_page_match=true` 和 `expected_result_match=true`，并提供具体 `reason`。所有
动作和会话响应都会写入 `llm_retest`、`steps`、`action_trace` 与复测证据目录。

如果宿主不可用、响应超时、协议不合法或达到复测上限，执行器安全地保留
`⛔阻塞`，不会猜测动作，也不会回退到旧的 runtime recovery CLI。
