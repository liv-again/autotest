# Site Compare 工作协议

Site Compare 是现有 sixgill Runner 外围的状态控制层。它不操作设备、不执行 ADB、不生成 action plan、不调用 LLM，也不改变 Runner、Retest、Gate、Review 或结果 schema。

## 固定边界

控制器固定使用以下项目内路径：

```text
runs/site_compare/state.json
runs/site_compare/new/
runs/site_compare/old/
runs/site_compare/comparison/
```

New 和 Old 必须是两个独立的 Runner 输出目录，Old 绝不能对 New 使用 `--resume`。控制器只接受现有 sixgill 正式产物，不把中间截图或暂停记录当成正式结果。

## App 与 source

真实运行时必须把 App 作为执行上下文持久化：

```powershell
python tools/site_compare_ctl.py init --app zhongyuan --source <项目内的校验Excel>
```

如果不想在指令中传 Excel 路径，可以在对应 `apps/<slug>/app.yaml` 增加外围配置字段：

```yaml
site_compare_source: ../../某个站点对比用例.xlsx
```

路径以该 `app.yaml` 所在目录为基准。若未配置 `site_compare_source`，控制器会复用已有 `default_source`；两者都没有时，`init` 会拒绝启动而不会猜测文件。这个字段是必要的配置扩展，因为现有 `zhongyuan/app.yaml` 当前没有 `default_source`，而且新旧两次执行必须绑定同一份 Excel。

`app` 会写入 `state.json`，后续 `status`、`start-old` 和 `compare-inputs` 都会返回它；因此会话重启后不依赖聊天记忆。Controller 不负责替 Runner 验证 App 的包名或设备环境，实际执行仍使用 Runner 的 `--app`。

## 命令协议

每次开始动作前先执行：

```powershell
python tools/site_compare_ctl.py status --json
```

新站点阶段：

```text
NO_ACTIVE_WORKFLOW --init--> RUNNING_NEW
RUNNING_NEW --finish-new--> WAITING_FOR_OLD_SITE
```

`finish-new` 会检查 New Run 的 `execution_manifest.json`、`execution_records.json` 和正式最终结果，并复用现有 execution quality gate；同时保存 source hash、manifest hash 和 selected-case hash。返回 `STOP_AND_ASK_USER_TO_SWITCH_SITE` 后必须停止，提示用户手动切换旧站点。

旧站点阶段：

```text
WAITING_FOR_OLD_SITE --用户明确确认后 start-old--> RUNNING_OLD
RUNNING_OLD --finish-old--> READY_TO_COMPARE
```

`start-old` 会再次计算 source SHA256；source 变化就拒绝继续。Old Run 完成时必须通过同样的完整性门，并且 selected case 范围 hash 必须与 New 相同。

比较阶段：

```text
READY_TO_COMPARE --compare-inputs--> 读取文件并逐 Case 比较
READY_TO_COMPARE --complete--> COMPLETED
```

`compare-inputs --json` 返回真正的 New/Old 最终结果文件、两份 `execution_records.json` 和 `comparison.json` 输出路径。Codex 负责按真实证据逐 Case 生成 `MATCH`、`MISMATCH` 或 `PENDING`，不能按 Case ID 固定分类，也不能用固定列表代替 LLM 判断。

`complete` 只接受控制器返回的 comparison 目录内文件，并检查：JSON 可解析、`version=1`、每个 New/Old Case 恰好一个比较记录、无未知或重复 Case、状态合法且有 reason。

## 比较结果 Excel

使用 `workflow.py continue --comparison-file <comparison.json>` 提交比较结果后，统一工作流会调用现有 `tools/annotate_excel.py`，把比较记录回填到原始用例文件，并在控制器返回的 comparison 目录生成 `<source-stem>_对比结果.xlsx`。工作簿保留源用例表，追加 AI 状态、实测结果、证据和时间列，并生成 `🤖AI自测汇总` 页；`MATCH`、`MISMATCH`、`PENDING` 分别映射为 `✅通过`、`❌不通过`、`🟡待验证`，可用的新旧站点截图会作为证据嵌入。

这个工作簿是站点对比模式的必需产物。工作流只有在每条比较记录都成功回填、工作簿和报告均存在后才会标记 `COMPLETED`。如果回填失败，可用同一份 comparison.json 重试；控制器已完成比较但统一工作流尚未完成时，工作流会识别该文件并重试 Excel 生成。

## 正式结果选择

控制器沿用仓库现有结果命名优先级：

```text
results.final.semantic.annotate.json
results.final.semantic.json
results.final.annotate.json
results.final.json
results.reviewed.json
results.json
```

只选择 Run 目录中已经存在的第一个文件；不会创建第二套结果解析规则，也不会把 `execution_records.json` 冒充正式最终结果。

## Comparison 最小结构

```json
{
  "version": 1,
  "source": "cases/market_data_compare.xlsx",
  "cases": [
    {
      "case_key": "行情!15",
      "status": "MATCH",
      "reason": "证据明确显示新旧站点字段一致。",
      "new_evidence_used": [],
      "old_evidence_used": [],
      "differences": []
    }
  ]
}
```

`case_key` 应优先使用仓库真实的 `sheet!row` 或 `case_id`。控制器也兼容示例中的 `sheet:row` 展示形式，但不会根据相似名称猜测身份。
