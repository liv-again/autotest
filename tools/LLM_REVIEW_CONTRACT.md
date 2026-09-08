# 外部 LLM 复核契约

`llm_review_queue.json` 是一次运行的唯一复核入口。Codex 和 OpenCode 是二选一的复核 Agent，不能直接改写执行记录或截图；复核 Agent 只读取队列中的事实和证据，并输出 `llm_reviews.json`。

## 运行链路

```text
execution_records.json
        ↓ 生成
llm_review_queue.json
        ↓ Codex 或 OpenCode 逐行复核
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
    "name": "Codex",
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

`agent.name` 只能是 `Codex` 或 `OpenCode`，`model` 和 `prompt_version` 不能为空。每个 `reviews` 项必须带当前队列中的 `case_id` 以及 `target_page_match`、`action_effect_match`、`expected_result_match`、`confidence`、`visible_facts`、`reason` 和 `status`。

## 合并时的硬校验

- `run_id` 不一致：拒绝，说明不是本次运行。
- `queue_id` 或 `queue_sha256` 不一致：拒绝，说明复核结果不是当前队列生成的。
- 执行记录规范化摘要不一致：拒绝，防止执行记录被替换。
- 截图路径、文件大小或 SHA-256 不一致：拒绝，防止截图被替换。
- 队列被手工修改：拒绝。
- Agent 不是 Codex/OpenCode，或缺少模型/提示词版本：拒绝。
- 用例集合多出、缺少或重复：拒绝。

确定性执行阻塞仍然是终态，LLM 只能解释，不能把阻塞升级为通过。
