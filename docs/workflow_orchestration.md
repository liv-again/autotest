# sixgill 统一执行流程入口

`tools/workflow.py` 是完整任务的统一入口。必须显式选择一种模式；编排器复用现有 Runner、站点对比控制器、导航探测器和结果工具，不修改 Runner 的设备执行行为。

## 三种模式

| 模式 | 用途 | 完成时产物 |
|---|---|---|
| `standard` | 一轮 Excel 驱动的完整自测 | `results.final.json`、`cases_AI自测结果.xlsx`、`workflow_report.md` |
| `site_compare` | 新站点与旧站点分别跑完整标准流程，再逐用例比较 | 两轮各自的正式结果和工作簿、`comparison.json`、`comparison/<source-stem>_对比结果.xlsx`、`comparison/site_compare_report.md` |
| `navigation_probe` | 探测代表用例的导航和目标页，不执行业务动作 | `navigation_probe_execution.json`、`navigation_probe_report.md` |

工作流状态保存在 `runs/workflows/<workflow-id>/workflow_state.json`。每次阶段转换会原子写入状态和历史；等待 Agent 或用户的阶段会返回 `waiting`，不能视为完成。启动时会冻结用例文件和显式 Profile 的 SHA256；后续阶段发现文件内容变化会拒绝续跑。
输入为旧版 `.xls` 时，执行仍使用原文件；回填前会在运行目录生成 `source_for_annotation.xlsx`，再对该副本执行严格回填。

## 标准流程

从 sixgill 项目目录启动，使用已生成并校验的 Agent Action Plan：

```powershell
python tools/workflow.py start `
  --mode standard `
  --app <app-slug> `
  --source <cases.xlsx> `
  --action-plan <agent_action_plan.json>
```

Runner 完成首轮后，工作流停在 `WAITING_FOR_REVIEW`。由复核 Agent 根据队列产出 `llm_reviews.json`，继续：

```powershell
python tools/workflow.py continue --id <workflow-id> --reviews <llm_reviews.json>
```

编排器会合并复核、生成严格首轮结果并规划复测。复测队列为空时，自动严格回填 Excel 并生成报告；队列非空时停在 `WAITING_FOR_RETEST_PLAN`。为队列生成 Action Plan 后继续：

```powershell
python tools/workflow.py continue --id <workflow-id> --retest-action-plan <retest_action_plan.json>
```

也可以用 `--llm-retest` 让 Runner 从实时设备观察生成复测计划；该方式要求桌面 Agent 宿主已注册 Session Factory。

Runner 完成复测后，若首轮或复测清单的 `execution_manifest.llm_review_required` 为 true，或存在缺少 `reason`/`blocker` 的“待验证/待数据”记录，工作流会停在 `WAITING_FOR_RETEST_REVIEW`。复核 Agent 根据 `retest/llm_review_queue.json` 生成 `llm_reviews.json` 后继续：

```powershell
python tools/workflow.py continue --id <workflow-id> --reviews <retest/llm_reviews.json>
```

编排器先合并逐行复核，再调用 `retest_results.py merge` 检查复测队列覆盖并与首轮记录合并。要求复核时，不能直接把原始 `retest_execution.json` 作为最终复测结果。若执行清单未要求复核且待验证记录已有原因，则沿用自动合并路径。最终结果通过严格校验后才回填 Excel 并生成报告；门禁未通过时工作流不会标记完成，可修复产物后继续。

如果设备执行中断，`status` 显示 `BLOCKED_EXECUTION` 时可使用：

```powershell
python tools/workflow.py continue --id <workflow-id> --resume-execution
```

## 站点切换

```powershell
python tools/workflow.py start `
  --mode site_compare `
  --app <app-slug> `
  --source <cases.xlsx> `
  --action-plan <new-site-action-plan.json>
```

新站点的完整标准流程结束后，工作流停在 `WAITING_FOR_OLD_SITE`。人工切换 App 到旧站点后，显式确认并提供旧站点的 Action Plan：

```powershell
python tools/workflow.py continue `
  --id <workflow-id> `
  --confirm-old-site `
  --action-plan <old-site-action-plan.json>
```

旧站点也要完成完整标准流程。随后工作流停在 `WAITING_FOR_COMPARISON`，Agent 根据 `workflow_state.json` 中的 `compare_inputs` 及其真实证据生成比较文件，再提交：

```powershell
python tools/workflow.py continue --id <workflow-id> --comparison-file <comparison.json>
```

提交后，工作流校验逐 Case 覆盖与状态，再调用现有 `annotate_excel.py` 将比较结论回填到原用例 Excel，并在控制器提供的 `comparison` 目录生成 `<source-stem>_对比结果.xlsx`。工作簿包含逐行状态、实测结论、证据和汇总页；有可用图片证据时会嵌入截图。只有对比 JSON、Excel 回填和报告都生成成功，工作流才会标记完成。

站点控制器仍是 `runs/site_compare/state.json` 的唯一写入者；统一入口在每次站点状态转换前先查询其状态。New 和 Old 继续使用两个独立输出目录。

## 导航探测

```powershell
python tools/workflow.py start `
  --mode navigation_probe `
  --app <app-slug> `
  --source <cases.xlsx> `
  --action-plan <agent_action_plan.json>
```

可用一个或多个 `--case Sheet!行号` 限定探测行。导航探测只做 setup、目标页门禁和截图；探测运行记录会标明 `execution_scope=navigation_probe`，不进入标准结果复核、复测流程，也不能当作业务用例通过报告。

查询状态：

```powershell
python tools/workflow.py status --id <workflow-id>
```

`start` 必须传 `--mode` 和经过校验的 `--action-plan`；输出目录必须在项目目录内且为空。兼容固定规则执行器仍可用于独立迁移诊断命令，但不能通过完整工作流入口生成正式完成状态。
