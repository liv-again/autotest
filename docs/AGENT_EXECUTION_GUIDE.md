# sixgill Agent 执行手册

> 本文档是 sixgill 的交接文档和运行手册。目标是让一个没有阅读全部源码的 Agent，在读取本文档后，能够正确完成一次 Excel 驱动的 Android App 自测，并知道每个阶段的输入、输出、停止条件和后处理方式。
>
> 本文档描述当前仓库的实际执行链路。项目规范中的安全要求高于任何示例命令；如果规范和当前代码不一致，应停止并报告，不要自行放宽门禁。

## 1. 先读什么

开始任何 Excel 驱动 Android App 的任务前，按下面顺序读取：

1. 本文档 `docs/AGENT_EXECUTION_GUIDE.md`。
2. 根目录 [AGENTS.md](../AGENTS.md)，其中包含跨 App、Sheet、模块的硬约束。
3. `.claude/skills/app-selftest/SKILL.md`。
4. `.claude/skills/app-selftest/references/execution-lessons.md`。
5. 与本轮有关的参考文档：
   - `references/workflow.md`：主工作流。
   - `references/safety-policy.md`：交易安全策略。
   - `references/tiering.md`：用例分档。
   - `references/pitfalls.md`：已知坑。
   - `references/explore.md`：画像缺失或版本变化时的探索流程。
6. 目标 App 的 `apps/<app>/app.yaml`、`profile.yaml`、`prerequisites.yaml`，以及由 YAML 派生的 `画像.md`、`前置条件.md`、`速览.md`。
7. 若使用视觉锚点，再读取目标 App 的 `visual_anchors.yaml` 和相关参考图。

画像中的 Markdown 是派生文档，不是权威数据源。机器读取和修改时以 `profile.yaml`、`prerequisites.yaml` 为准；不要直接手工修改 `画像.md`、`前置条件.md` 或 `速览.md`。

## 2. 项目是什么

sixgill 是一个以 Excel 测试用例为输入、以 Android 真机为执行对象的 Agent 驱动自测框架。它不是“把 Excel 的操作描述翻译成固定点击”的脚本，标准路径要求：

```text
Excel 用例
  → module_plan.json：结构化事实和页面分组
  → agent_context.json：交给 Agent 的上下文
  → agent_action_plan.json：Agent 生成的低层动作计划
  → execution_manifest.json：冻结本轮范围和执行绑定
  → ADB/UIAutomator 真机执行
  → execution_records.jsonl/json：逐行证据和状态
  → LLM 逐行复核
  → results.json：质量门后的正式结果
  → 复测/异常分析
  → 回填 Excel、画像候选和 metrics
```

核心设计原则：

- Excel 是测试事实来源，不是动作执行脚本。
- Agent 负责生成结构化低层动作，运行器只执行经过校验的动作。
- 页面导航和当前行的业务动作必须分开。
- 每个 Excel 行是一个独立执行单元，即使多行位于同一页面。
- 页面分组只能复用连续页面组的导航上下文，不能合并业务动作。
- 页面目标必须经过可观察的 Gate 校验，不能只凭“点击成功”判定进入页面。
- 每行完成后立即写 journal、截图和证据，不能整批结束后一次性补写。
- LLM 不能覆盖确定性导航失败、动作失败或缺证据导致的阻塞。
- 画像候选只能经过审核、schema 校验和 lint 后才能回写正式画像。

## 3. 目录和职责

| 路径 | 职责 | Agent 是否通常需要读取 |
|---|---|---|
| `tools/module_planner.py` | 读取 Excel、归一化层级、生成模块和页面组上下文 | 是 |
| `tools/agent_plan.py` | 生成 Agent 上下文、校验 Agent 动作计划 | 是 |
| `tools/agent_contract.py` | 动作类型、按键、方向和参数约束 | 需要规划动作时读取 |
| `tools/_run_three_sheets.py` | 通用真机执行器、journal、截图、队列 | 是 |
| `tools/droid.py` | ADB/UIAutomator 驱动和页面观察 | 需要现场操作时读取 |
| `tools/app_adapter.py` | App 通用适配层 | 需要理解 App 启动/重置时读取 |
| `apps/<app>/app.yaml` | 包名、版本、Adapter、Profile 路径、用例默认路径 | 是 |
| `apps/<app>/profile.yaml` | 页面入口、能力、页面链路和验证状态 | 是 |
| `apps/<app>/prerequisites.yaml` | 账户、证券、权限和前置数据 | 按用例读取 |
| `tools/safety/` | 环境认证、提交保护、价格规则、恢复状态机 | 交易用例必须读取 |
| `tools/execution_journal.py` | 逐行持久化、断点续跑、完整性 | 结果处理时读取 |
| `tools/execution_gate.py` | 执行完整性门禁 | 结果处理时读取 |
| `tools/results_quality.py` | `actual`、证据和语义结果质量门 | 结果处理时读取 |
| `tools/llm_review_queue.py` | 生成逐行复核队列 | 测后读取 |
| `tools/llm_review_results.py` | 校验并合并 LLM 复核结果 | 测后读取 |
| `tools/build_results.py` | 生成严格 `results.json` | 测后读取 |
| `tools/retest_results.py` | 生成和合并未通过用例复测 | 需要复测时读取 |
| `tools/annotate_excel.py` | 将结果和截图回填 Excel | 测后读取 |
| `tools/profile_feedback.py` | 生成带证据的画像候选 | 测后读取 |
| `tools/reback.py` | 将审核后的候选 upsert 回 YAML | 明确批准画像变更时读取 |
| `tools/derive_docs.py` | 从 YAML 派生 Markdown | 画像回写后运行 |
| `tools/lint_profile.py` | 检查重复、过期和 Markdown 漂移 | 画像回写后运行 |
| `maestro/` | 确定性 Maestro 流程，主要是交易回放 | 仅 Maestro 任务读取 |
| `runs/`、`output/` | 历史或本轮运行产物 | 只读取当前 run |

详细的通用执行器约束见 [tools/APP_ADAPTER_CONTRACT.md](../tools/APP_ADAPTER_CONTRACT.md)，Agent 计划和复核绑定见 [tools/LLM_REVIEW_CONTRACT.md](../tools/LLM_REVIEW_CONTRACT.md)。

## 4. 执行前的前置条件

### 4.1 本地环境

项目没有独立的打包入口或 console script，命令都从仓库根目录 `sixgill` 执行：

```powershell
Set-Location D:\autotest\autotest\sixgill
$env:PYTHONUTF8 = "1"
```

Python 依赖见 [requirements.txt](../requirements.txt)：

```powershell
python -m pip install -r requirements.txt
```

代码支持 `.xls`，当前环境需要额外确认 `xlrd` 已安装；如果执行 `.xls` 用例，应先运行：

```powershell
python -c "import xlrd; print(xlrd.__version__)"
```

最少需要：

- Python 3.11 或兼容版本。
- `PyYAML`、`jsonschema`、`pytest`、`openpyxl`、`xlrd`。
- Android SDK Platform Tools 中的 `adb`。
- 一台已开启 USB 调试、已授权、屏幕可用的 Android 真机。
- 目标 App 已安装，且登录状态、账户和前置证券数据已准备好。
- 截图不可被 `FLAG_SECURE` 阻止；否则证据图可能为 0 字节。

Maestro 流程另外需要 Maestro、Java 11+、满足流程条件的模拟账户和交易时段；Maestro 不是通用 Agent Runner 的替代品。

### 4.2 前置交付物

正式执行前必须确认前置任务已经产生：

- `selection.yaml`
- `scope_hash`
- 本轮前置清单，如 `本轮前置.yaml`/`本轮前置.md`

这些文件用于冻结本轮范围、避免 Agent 临场扩大测试范围。缺失或过期时，先完成前置准备，不要直接执行全量用例，也不要现场凭经验重新选择范围。

前置清单必须至少说明：

- 本轮 App、包名和版本。
- 本轮 Excel 和 Sheet 范围。
- 账户别名和账户类型。
- 证券代码、证券属性、权限或持仓要求。
- 缺失数据及其处理方式。
- `scope_hash` 或等价的范围指纹。

### 4.3 设备探测

必须先使用项目封装的探测命令：

```powershell
python tools/droid.py wait-device
```

该命令最多探测 3 次，每次间隔 30 秒。三次都未发现 `state=device` 的设备时，退出并停止任务；不要无限等待或循环重试。

如果有多台设备，记录实际使用的 serial，并在运行命令中显式传递：

```powershell
python tools/_run_three_sheets.py --device <adb-serial> ...
```

开始执行前建议检查：

```powershell
python tools/droid.py current
python tools/droid.py screen
```

确认当前前台包名、页面可观察、没有遮挡弹窗或系统授权框。

### 4.4 App、版本和环境认证

根据设备实际包名和版本读取 `apps/<app>/app.yaml`，匹配：

- `packages`
- `runtime_package`
- `verified_versions`
- `compatibility`
- `environment`
- `profile`
- `prerequisites`

版本不在兼容区间、包名不匹配或无法确认版本时，不得静默套用旧画像，应停止并标记 `revalidation_required`。

交易或提交类用例还要读取 `apps/<app>/env.yaml`，调用 [tools/safety/env_auth.py](../tools/safety/env_auth.py) 的 `verify_env()` 推导执行模式：

- `confirm_only`：只允许到确认阶段，不允许最终提交。
- `simulated_submit`：仅限已认证的模拟环境。
- `live_submit`：策略层概念，当前首版执行路径不实现。

任一环境卫生条件失败都应回退到 `confirm_only`。当前仓库的权威安全说明是 `.claude/skills/app-selftest/references/safety-policy.md`。

## 5. 标准执行链路

标准全量自测严格按以下阶段执行：

```text
A. 冻结范围
 → B. 生成 module_plan
 → C. 生成 agent_context
 → D. Agent 生成 action_plan
 → E. 校验 action_plan
 → F. 真机逐行执行
 → G. 生成异常/复核/画像候选队列
 → H. LLM 逐行复核
 → I. 生成严格 results
 → J. 复测未通过用例
 → K. 回填 Excel
 → L. 审核画像、派生文档、lint、metrics
```

### A. 冻结本轮范围

使用前置任务生成的 selection 和 scope hash。执行器的 `--sheet` 只能缩小范围，不能在运行中扩大范围。

如果没有 selection 产物，至少要在人工启动前明确记录：

- 输入 Excel 的绝对路径。
- App slug。
- Sheet 列表。
- 每个 Sheet 的行范围或 case ID。
- 本轮是 `full`、`sample`、`retest` 还是 `navigation_probe`。

### B. 生成模块计划

`module_planner.py` 只负责事实整理，不负责生成点击动作：

```powershell
python tools/module_planner.py `
  --source <绝对路径\cases.xlsx> `
  --profile apps/<app>/profile.yaml `
  --out <run>\module_plan.json
```

只跑指定 Sheet 时可以重复 `--sheet`：

```powershell
python tools/module_planner.py `
  --source <绝对路径\cases.xlsx> `
  --sheet 行情 `
  --sheet 基金 `
  --profile apps/<app>/profile.yaml `
  --out <run>\module_plan.json
```

生成的 `module_plan.json` 包含：

- 原始 Excel 行及 `source_order`。
- 规范化后的一级至四级目录。
- `entry`、前置条件、操作描述、期望结果。
- `page_group_key` 和页面组上下文。
- App Profile 入口提示。
- 本轮 `execution_manifest` 的候选范围。

页面组必须按照连续的导航上下文分组。以下两种上下文不能合并：

- 沪深 A 股 → 个股详情。
- 港股 → 个股详情。

同一目标页面名称不代表可以复用同一导航路径。

### C. 生成 Agent 上下文

```powershell
python tools/agent_plan.py context `
  --source <绝对路径\cases.xlsx> `
  --profile apps/<app>/profile.yaml `
  --out <run>\agent_context.json
```

如果只处理指定 Sheet，重复添加 `--sheet`。

Agent 必须读取 `agent_context.json`，而不是重新直接解析整个 Excel。上下文中包含：

- `module_plan` 事实。
- Excel `source_sha256`。
- Profile `profile_sha256`。
- App 画像相关入口。
- `generic_planning_knowledge`。
- 当前规划模型默认值。
- 生成 action plan 的提示约束。

上下文中的模型默认值只是项目配置。action plan 中必须记录实际使用的 Agent、模型 wire ID 和 prompt version。

### D. Agent 生成 `agent_action_plan.json`

Agent 读取 context 后，必须生成结构化 JSON 文件。不能把 Excel 原文、Shell 命令或自由格式自然语言交给执行器。

计划最小结构如下：

```json
{
  "schema_version": "1.0",
  "plan_type": "agent_action_plan",
  "planner": {
    "agent": "实际使用的 Agent 名称",
    "model": "实际使用的模型 wire ID",
    "prompt_version": "planner-v1"
  },
  "source": {
    "path": "cases.xlsx",
    "sha256": "与当前 Excel 完全一致的 64 位 SHA-256"
  },
  "app_profile": {
    "path": "apps/<app>/profile.yaml",
    "sha256": "与当前 profile 完全一致的 64 位 SHA-256"
  },
  "cases": [
    {
      "case_id": "行情-row-12",
      "sheet": "行情",
      "row": 12,
      "page_group_id": "行情-group-001",
      "page_group_key": "行情|沪深京|个股详情|入口",
      "navigation_source": "profile",
      "navigation_status": "verified",
      "navigation_policy": "required",
      "profile_entry_key": "profile-entry-key",
      "navigation": [
        {"type": "tap_text", "text": "行情"}
      ],
      "recovery_navigation": [
        {"type": "key", "key": "BACK"}
      ],
      "target_page": {
        "gate_mode": "composite",
        "all_ids": ["stable_page_id"],
        "all_text": ["目标页标题"],
        "orientation": "portrait",
        "description": "目标页面的可观察身份"
      },
      "actions": [
        {"type": "tap_text", "text": "查询"}
      ],
      "expected_observations": ["页面显示目标结果"]
    }
  ]
}
```

计划要求：

- `schema_version` 必须是 `1.0`。
- `plan_type` 必须是 `agent_action_plan`。
- `planner.agent`、`planner.model`、`planner.prompt_version` 必须非空。
- `source.sha256` 必须匹配当前 Excel。
- 提供 Profile 时，`app_profile.sha256` 必须匹配当前 Profile。
- 每个本轮执行 case 都要有一项，不能漏行、重复或跨 Sheet 复用页面组。
- `navigation` 只放公共导航，不放当前行的业务动作。
- `actions` 必须是显式数组，不能让运行器再次解析 Excel 文本。
- `target_page` 至少有一个可观察条件。
- `navigation_source=profile` 或 `profile_plus_llm` 时必须提供存在于 Profile 的 `profile_entry_key`。
- Profile 没有明确路径时使用 `navigation_source=llm_inferred`，并将 `navigation_status` 标为 `unverified` 或 `partial`，不能只点击目标文字。

允许的动作类型和参数：

| 动作 | 必填字段 | 约束 |
|---|---|---|
| `tap_text` | `text` | 非空 |
| `tap_id` | `id` | 非空 |
| `tap_xy` | `x`, `y` | 非负整数 |
| `tap_bbox` | `x1`, `y1`, `x2`, `y2` | `x2>x1`、`y2>y1` |
| `type_text` | `text` | 非空 |
| `key` | `key` | `BACK/HOME/ENTER/DEL/TAB/ESC/MENU` 等白名单 |
| `swipe` | `x1,y1,x2,y2` | `duration_ms` 在 1 到 10000 |
| `rotate` | `orientation` | `portrait` 或 `landscape` |
| `wait` | `seconds` | 0 到 10 秒 |
| `observe` | 可选 `target` | 只观察，不是通过条件，也不是动作兜底 |
| `assert_text` | `text` | 执行断言 |
| `assert_id` | `id` | 执行断言 |

`type_text` 输入非 ASCII 文本（包括中文）时，执行器不会调用容易在部分设备上
触发 `InputText.sendText` 异常的 `adb shell input text`，而是按以下顺序使用
ADB Keyboard：检查/安装 `com.android.adbkeyboard`、启用并切换到
`com.android.adbkeyboard/.AdbIME`，然后发送 `ADB_INPUT_TEXT` 广播。运行器可通过
`--adb-keyboard-apk <路径>` 或环境变量 `SIXGILL_ADB_KEYBOARD_APK` 提供 APK；
设备未安装且未提供 APK 时，该输入动作会明确失败并记录原因。

目标页 Gate 支持 `exact`、`composite`、`weak`：

- `exact` 必须包含稳定身份条件，如 `all_ids`、`selected_text` 或 `selected_ids`。
- `composite` 至少包含两个正向条件。
- 仅有普通文本时只能是 `weak`，不能用来跳过已声明的导航。
- 可组合 `all_text`、`any_text`、`not_text`、`all_ids`、`any_ids`、`not_ids`、`selected_text`、`selected_ids` 和方向条件。

### E. 校验 Agent 计划

```powershell
python tools/agent_plan.py validate `
  --source <绝对路径\cases.xlsx> `
  --plan <run>\agent_action_plan.json `
  --profile apps/<app>/profile.yaml
```

校验失败时不能启动设备执行。常见失败原因：

- Excel 在生成计划后被修改，导致 hash 不一致。
- Profile 在生成计划后被修改。
- action plan 缺少某个 Excel 行。
- `case_id`、Sheet 或行号不一致。
- 页面组跨 Sheet 复用。
- 目标页只有弱文本条件。
- Profile 来源没有合法 `profile_entry_key`。
- 动作坐标、方向、等待时间或按键非法。

### F. 启动确定性 Agent 执行器

标准命令：

```powershell
python tools/_run_three_sheets.py `
  --app <app-slug> `
  --source <绝对路径\cases.xlsx> `
  --profile apps/<app>/profile.yaml `
  --device <adb-serial> `
  --action-plan <run>\agent_action_plan.json `
  --output <run>
```

例如：

```powershell
python tools/_run_three_sheets.py `
  --app guotou `
  --source "国投行情测试用例(1).xls" `
  --action-plan output\guotou-run\agent_action_plan.json `
  --output output\guotou-run
```

指定 Sheet：

```powershell
python tools/_run_three_sheets.py `
  --app guotou `
  --source "国投行情测试用例(1).xls" `
  --sheet 行情 `
  --action-plan output\guotou-run\agent_action_plan.json `
  --output output\guotou-run
```

执行器默认要求 `--action-plan`。只有两种例外：

- 明确传入 `--legacy-deterministic`，用于旧版固定规则迁移诊断。
- 使用 `--llm-retest --retest-queue` 的独立 LLM 复测路径，可以不传旧 action plan。

不要把 `--legacy-deterministic` 当作标准 Agent 执行方式，也不要因为 action plan 生成困难而回退到旧解析器。

## 6. 真机执行时的状态机

### 6.1 每个模块

模块首次进入时：

1. 归一为竖屏，除非目标用例明确要求横屏。
2. 启动目标 App。
3. 等待 App 页面稳定。
4. 进入模块根页面。
5. 记录模块初始化和导航 trace。

模块内普通行之间优先复用 App 进程和导航上下文，不要每行强制冷启动。

以下情况才允许冷启动：

- 模块切换。
- App 崩溃或卡死。
- 有限次返回无法恢复到模块根页面。
- 页面状态已经无法可靠识别。

### 6.2 每个页面组

页面组只能由连续且导航上下文相同的 Excel 行组成。组内可复用导航策略，但每一行仍须：

1. 恢复组锚点或确认当前状态。
2. 重新检查当前行的目标页面。
3. 必要时执行本行的公共导航。
4. 重新通过目标页 Gate。
5. 只在 Gate 成功后执行本行 `actions`。

上一行留下的页面状态不能直接证明下一行已到达目标页。

### 6.3 每个 Excel 行

固定顺序如下：

```text
状态复位
  → 目标页 Gate
      ├─ 已在目标页：执行本行 actions
      └─ 不在目标页：执行 navigation
                         → 重新 Gate
                             ├─ 成功：执行本行 actions
                             └─ 失败：⛔阻塞，不执行本行动作
  → 本行结果观察/断言
  → 独立截图和证据
  → 追加 execution_records.jsonl
```

状态复位必须清理可能影响当前行的：

- 键盘。
- 搜索框和搜索结果。
- 排序状态。
- 弹窗。
- 详情页。
- 页面栈。
- 错误的横竖屏方向。

目标子页面入口查找必须是有限的：

1. 当前可见区域查找。
2. 未找到则回到顶部再查找。
3. 顶部仍未找到则向下最多查找三个屏幕。
4. 仍未找到则阻塞或待验证，不得无限滑动或盲点坐标。

列表滑动只有在滑动前后第一行股票名称和代码发生变化时，才可判定滑动生效。排序只有在点击前后前两条数据发生变化，并且字段顺序确实反转时，才可判定排序生效。

同一行包含多个入口时，每个入口都要恢复来源页：

```text
恢复来源页 → 找入口 A → 点击 → 校验子页 A
恢复来源页 → 找入口 B → 点击 → 校验子页 B
```

不能在子页面上继续点击只存在于来源页的入口。

### 6.4 动作失败和重试

同一动作最多自动重试 1 次。第二次仍失败时：

- 保留两次动作轨迹。
- 记录失败原因和页面观察。
- 当前行标记 `⛔阻塞` 或 `🟡待验证`。
- 不继续猜坐标、不无限返回、不无限重启。

`observe` 只能产生观察证据，不能把未执行的业务动作伪装成成功，也不能作为未识别动作的兜底。

## 7. 运行产物

执行器会在 `--output <run>` 目录中写入或复制以下文件：

| 文件/目录 | 含义 |
|---|---|
| `module_plan.json` | 当前 Excel/App 事实和页面组计划 |
| `agent_action_plan.json` | 本轮使用的 Agent 计划副本 |
| `execution_manifest.json` | 本轮 run ID、范围、模式、Agent 绑定和统计 |
| `<source>_source.xls/xlsx` | 本轮输入 Excel 副本 |
| `execution_records.jsonl` | 每完成一行立即追加的 journal |
| `execution_state.json` | 当前 journal 状态、已完成数量和剩余数量 |
| `execution_records.json` | journal 完成后的规范化执行文档 |
| `runtime_stats.json` | 模块冷启动、软重置、组复用等运行统计 |
| `shots/` | 当前运行的截图证据 |
| `retest_queue.json` | 首轮 LLM 复核后筛选出的复测队列 |
| `retest_summary.json` | 复测摘要 |
| `retest/` | 复测用例子运行目录 |
| `execution_records.retested.json` | 首轮和阻塞复测合并后的执行记录 |
| `retest_execution.json` | 显式复测运行的约定结果文件名 |
| `exception_queue.json` | 失败、阻塞、待验证和低置信度记录 |
| `profile_feedback.json` | 带证据的画像候选 |
| `llm_review_queue.json` | 逐行 LLM 复核队列 |
| `navigation_probe_execution.json` | `--probe` 模式的执行记录 |

`execution_manifest` 会冻结 `selected_cases` 和 `expected_count`。恢复运行时，已有 manifest 的模式和选择范围必须一致，不能用 `--resume` 换一套 Sheet 或行范围。

## 8. 结果记录要求

每条记录至少要能追溯：

- `sheet`
- Excel 实际 `row`
- `case_id`
- `source_order`
- `execution_order`
- `status`
- `expected`
- `action_trace`
- `page_observation`
- `evidence`
- 判断理由

`actual` 不得只是“执行成功”“截图已保存”或复制 Excel 操作描述。正式结果必须包含三段：

```text
AI执行步骤：基于本行真实 action_trace 的可读步骤
操作结果：截图/UI 观察中实际可见的页面事实
判断理由：说明为什么判定为通过、不通过、待验证或阻塞
```

公共导航写入顶层 `setup_trace`，不能混入当前用例的 `actual`。原始 UI 树放在 `page_observation` 等底层证据字段，不能单独当作业务结果。

以下状态含义固定：

| 状态 | 含义 |
|---|---|
| `✅通过` | 动作实际完成、目标页面/结果可观察、证据足够且复核通过 |
| `❌不通过` | 动作完成但页面事实与预期不一致 |
| `⛔阻塞` | 页面、导航、控件、动作或环境门禁失败，导致本行动作无法可靠完成 |
| `🟡待验证` | 有观察或动作，但语义结果或证据不足，不能判定通过 |
| `☑不适用` | 经过明确规则确认本行不适用于当前范围 |
| `BLOCKED_ENVIRONMENT` | 环境降级导致不能进行允许的提交/撤单；不计入通过率 |

## 9. LLM 逐行复核和正式结果

执行器完成后，先生成复核队列：

```powershell
python tools/llm_review_queue.py `
  --input <run>\execution_records.json `
  --out <run>\llm_review_queue.json
```

复核 Agent 逐条读取队列、截图、UI 事实、动作轨迹和 Excel expected，输出 `<run>\llm_reviews.json`。

复核 Agent 必须独立于规划 Agent 的活动执行会话，不能继续控制设备，也不能直接修改执行记录或截图。复核结果必须复制队列中的绑定信息：

- `run_id`
- `queue_id`
- `queue_sha256`
- `execution_document_sha256`
- `evidence_manifest_sha256`
- 实际 Agent 名称
- 实际模型
- prompt version

合并复核结果：

```powershell
python tools/llm_review_results.py merge `
  --input <run>\execution_records.json `
  --queue <run>\llm_review_queue.json `
  --reviews <run>\llm_reviews.json `
  --out <run>\results.reviewed.json
```

绑定或证据指纹不一致时，命令必须失败。LLM 只能解释事实，不能把确定性阻塞改成通过。

生成正式质量结果：

```powershell
python tools/build_results.py `
  --input <run>\results.reviewed.json `
  --out <run>\results.json
```

正式结果默认要求：

- 有 `execution_manifest`。
- 选中的用例全部有记录。
- 每行有 action trace、观察、独立证据和判断理由。
- `actual` 包含三段结构。
- 不存在跨用例大量复制的通用 actual。
- 不存在空证据路径或当前运行之外的证据。

`--allow-incomplete`、`--allow-missing-evidence` 和 `--allow-missing-judgment-reason` 只用于中间结果或兼容旧产物，不能用于正式交付。

## 10. 阻塞复测

### 10.1 Runner 自动阻塞复测

首轮执行不再自动启动阻塞复测。必须先完成首轮逐行 LLM 复核，再显式生成复测队列；
`--auto-retest-blocked` 仅保留为旧命令的兼容参数，当前流程会拒绝在首轮复核前执行它。

```text
--no-auto-retest-blocked
```

完成首轮执行后，Runner 只会：

1. 保留首轮 `execution_records.json`。
2. 生成 `llm_review_queue.json`。
3. 等待复核 Agent 完成逐行 `llm_reviews.json`。
4. 合并为 `results.reviewed.json`。
5. 再由 `retest_results.py plan` 按复核后的状态生成复测队列。

需要让当前 Agent 重新理解和选择动作时，先使用复核后的结果生成队列，再执行：

```powershell
python tools/retest_results.py plan `
  --results <run>\results.reviewed.json `
  --statuses blocked,pending,fail,partial `
  --out <run>\retest_queue.json

python tools/_run_three_sheets.py `
  --app <app-slug> `
  --source <cases.xlsx> `
  --retest-queue <run>\retest_queue.json `
  --current-agent `
  --no-auto-retest-blocked `
  --output <run>\retest
```

LLM 复测要求 provider-neutral 的独立会话边界；复测 Agent 必须重新读取原始 Excel、当前 Profile 和实时截图/UI 树；旧 action plan 只能作审计参考，不能继续作为动作来源。

### 10.2 显式复测其他未通过状态

首轮结果经 LLM 复核后，可以显式复测 `fail`、`partial`、`pending` 等状态：

```powershell
python tools/retest_results.py plan `
  --results <run>\results.reviewed.json `
  --scope sheet `
  --scope-name <Sheet名称> `
  --out <run>\retest_queue.json
```

`retest_results.py plan` 默认有首轮复核门禁：当结果的
`execution_manifest.llm_review_required=true` 时，每条用例必须已经带有
`llm_review`，否则直接拒绝生成复测队列。这样不能把首轮 Agent Action Plan
执行产生的中间 `🟡待验证` 结果直接当成复测输入。只有兼容旧产物时，才允许显式
传入 `--allow-unreviewed`；正式流程不得使用该选项。

用队列执行：

```powershell
python tools/_run_three_sheets.py `
  --app <app-slug> `
  --source <cases.xlsx> `
  --retest-queue <run>\retest_queue.json `
  --action-plan <run>\agent_action_plan.json `
  --output <run>\retest-run
```

合并两轮结果：

```powershell
python tools/retest_results.py merge `
  --results <run>\results.json `
  --plan <run>\retest_queue.json `
  --retest-results <run>\retest-run\retest_execution.json `
  --out <run>\results.final.json
```

合并后最终可见状态以复测结果为准，但 `attempts` 必须保留首轮和复测两份事实。未通过用例没有完成必要复测时，不要把首轮中间结果当作最终结果。

## 11. 回填 Excel

正式结果生成后，使用通用回填器：

```powershell
python tools/annotate_excel.py `
  --src <cases.xlsx> `
  --results <run>\results.final.json `
  --out <run>\cases_AI自测结果.xlsx `
  --evidence-root <run> `
  --strict
```

如果没有复测，使用 `<run>\results.json`；如果是普通执行并包含阻塞复测，优先使用最终合并结果。

`--strict` 会检查：

- 结果是否都能匹配到 Sheet + row 或用例 ID。
- 证据文件是否存在。
- 结果质量门是否通过。
- AI 实测结果是否包含三段结构。
- `matched` 数量是否等于本轮结果数量。

遇到非标准表头时，增加：

```powershell
--header-row <表头行号>
--case-id-column <列号/Excel列字母/表头名>
--case-name-column <列号/Excel列字母/表头名>
```

回填器不得覆盖源文件。完成后必须检查：

1. 输出文件存在且可打开。
2. 源 Excel 修改时间没有被意外改变。
3. `matched` 数量与 `results.final.json` 中的 case 数一致。
4. 证据图片能在输出 Excel 中显示或能由证据路径打开。

## 12. 画像反哺

运行器会生成 `profile_feedback.json`，也可以显式运行：

```powershell
python tools/profile_feedback.py `
  --input <run>\results.final.json `
  --out <run>\profile_feedback.reviewed.json `
  --app-slug <app-slug> `
  --app-version <app-version>
```

画像候选必须带有：

- App slug 和版本。
- Sheet、Excel 行号和 case ID。
- 页面上下文。
- UI 观察。
- action trace。
- 独立截图证据。
- 运行目录和 evidence 路径。

候选不能直接覆盖 `profile.yaml`。只有在 Agent/人工审核通过后，才能使用 `reback.py` 进行结构化 upsert，然后重新派生文档：

```powershell
python tools/derive_docs.py apps/<app>
python tools/lint_profile.py apps/<app>
```

`lint_profile.py` 的重点检查包括：

- 重复 entry key。
- 跨产物重复代码。
- `last_verified` 过期但仍标记 verified。
- YAML 和派生 Markdown 漂移。

不要直接编辑 `apps/<app>/画像.md`、`前置条件.md`、`速览.md`。

## 13. 前置条件提取

如果测试依赖账户、权限或证券属性，先准备一个符合 `prereq_extract.py` 输入格式的 YAML 用例清单，再使用前置条件规则提取本轮数据。该脚本的输入不是 Excel，而是包含 `cases` 数组的 YAML：

```powershell
python tools/prereq_extract.py `
  --cases <run>\prereq_cases.yaml `
  --rules tools/prereq_rules.yaml `
  --prerequisites apps\<app-slug>\prerequisites.yaml `
  --app <app-slug> `
  --market 北交所 `
  --out-yaml <run>\本轮前置.yaml `
  --out-md <run>\本轮前置.md
```

实际参数以当前脚本 `--help` 为准。规则根据用例文本匹配风险警示、退市、普通股票、北交所 ETF、持仓、行情深度、IOPV、两融和申购等条件。提取结果中的 `missing_codes`、`unidentified` 或 `conflict` 非空时，不能直接进入业务执行，必须先人工补齐或裁决。

缺少证券代码、账户权限或前置数据时，不要把用例标为通过。应在前置阶段补齐，或明确标记环境阻塞/不适用。

## 14. 安全交易流程

交易类用例必须先判断当前环境是否允许提交。安全模块的设计流程为：

```text
env_auth.verify_env()
  → 推导 confirm_only/simulated_submit
  → 校验本轮安全约束
  → submit_guard.guard_submit()
  → 允许模拟提交或停在确认阶段
  → recovery.plan_recovery()
  → 查询本轮残留委托并撤单/STOP
```

`guard_submit()` 至少检查：

- mode 是否允许提交。
- `code`、`price`、`qty`、`side` 是否完整。
- 账户 HMAC 是否在白名单。
- 证券代码是否在白名单。
- 数量是否为正整数且不超过上限。
- 价格规则是否为已知规则。
- 行情是否新鲜。
- 买卖价格是否满足非市价规则。

`plan_recovery()` 对本轮提交的委托按 contract number 或代码/方向/数量/价格/时间窗口匹配：

- 唯一匹配且可撤：`CANCEL`。
- 已撤、部撤、已成等终态：不再撤。
- 多条匹配：`STOP`，转人工，禁止猜测。

特别注意：当前源码检查显示，`tools/safety/*` 的安全函数和测试已经存在，但通用 `_run_three_sheets.py` 尚未把 `verify_env()`、`guard_submit()`、`plan_recovery()` 全部接入普通 Agent 动作分发路径。因此在当前状态下，不要声称普通 `tap_text` 已经自动受到交易安全护栏保护。

对交易提交、撤单和两融流程，必须：

1. 确认当前运行版本确实接入安全编排层。
2. 如果未接入，只允许执行到确认页或使用明确的模拟/确定性安全流程。
3. 不执行真实提交，不伪造环境认证，不把 `live_submit` 当作可用模式。
4. 环境、账户、证券或委托匹配存在歧义时立即 STOP。

## 15. App Adapter 和旧路径

### 15.1 通用 Adapter

执行器通过 `--app <slug>` 读取 `apps/<slug>/app.yaml`：

- `packages`：启动候选包。
- `runtime_package`：优先运行包。
- `adapter`：可选专用 Adapter。
- 没有 Adapter 时使用 `GenericAdapter`。
- `module_roots`：为通用 Adapter 提供模块根页面契约。

Adapter 只能处理 App 特有的启动、模块根页、复位、入口和页面谓词；不能在 Adapter 中重新解析 Excel，也不能启动另一个 Agent。

当前 `guotou` 有专用 Adapter，使用部分市场/基金标签坐标和页面谓词；其他 App 主要依赖通用 Adapter 和 YAML Profile。

### 15.2 旧版固定规则

只有在迁移诊断时显式使用：

```powershell
python tools/_run_three_sheets.py `
  --app guotou `
  --source <cases.xls> `
  --legacy-deterministic `
  --output <run>
```

该路径不能代表标准 Agent 驱动执行，也不能成为缺少 action plan 时的默认回退。

### 15.3 Maestro

`maestro/` 中是确定性回放流程，主要针对已知包名、已知页面和交易演练。使用前先读 `maestro/README.md`，确认：

- 设备和 App 包。
- 分辨率和横竖屏。
- 登录状态。
- 交易时段。
- 模拟账户。
- Maestro/Java 版本。

Maestro 坐标流程的兼容性不等于通用 Agent Runner 的兼容性。

## 16. 导航探测模式

如果只想验证 Profile/计划的导航和目标页，不执行 Excel 业务动作，可使用 `--probe` 和探测队列：

```powershell
python tools/_run_three_sheets.py `
  --app <app-slug> `
  --source <cases.xlsx> `
  --probe `
  --probe-queue <probe-queue.json> `
  --output <run>\navigation-probe
```

探测模式只执行 setup、目标页门禁和截图，不执行当前行 `actions`。它生成 `navigation_probe_execution.json`，不能直接当作业务用例通过结果。

## 17. 断点续跑和恢复

### 正常恢复

如果运行被中断：

```powershell
python tools/_run_three_sheets.py `
  --app <app-slug> `
  --source <cases.xlsx> `
  --action-plan <run>\agent_action_plan.json `
  --output <run> `
  --resume
```

恢复前确认：

- 当前 Excel 没有变更。
- 当前 Profile 没有变更。
- `execution_manifest.json` 存在。
- 当前模式与原模式相同。
- Sheet 和 selected cases 与原运行完全相同。
- 设备和 App 版本没有改变。

如果范围、模式、Excel 或 Profile 发生变化，使用新 output 目录，不要强行 `--resume`。

### 暂停时的处理原则

- 以最后一条成功持久化的 journal 为准。
- 只补跑未完成行。
- 不用旧轮次结果补齐当前轮次。
- 不手工删除 journal 中的失败记录来制造完整运行。
- 不重写已完成行的 evidence。

## 18. 常见故障处理

| 现象 | 处理 |
|---|---|
| `必须提供 --action-plan` | 先生成并校验 Agent action plan；不要直接加旧模式参数 |
| source hash 不一致 | 重新生成 context 和 action plan，确认 Excel 未被修改 |
| profile hash 不一致 | 重新生成计划；确认不是执行期间改过 Profile |
| 设备三次探测失败 | 停止任务，检查 USB 调试、授权、ADB 服务和设备状态 |
| 目标页 Gate 失败 | 记录 UI 树和截图，最多执行一次声明的 recovery navigation，仍失败则阻塞 |
| 控件找不到 | 当前区域 → 顶部 → 最多三个屏幕；仍找不到则阻塞/待验证 |
| 截图为 0 字节 | 检查 `FLAG_SECURE`、设备权限、存储空间和截图路径 |
| 中文输出乱码 | 设置 `$env:PYTHONUTF8="1"`，并优先读取 JSON/文件而不是依赖终端输出 |
| strict 回填失败 | 先修复缺失 evidence、actual 三段结构、行匹配或重复结果，不要降低门禁 |
| LLM review 合并失败 | 检查 run/queue/hash/evidence 指纹和 case 集合是否来自同一运行 |
| Profile lint 报 stale | 重新实测或明确降级为 unverified；不要只更新日期伪造验证 |
| 需要无限重试 | 这是错误信号，应进入异常队列或人工分析，不得继续自动点击 |

## 19. 测后指标

运行结束后可以使用：

```powershell
python tools/metrics.py now
python tools/metrics.py tokens <开始时间> <结束时间>
```

`metrics.py` 用于记录 token、缓存读取、动作数和上下文税提醒。它依赖 Agent transcript；如果当前环境没有配置有效 transcript 路径，指标结果可能不完整，不要把缺失 metrics 当作业务通过。

## 20. 新 App 接入

新 App 不要手写 Profile 骨架，优先使用：

```powershell
python tools/init_app.py <slug> `
  --package <android.package.name> `
  --version <version> `
  --aliases <别名1,别名2> `
  --compat-min <min-version> `
  --compat-max-excl <max-version>
```

该命令生成经过 schema 校验的 `app.yaml`、`profile.yaml`、`prerequisites.yaml` 和派生文档，但不会自动生成 `env.yaml`。环境认证必须人工准备，不能把 `env.yaml.example` 直接改名当作真实认证。

新 App 接入至少要完成：

1. 包名和版本范围确认。
2. `app.yaml` 配置。
3. Profile 页面入口和稳定身份特征。
4. 前置账户、权限、证券代码。
5. 必要时实现 Adapter。
6. 生成 action plan 并完成导航探测。
7. 真机执行一批最小冒烟用例。
8. 结果复核、画像候选审核、`reback`、`derive_docs`、`lint_profile`。

## 21. 交付前检查清单

### 执行前

- [ ] 已读取 `AGENTS.md`、自测规范和执行经验。
- [ ] 已确认 `selection.yaml`、`scope_hash` 和本轮前置清单。
- [ ] Excel 路径、App slug、Sheet 和版本范围明确。
- [ ] 设备已通过 `wait-device`，serial 已记录。
- [ ] App 包名和版本与 `app.yaml` 匹配。
- [ ] Profile、前置条件和视觉锚点已按需读取。
- [ ] 交易环境已完成 mode 判断。
- [ ] `module_plan.json` 和 `agent_context.json` 已生成。
- [ ] Agent action plan 已记录实际 Agent、模型和 prompt version。
- [ ] action plan 已通过 source/profile hash 和 case 完整性校验。

### 执行中

- [ ] 按 source order 升序逐行执行。
- [ ] 每行先做目标页 Gate，再执行本行动作。
- [ ] 页面组只复用导航，不合并业务动作。
- [ ] 每行有独立 action trace、observation 和 evidence。
- [ ] 失败最多重试一次，失败后进入阻塞/待验证。
- [ ] 每行结束立即写 journal。
- [ ] 不使用 `observe` 掩盖未识别动作。
- [ ] 不跨模块、市场或入口复用页面状态。

### 执行后

- [ ] `execution_records.json` 完整且与 manifest 数量一致。
- [ ] 已生成 `llm_review_queue.json`。
- [ ] LLM 复核绑定和证据指纹通过。
- [ ] `results.json` 通过完整性和质量门。
- [ ] 阻塞/失败/待验证已按策略复测。
- [ ] Excel 已使用 `--strict` 回填到新文件。
- [ ] 输出文件可打开，源 Excel 未被覆盖。
- [ ] `profile_feedback.json` 已审核或明确不回写。
- [ ] 画像变更已通过 `reback`、schema、`derive_docs` 和 `lint_profile`。
- [ ] metrics 已记录，异常和剩余风险已交付。

## 22. 当前源码状态的特别说明

下面几项是当前仓库真实状态，执行 Agent 必须知道：

1. 全量 pytest 当前不是完全绿色：`pytest -q` 的基线为 279 通过、2 失败；失败来自 App 初始化模板测试期待 12 个标准代码，而当前模板实际有 15 个。不要把它误解为 Runner 全链路失败，也不要忽略它。
2. `guojin` 和 `zhongyou` 的部分 Profile 已经过期，需要重新验证；过期 Profile 不应直接当作当前页面事实。
3. 多个 App 的兼容上限仍使用 `999.999.999` 占位值，需要人工收紧。
4. `requirements.txt` 当前没有声明 `xlrd`，执行 `.xls` 前必须确认环境已安装。
5. 通用 Runner 的设备 serial 传递路径需要保持警惕；在多设备环境下，所有 ADB、UI 树、截图和点击必须确认属于同一设备。
6. `tools/safety/*` 已有程序化安全函数和测试，但当前通用 Runner 尚未完全强制接线；交易动作不能仅凭普通低层点击动作就宣称受安全护栏保护。
7. `output/` 和 `runs/` 包含大量历史运行产物。交付时只引用当前 run 目录，不要误读历史截图或旧结果。

以上状态说明不会改变执行规范：规范要求的安全门、证据门和结果门仍然必须执行；如果当前实现无法满足某个门，正确处理是停止并报告，而不是绕过。
