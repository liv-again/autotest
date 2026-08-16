# Hook 可行性 spike：上下文税（token/cache）预警能否接在 Claude Code hook 上（D2）

> 对应 spec §8.4 / §L3「待 spike（D2）」；对应 plan `2026-07-29-migration-skill-reback.md` Task 1。

## 调研问题

`.claude/hooks/` 下的 Claude Code hook（`PreToolUse`/`PostToolUse`/`Stop` 等事件）能否在
**事件触发的那一刻**拿到「实时 token 用量 / cache_read 用量 / 已用 metrics」，从而在 hook 里
直接做「上下文税超阈值」预警？

## 调研方法

1. **查 hook 事件的 stdin payload 字段**：Claude Code 的 hook 以 JSON 形式把 payload 通过 stdin
   传给 hook 脚本。`PreToolUse`/`PostToolUse` payload 含 `session_id`/`transcript_path`/
   `cwd`/`hook_event_name`/`tool_name`/`tool_input`（`PostToolUse` 再加 `tool_response`）；
   `Stop`/`SubagentStop` payload 含 `session_id`/`transcript_path`/`cwd`/`hook_event_name`/
   `stop_hook_active`。**均不含 token 数、cache_read 数、或任何用量统计字段**——没有
   `usage`/`metrics`/`tokens` 这类 key。
2. **查 `$CLAUDE_*` 环境变量**：hook 脚本运行时能看到的环境变量里，本仓库/本会话实测到的只有
   `CLAUDE_CODE_ENTRYPOINT`、`CLAUDE_CODE_SESSION_ID`、`CLAUDECODE`、`CLAUDE_CODE_EXECPATH`、
   `CLAUDE_EFFORT`、`CLAUDE_CODE_CHILD_SESSION` 等**身份/环境标识**，**没有任何实时 token/cache
   计数类变量**。
3. **查 `tools/metrics.py` 现在怎么取 token**：`tools/metrics.py::sum_usage()` 的实现是——从
   `PROJECT_DIR` 下最新修改的 transcript `*.jsonl` 文件里，**逐行解析历史消息**，对每条
   `message.usage` 里的 `input_tokens`/`output_tokens`/`cache_read_input_tokens`/
   `cache_creation_input_tokens` 按时间窗（`since`/`until`）**求和统计**。这是**事后**对已落盘
   transcript 的**批量重放统计**，不是"问一次拿到当前值"的实时查询接口；且它依赖
   `transcript_path`/工程目录扫描，*同一机制*（transcript 逐行 usage 求和）是 hook payload 里
   完全没有暴露给 hook 脚本的。

## 事实发现

- `PreToolUse`/`PostToolUse`/`Stop` 等 hook 事件的 stdin JSON payload **均不携带实时
  token/cache 用量或 metrics 字段**。
- 唯一途径是 hook 脚本自己去读 payload 里的 `transcript_path`，仿照 `metrics.py` 重新解析
  jsonl 求和——但这在 `Stop` 等每次触发都做一次全量/增量 transcript 解析，**成本与
  `metrics.py` 手动跑本质相同**，并不能带来"更实时"的收益，反而让 hook 在**每一次工具调用/
  每一次 Stop** 上都多一次 IO+解析开销。
- **无论如何都拿不到"当前 window 剩余多少上下文/token"这类模型侧实时状态**——hook 能读到的
  只有历史 transcript 里已经落盘的 usage 记录，无法读到"当前这一步"尚未落盘的用量。

## D2 决策

**确认结论：hook 无法拿到实时 token/cache（`metrics.py` 是事后按 transcript 时间窗算），
故 D2 降级为「每批 `tools/metrics.py` 算完后 + turn/action/时长阈值提醒」。**

具体地：

- **D2 真实触发点 = `tools/metrics.py`**：每次跑 `python tools/metrics.py session` 或
  `python tools/metrics.py tokens <since> [until]` 算完一批 metrics 后，调用
  `.claude/hooks/context_tax_reminder.py::assess()` 打印阈值提醒（超阈值才发声，未超阈值
  静默）。
- **不接 Claude Code `Stop` hook**：`Stop` payload 没有 `metrics` 字段，接了 hook 也**永远
  拿不到 `metrics.py` 算出来的数字**，等于一个永远不会发声的死护栏——不做这种"看起来接了
  但实际不生效"的伪护栏。
- `.claude/hooks/context_tax_reminder.py` 只承载**纯函数 `assess(metrics, thresholds) -> list[str]`**
  （可单测、可被 `metrics.py` 复用），文件里附带的 `_main()` 只是一个**可选的手动 CLI**
  （`echo '{"metrics":{...}}' | python .claude/hooks/context_tax_reminder.py`），**不在
  `.claude/settings.json` 里接线为任何 hook 事件**。

## 降级方案的阈值建议

依据 `runs/metrics.md` 记录的实测批次（如「全场景扫」84 动作/26.3min、「盘中批(下单链)」
70 动作/~73min、cache_read 累到 22.7M~59M 等），单批过大时含税成本($，主要来自
`cache_read`)会明显偏离 output-only 的"干净单价"，建议：

| 指标 | 阈值 | 触发提醒 |
|---|---|---|
| `turns`（回合数） | > 40 | 上下文税(cache_read)累积，考虑新开精简会话或固化 Maestro |
| `actions`（动作数） | > 60 | 批次偏大，按屏合并取证以省 token |
| `minutes`（墙钟时长） | > 30 | 长会话含税成本虚高，考虑分批 |

三项独立判定、可同时触发多条；具体数值见 `.claude/hooks/context_tax_reminder.py` 的
`DEFAULT_THRESHOLDS`。
