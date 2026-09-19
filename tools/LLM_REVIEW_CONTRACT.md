# Agent 执行计划与逐行复核契约

## 执行动作计划（先于设备执行）

Excel/profile 的读取和层级归一化只生成当前 Agent 的输入上下文，不会把
Excel 的“操作描述”直接交给固定字符串解析器。当前选定的 Agent 必须先产出
`agent_action_plan.json`，再启动执行器：

```powershell
python tools/agent_plan.py context `
  --source <cases.xlsx> `
  --profile apps/<app>/profile.yaml `
  --out <run>/agent_context.json

# Agent 读取 context 后生成 agent_action_plan.json，再由执行器校验
python tools/agent_plan.py validate `
  --source <cases.xlsx> `
  --plan <run>/agent_action_plan.json

python tools/_run_three_sheets.py `
  --source <cases.xlsx> `
  --action-plan <run>/agent_action_plan.json `
  --output <run>
```

计划必须逐行提供 `navigation`、`actions` 和可观察的 `target_page`。动作
只能是 `tap_text`、`tap_id`、`tap_xy`、`tap_bbox`、`type_text`、`key`、
`swipe`、`rotate`、`wait`、`observe`、`assert_text`、`assert_id` 等低层
原语；不能提供自由格式 shell 命令，也不能只提供 Excel 原文。执行器只
负责调用这些原语、采集 UI 树和截图，不负责理解用例语义。没有
`--action-plan` 时，执行器会拒绝启动；旧固定规则解析仅能通过显式
`--legacy-deterministic` 做迁移诊断，并不能称为 Agent 驱动执行。

Agent 计划产生的成功动作不会直接变成通过。执行器先记录为
`🟡待验证`，然后由 Agent 读取该行的 `action_trace`、执行后页面观察、
独立截图和 Excel `expected`，生成下面的 `llm_reviews.json`。确定性页面、
动作或取证失败仍然是阻塞，不能被复核升级为通过。

`llm_review_queue.json` 是一次运行的唯一复核入口。复核 Agent 不能直接改写执行记录或截图；它只读取队列中的事实和证据，并输出 `llm_reviews.json`。

队列中的 `review_agent_default` 来自本次执行清单的
`execution_manifest.agent_binding.reviewer`。在没有角色级覆盖时，
`reviewer.agent`/`reviewer.model` 与 `agent_action_plan.json` 的
`planner.agent`/`planner.model` 相同。复核 Agent 默认应使用相同的 Agent 和模型，
但必须创建独立的只读会话；如果明确使用了不同的 Agent 或模型，必须在
`llm_reviews.json.agent` 中记录实际值。规划、复测和复核的 prompt_version 可以不同，
因为三者承担的任务不同。

复测记录中的 `llm_retest.session` 只描述 Queue 级 whole-case 复测事实。复核 Agent 不得沿用该
session_id、继续控制设备或把复测的 `pass` 直接当作最终结论；它必须重新读取当前
队列中的截图、UI 树、动作轨迹和 Excel `expected`，独立给出判断理由。

## 运行链路

```text
execution_records.json
        ↓ 生成
llm_review_queue.json
        ↓ 选定的复核 Agent 逐行复核
llm_reviews.json
        ↓ tools/llm_review_results.py 校验绑定和证据
results.reviewed.json
```

合并命令必须同时指定三份输入：

```powershell
python tools/llm_review_results.py merge `
  --input <run>/execution_records.json `
  --queue <run>/llm_review_queue.json `
  --reviews <run>/llm_reviews.json `
  --out <run>/results.reviewed.json
```

## `llm_reviews.json` 必填元数据

复核 Agent 必须原样复制队列的 `queue_binding` 中以下字段到 `review_binding`：

```json
{
  "schema_version": "1.1",
  "agent": {
    "name": "当前实际使用的 Agent",
    "model": "实际使用的模型名称",
    "prompt_version": "row-review-v1"
  },
  "review_binding": {
    "run_id": "从 queue_binding 复制",
    "queue_id": "从 queue_binding 复制",
    "queue_sha256": "从 queue_binding 复制",
    "execution_document_sha256": "从 queue_binding 复制",
    "evidence_manifest_sha256": "从 queue_binding 复制"
  },
  "reviews": []
}
```

`agent.name` 必须是本次实际使用的非空 Agent 名称，可以是 Codex、OpenCode、Trae、Claude 或其他接入的 Agent；不得伪造或留空。`model` 和 `prompt_version` 不能为空。每个 `reviews` 项必须带当前队列中的 `case_id` 以及 `target_page_match`、`action_effect_match`、`expected_result_match`、`expected_checks`、`confidence`、`visible_facts`、`reason` 和 `status`。`expected_checks` 必须逐项拆解当前 Excel `expected`，每项包含 `criterion`、`matched`、`observed` 和非空 `evidence_refs`；所有检查项为 true 才能把 `expected_result_match` 写成 true，任一 false 写成 false，存在未知项才写成 null。`reason` 必须是非空、基于当前行截图与执行事实的具体判断理由，并引用至少一个检查项或观察事实，不能只写“通过”“正常”“符合预期”，也不能套用与当前 `expected` 无关的排序、刷新、切换或数据完整性理由。

合并复核结果时，执行器会把该理由写入每条结果的 `actual`，固定保留以下三段：

```text
AI执行步骤：...
操作结果：...
判断理由：判定为✅通过/❌不通过/🟡待验证/⛔阻塞。<具体理由>
```

缺少非空 `reason`，或最终 `actual` 缺少上述任一段落，严格结果门禁会拒绝生成正式 Excel 结果。

## 合并时的硬校验

- `run_id` 不一致：拒绝，说明不是本次运行。
- `queue_id` 或 `queue_sha256` 不一致：拒绝，说明复核结果不是当前队列生成的。
- 执行记录规范化摘要不一致：拒绝，防止执行记录被替换。
- 截图路径、文件大小或 SHA-256 不一致：拒绝，防止截图被替换。
- 队列被手工修改：拒绝。
- Agent 名称为空，或缺少模型/提示词版本：拒绝。
- 用例集合多出、缺少或重复：拒绝。

确定性执行阻塞仍然是终态，LLM 只能解释，不能把阻塞升级为通过。
