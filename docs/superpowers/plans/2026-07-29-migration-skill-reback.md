# Hook 可行性 + 迁移 + Skill + 反哺/派生/lint 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把国金这轮沉淀从「散落 md」升级为「YAML 机器权威 + 脚本派生 md + 结构化反哺 + 护栏 hook + 可复用 skill」，落地 spec §8.4–8.6，为第二个 app 接入与越用越快的自测服务铺底座。

**Architecture:** `apps/guojin/` 成为国金的家：`app.yaml`(身份/版本/账户脱敏/引用) + `env.yaml`(**安全默认态**的环境认证，真实认证由负责人签) + `profile.yaml`/`prerequisites.yaml`(机器权威) → 脚本派生 `画像.md/前置条件.md/速览.md`。`tools/` 跨 app 共用，新增 `derive_docs.py`(派生)/`reback.py`(结构化 upsert 反哺)/`lint_profile.py`(lint)/`safety/env_sign.py`(认证签名器)。`.claude/hooks/` 落 `git add .` 拦截 + 秘密泄漏护栏；`.claude/skills/app-selftest/` 落主 skill + references 软护栏。

**Tech Stack:** Python 3.14 · PyYAML · jsonschema · pytest 9（仓根 `PYTHONUTF8=1 python -m pytest`）· 复用 Plan 1 `tools/contracts/validate.py`、`tools/safety/{env_auth,secrets}.py` · Claude Code hooks(PreToolUse, JSON on stdin/stdout)。

---

## Global Constraints

> 每个任务的要求都隐含包含本节。逐条 verbatim，实现/复审都以此为准。

- **[P0] 执行程序绝不可自签/伪造环境认证（spec §4.6/§D5）。** `env.yaml` 的真实 `operator_attested` 认证（`attested_by`/`attested_at`/`valid_until`/`basis`）**必须由项目负责人 shenjie 本人填写并用 `.secrets` 密钥签 `.sig`**。本 plan 只交付：①**安全默认态** `apps/guojin/env.yaml`（`revoked: true`，使 `verify_env` 天然回退 `confirm_only`）②`apps/guojin/env.yaml.example`（填好的样例，供人照抄，用假名/假日期）③签名工具 `tools/safety/env_sign.py` ④`.secrets/README.md`。**任何任务都不得写入一个"有效的"真实认证**（不得设 `revoked:false` + 真实 attested 值使其通过）。
- **迁移必须脱敏。** 新产物（`app.yaml`/`profile.yaml`/`prerequisites.yaml`/派生 md/example）里账户只留 **别名 `pt`/`xy` + 脱敏尾号 `***5183`/`***2927`**（spec §4.1）。完整账号（登录号 `***1395`/`***0047`、股东/资金号 `***5183`/`***2927`）**只进 `.secrets/guojin.accounts.yaml`（git-ignored，用户填）**；仓内 `.secrets/guojin.accounts.yaml.example` 用**假号**（如 `99999999`）。
- **git 历史脱敏 = 待用户决定，不在本 plan 做破坏性重写。** 旧提交里 `profiles/*.md` 等仍含真实号（Plan 1 终审已升级）；本 plan 只保证**工作区新产物脱敏**，把"历史清洗"列入计划末尾"遗留/待用户"。
- **提交格式**：subject `302968 feat <desc>` 或 `302968 test <desc>`；**测试文件与非测试文件分属不同提交**；显式 `git add <files>`（**禁 `git add .`**）；body 空行后 `Co-Authored-By: Claude Code | claude-opus-4-8 | <类型>`。**第三段 `<类型>` 合法取值为三类**：`code`（新增功能/实现的非测试提交）、`test`（单测批）、`fix`（**修 AI 自身缺陷**的非测试提交，如终审复盘修补 hook/契约/lint 等——即便在非 fix 分支、subject 前缀仍用 `feat`，第三段用 `fix` 以示与新增功能区分）。
- **YAML 机器权威、MD 脚本派生（D3）**：结构化职责（schema 校验/唯一 key/stale 查询/upsert）都在 yaml 层；`画像.md/前置条件.md/速览.md` 顶部标注「本文件脚本派生，勿手改」。
- **复用 Plan 1**：schema 放 `tools/contracts/schemas/`，用 `validate(doc, schema_name)`/`load_and_validate(path, schema_name)`；环境校验走 `tools/safety/env_auth.verify_env`、`tools/safety/secrets`。
- **`git mv` 保留历史**：迁移旧 md 用 `git mv` 不用删+建。
- **测试从仓根跑**：`PYTHONUTF8=1 python -m pytest -q`；pyproject 已配 `pythonpath=["."]`。
- **hook D2 降级原则**：若 hook 拿不到实时 token/cache（Task 1 spike 结论），护栏降级为「每批 `metrics.py` + turn/action/时长阈值提醒」，不硬造一个拿不到数据的 hook。

---

## File Structure

- `docs/superpowers/spikes/2026-07-29-hook-context-tax-feasibility.md` — Task 1：hook 取上下文税可行性结论 + D2 决策。
- `.claude/hooks/guard_git_add.py` — Task 2：`git add .`/秘密路径 拦截（PreToolUse）。
- `.claude/hooks/context_tax_reminder.py` — Task 1：`assess` 纯函数（被 `tools/metrics.py` 复用做阈值提醒）；**非 Stop hook**——Stop payload 无实时 token/cache/metrics，接了永不发声。
- `tools/metrics.py` — Task 1（D2 真实接线）：批次 metrics 算完后调用 `assess` 打印阈值提醒（D2 的真实触发点）。
- `.claude/settings.json` — Task 2：hook 接线（若不存在则建，存在则合并）。**仅 Task 2 写 settings.json；Task 1 不写 settings**（D2 落在 metrics.py，不接 Stop hook）。
- `tools/contracts/schemas/profile.schema.json` — Task 3。
- `tools/contracts/schemas/prerequisites.schema.json` — Task 3。
- `apps/guojin/app.yaml` — Task 4：身份/版本/账户脱敏/引用。
- `apps/guojin/env.yaml` — Task 4：安全默认态认证（`revoked:true`）。
- `apps/guojin/env.yaml.example` — Task 4：填好样例（假值）。
- `tools/safety/env_sign.py` — Task 4：HMAC 认证签名器。
- `.secrets/README.md`、`.secrets/guojin.accounts.yaml.example` — Task 4。
- `apps/guojin/profile.yaml` — Task 5：由画像迁入的机器权威。
- `apps/guojin/prerequisites.yaml` — Task 6：由需求清单迁入的机器权威。
- `tools/derive_docs.py` — Task 7：yaml → `apps/guojin/{画像.md,前置条件.md,速览.md}` 派生。
- `apps/guojin/{画像.md,前置条件.md,速览.md}` — Task 7：派生产物（由 `git mv profiles/*.md` 迁入后覆盖为脱敏派生版）。
- `tools/reback.py` — Task 8：结构化 upsert 反哺。
- `tools/lint_profile.py` — Task 9：重复/复制/stale/漂移 lint。
- `.claude/skills/app-selftest/SKILL.md` — Task 10：主 skill。
- `.claude/skills/app-selftest/references/{workflow.md,tiering.md,pitfalls.md}` — Task 10（`safety-policy.md` 已存在，不重写）。
- 测试：`tests/hooks/`、`tests/contracts/`、`tests/tools/` 下对应 `test_*.py`。

---

## Task 1: Hook 可行性 spike（§8.4 / D2）

**Files:**
- Create: `docs/superpowers/spikes/2026-07-29-hook-context-tax-feasibility.md`
- Create: `.claude/hooks/context_tax_reminder.py`（`assess` 纯函数；**非 Stop hook**）
- Modify: `tools/metrics.py`（**D2 真实接线**：批次 metrics 算完后调用 `assess` 打印阈值提醒）
- Test: `tests/hooks/test_context_tax_reminder.py`、`tests/tools/test_metrics_context_tax.py`

**Interfaces:**
- Produces: `context_tax_reminder.py` 的纯函数 `assess(metrics: dict, thresholds: dict) -> list[str]`（返回触发的提醒文案列表；空=不提醒）。
- **D2 真实触发点 = `tools/metrics.py` 复用 `assess`**：每批 metrics 算完后调用并打印阈值提醒，**不依赖 Claude Code Stop hook 的 `metrics` payload（该 payload 不存在）**。`context_tax_reminder.py` 里的 `_main` 仅为可选手动 CLI，不接为 hook。
- Produces: `tools/metrics.py` 的 `context_tax_metrics(tot, since=None, until=None) -> dict`（把 `sum_usage` 的 tot 折算成 `{turns,actions,minutes}`）与 `remind(tot, since=None, until=None) -> None`（调 `assess` 打印提醒；空=不发声）。

- [ ] **Step 1: 调研并写结论文档.** 调研 Claude Code hook（PreToolUse/PostToolUse/Stop）事件 payload 是否含实时 token/cache 用量。用可得手段核实（读 `.claude/settings.json` 支持的 hook 事件、`$CLAUDE_*` 环境变量、hook stdin JSON 字段；查 `tools/metrics.py` 现在怎么取 token=从会话 transcript 事后算，非实时）。写文档，含：调研方法、事实发现、**D2 决策**。默认结论（除非调研推翻）：**hook 无法拿到实时 token/cache（metrics.py 是事后按 transcript 时间窗算），故降级为「每批 metrics.py + turn/action/时长阈值提醒」**。**结论须显式写明**：若确认 Claude Code hook 拿不到实时 token/cache（预期结论），**D2 = `tools/metrics.py` 集成的阈值提醒（metrics.py 算完一批指标后调用 `assess`），不依赖 Stop hook 的 `metrics` payload（该 payload 不存在，接 Stop hook 会永不发声）**。文档给出降级方案的具体阈值建议（如 单批 turn>40 / action>60 / 时长>30min → 提醒"考虑新开精简会话/固化 Maestro"，依据 `runs/metrics.md` 的上下文税洞察）。

- [ ] **Step 2: 写降级提醒纯函数的失败测试.**

```python
# tests/hooks/test_context_tax_reminder.py
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "context_tax_reminder",
    pathlib.Path(__file__).resolve().parents[2] / ".claude/hooks/context_tax_reminder.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
assess = mod.assess

TH = {"turns": 40, "actions": 60, "minutes": 30}

def test_no_reminder_below_thresholds():
    assert assess({"turns": 10, "actions": 20, "minutes": 5}, TH) == []

def test_reminder_on_turn_overflow():
    out = assess({"turns": 55, "actions": 20, "minutes": 5}, TH)
    assert any("turn" in m or "新开" in m for m in out)

def test_reminder_lists_all_breached():
    out = assess({"turns": 55, "actions": 80, "minutes": 45}, TH)
    assert len(out) == 3
```

- [ ] **Step 3: 运行测试确认失败.** `PYTHONUTF8=1 python -m pytest tests/hooks/test_context_tax_reminder.py -v` → FAIL（模块/函数不存在）。

- [ ] **Step 4: 实现 `assess` + hook 外壳.**

```python
# .claude/hooks/context_tax_reminder.py
"""上下文税提醒（D2 降级态）：hook 拿不到实时 token，故按 turn/action/时长阈值提醒。
D2 真实触发点是 tools/metrics.py（每批 metrics 算完后调用 assess）；本文件只出 assess 纯函数。
_main 仅为可选手动 CLI（stdin 传 {"metrics": {...}} JSON），**不接为 Claude Code Stop hook**——
Stop payload 无实时 token/cache/metrics，接了永不发声。见 docs/superpowers/spikes/2026-07-29-hook-context-tax-feasibility.md"""
import json, sys

DEFAULT_THRESHOLDS = {"turns": 40, "actions": 60, "minutes": 30}

def assess(metrics, thresholds=None):
    th = thresholds or DEFAULT_THRESHOLDS
    msgs = []
    if metrics.get("turns", 0) > th["turns"]:
        msgs.append(f"本会话 turn={metrics['turns']}>{th['turns']}：上下文税(cache_read)累积，考虑新开精简会话或固化 Maestro。")
    if metrics.get("actions", 0) > th["actions"]:
        msgs.append(f"动作数={metrics['actions']}>{th['actions']}：批次偏大，按屏合并取证以省 token。")
    if metrics.get("minutes", 0) > th["minutes"]:
        msgs.append(f"批次时长={metrics['minutes']}min>{th['minutes']}：长会话含税成本虚高，考虑分批。")
    return msgs

def _main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    for m in assess(payload.get("metrics", {})):
        print(m, file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 5: 运行测试确认通过.** `PYTHONUTF8=1 python -m pytest tests/hooks/test_context_tax_reminder.py -v` → PASS。

- [ ] **Step 6: 写 metrics.py D2 接线的失败测试（让护栏真正发声，非永不触发）.**

```python
# tests/tools/test_metrics_context_tax.py
import tools.metrics as M

def test_context_tax_metrics_maps_window_to_minutes():
    m = M.context_tax_metrics({"turns": 55, "actions": 80},
                              "2026-07-29T10:00:00Z", "2026-07-29T10:45:00Z")
    assert m["turns"] == 55 and m["minutes"] == 45

def test_remind_fires_over_threshold(capsys):
    M.remind({"turns": 55, "actions": 80},
             "2026-07-29T10:00:00Z", "2026-07-29T10:45:00Z")
    assert "上下文税" in capsys.readouterr().out

def test_remind_silent_under_threshold(capsys):
    M.remind({"turns": 5, "actions": 5})
    assert capsys.readouterr().out == ""
```

- [ ] **Step 7: 接线 `tools/metrics.py`（D2 真实触发点，复用 `assess` 单一真源，不复制阈值逻辑）.** ①在 `sum_usage` 的 `tot` 增加 `actions`（统计 assistant `message.content` 里 `type=="tool_use"` 的块数，与 `turns` 同循环累加）；②追加下列函数；③在 `main()` 的 `session` 与 `tokens` 分支 `show(t, f)` 之后调用 `remind(t, since, until)`（`session` 分支 `since=until=None`），使每次跑 metrics 超阈值即发声。

```python
# tools/metrics.py —— 追加（复用 .claude/hooks/context_tax_reminder.py 的 assess）
import importlib.util, pathlib

def _load_assess():
    p = pathlib.Path(__file__).resolve().parent.parent / ".claude/hooks/context_tax_reminder.py"
    spec = importlib.util.spec_from_file_location("context_tax_reminder", p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.assess

def context_tax_metrics(tot, since=None, until=None):
    """把 sum_usage 的 tot 折算成 assess 需要的 {turns,actions,minutes}。"""
    m = {"turns": tot.get("turns", 0), "actions": tot.get("actions", tot.get("turns", 0))}
    if since and until:
        m["minutes"] = int((parse_ts(until) - parse_ts(since)).total_seconds() // 60)
    return m

def remind(tot, since=None, until=None):
    """D2 真实触发点：批次 metrics 算完后打印阈值提醒（空=不发声）。"""
    for msg in _load_assess()(context_tax_metrics(tot, since, until)):
        print("⚠ 上下文税提醒:", msg)
```

- [ ] **Step 8: 运行确认通过.** `PYTHONUTF8=1 python -m pytest tests/hooks/test_context_tax_reminder.py tests/tools/test_metrics_context_tax.py -v` → PASS。

- [ ] **Step 9: 提交（实现+文档+接线 与 测试 分两次）.**

```bash
git add docs/superpowers/spikes/2026-07-29-hook-context-tax-feasibility.md .claude/hooks/context_tax_reminder.py tools/metrics.py
git commit -m "302968 feat hook可行性spike+上下文税提醒(D2降级,接入metrics.py)" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | code'
git add tests/hooks/test_context_tax_reminder.py tests/tools/test_metrics_context_tax.py
git commit -m "302968 test 上下文税提醒阈值+metrics接线单测" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | test'
```

---

## Task 2: `git add .` 拦截 + 秘密泄漏护栏 hook（§L3 硬护栏）

**Files:**
- Create: `.claude/hooks/guard_git_add.py`
- Create/Modify: `.claude/settings.json`（PreToolUse 接线 Bash）
- Test: `tests/hooks/test_guard_git_add.py`

**Interfaces:**
- Produces: `decide(command: str) -> tuple[bool, str]` — 返回 `(allow, reason)`。`allow=False` 表示应阻断该 Bash 命令。hook 外壳读 PreToolUse stdin JSON（`tool_input.command`），`allow=False` 时输出阻断决定（退出码 2 + stderr 原因，符合 Claude Code PreToolUse 阻断约定）。

- [ ] **Step 1: 写失败测试.**

```python
# tests/hooks/test_guard_git_add.py
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "guard_git_add",
    pathlib.Path(__file__).resolve().parents[2] / ".claude/hooks/guard_git_add.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
decide = mod.decide

def test_blocks_git_add_dot():
    allow, reason = decide("git add .")
    assert allow is False and "git add" in reason

def test_blocks_git_add_all_flags():
    for cmd in ("git add -A", "git add --all", "git add -A .", "git add   ."):
        assert decide(cmd)[0] is False

def test_blocks_secrets_path():
    assert decide("git add .secrets/guojin.accounts.yaml")[0] is False
    assert decide("git add .secrets/hmac.key")[0] is False
    assert decide("git add runs/x/snapshots/a.png")[0] is False
    assert decide("git add foo.private.png")[0] is False

def test_allows_secrets_whitelist():
    # 镜像 .gitignore 的 !.secrets/README.md / !.secrets/*.example 白名单例外（Task 4 Step 7）
    assert decide("git add .secrets/README.md")[0] is True
    assert decide("git add .secrets/guojin.accounts.yaml.example")[0] is True

def test_allows_explicit_safe_add():
    assert decide("git add tools/derive_docs.py")[0] is True
    assert decide("git add apps/guojin/profile.yaml tests/tools/test_x.py")[0] is True

def test_ignores_non_git_add():
    assert decide("git commit -m x")[0] is True
    assert decide("ls .secrets/")[0] is True
```

- [ ] **Step 2: 运行确认失败.** `PYTHONUTF8=1 python -m pytest tests/hooks/test_guard_git_add.py -v` → FAIL。

- [ ] **Step 3: 实现 `decide` + hook 外壳.**

```python
# .claude/hooks/guard_git_add.py
"""PreToolUse(Bash) 护栏：拦 `git add .`/`-A`/`--all` 与触碰 .secrets/snapshots/*.private.png 的 add。
放行显式安全 add 与 .secrets/ 白名单例外(README.md / *.example)。见 git-commit skill「禁 git add .」与 spec §L3。"""
import json, re, shlex, sys

_SECRET_PAT = re.compile(r"(^|/)\.secrets/|(^|/)snapshots/|\.private\.png$")
# .secrets/ 白名单例外：镜像 .gitignore 的 !.secrets/README.md / !.secrets/*.example（Task 4 Step 7）——两处必须一致。
_SECRET_ALLOW_PAT = re.compile(r"(^|/)\.secrets/(README\.md|[^/]*\.example)$")

def decide(command):
    cmd = command.strip()
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    # 仅关心 git add
    if "git" not in toks:
        return (True, "")
    gi = toks.index("git")
    if gi + 1 >= len(toks) or toks[gi + 1] != "add":
        return (True, "")
    args = toks[gi + 2:]
    for a in args:
        if a in (".", "-A", "--all", "-Av", "-u", ":/"):
            return (False, "禁用批量 `git add .`/-A/--all：请显式 `git add <files>`（git-commit skill 硬规则）。")
        if _SECRET_ALLOW_PAT.search(a):
            continue  # 白名单例外：.secrets/README.md 与 .secrets/*.example 允许入仓（镜像 .gitignore 的 ! 例外）
        if _SECRET_PAT.search(a):
            return (False, f"拒绝把秘密/私密路径加入版本控制：{a}（.secrets/、snapshots/、*.private.png 应 gitignore；仅 .secrets/README.md 与 *.example 例外）。")
    return (True, "")

def _main():
    try:
        payload = json.load(sys.stdin)
        command = payload.get("tool_input", {}).get("command", "")
    except Exception:
        return 0
    allow, reason = decide(command)
    if not allow:
        print(reason, file=sys.stderr)
        return 2  # PreToolUse: 非零阻断
    return 0

if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 4: 运行确认通过.** `PYTHONUTF8=1 python -m pytest tests/hooks/test_guard_git_add.py -v` → PASS。

- [ ] **Step 5: 接线 `.claude/settings.json`.** 若不存在则建；存在则合并（不覆盖既有键）。加入 PreToolUse→Bash→`guard_git_add.py`：

```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash",
       "hooks": [{"type": "command", "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard_git_add.py\""}]}
    ]
  }
}
```

- [ ] **Step 6: 提交（实现+接线 与 测试 分两次）.**

```bash
git add .claude/hooks/guard_git_add.py .claude/settings.json
git commit -m "302968 feat git-add./秘密路径拦截hook(PreToolUse)" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | code'
git add tests/hooks/test_guard_git_add.py
git commit -m "302968 test git-add拦截hook单测" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | test'
```

---

## Task 3: `profile` / `prerequisites` 契约 schema（补 spec §8.1 未建项）

**Files:**
- Create: `tools/contracts/schemas/profile.schema.json`
- Create: `tools/contracts/schemas/prerequisites.schema.json`
- Test: `tests/contracts/test_profile_prereq_schemas.py`
- Fixtures: `tests/fixtures/profile_valid.yaml`、`profile_invalid.yaml`、`prerequisites_valid.yaml`、`prerequisites_invalid.yaml`

**Interfaces:**
- Consumes: Plan 1 `tools/contracts/validate.py` 的 `load_and_validate(path, schema_name)`（`schema_name` 取文件名去 `.schema.json`，即 `"profile"`/`"prerequisites"`）。
- Produces: 两个 schema，供 Task 4/5/6/8/9 校验。

**数据模型（schema 需强制）：**
- `profile`: **顶层 `required`（5 项全必填）= `slug`(str) · `app_version`(str) · `entries`(array) · `capabilities`(array) · `verified_chains`(array)**。每个 `entries[]`：`key`(唯一,str)、`path`(str，导航路径/resource-id 串)、`last_verified`(date str)、`app_version`(str)、`evidence_run`(str)、`status`(enum: verified|stale|unverified)。`capabilities[]`：`key`、`supported`(bool)、`note`、`last_verified`、`status`。`verified_chains[]`：`key`、`steps`(array[str])、`last_verified`、`evidence_run`、`status`。所有 `key`（及各 item 上列字段）`required`，`additionalProperties:false`。
- `prerequisites`: **顶层 `required`（4 项全必填）= `slug` · `account_capabilities`(array) · `instrument_properties`(array) · `known_codes`(array)**。`account_capabilities[]`：`alias`(pt|xy…)、`type`(普通|信用)、`capabilities`(array[str])、`mask`(如 `***5183`)。`instrument_properties[]`：`code`、`name`、`props`(array[str])。`known_codes[]`：`code`、`name`、`market`、`attributes`(object)。**schema 禁止出现完整账号**：给 `mask` 加 `pattern: "^\\*{2,}[0-9]{2,4}$"`，`account_capabilities` 无 `account_no`/`full` 字段（`additionalProperties:false` 兜底）。

- [ ] **Step 1: 写 fixtures + 失败测试.** 四个 fixture 用**字面完整内容**（valid 顶层字段齐全、能过校验；invalid 触发拒绝）：

```yaml
# tests/fixtures/profile_valid.yaml —— 顶层 5 项齐全
slug: guojin
app_version: "8.05.001"
entries:
  - {key: trade.putong.buy, path: "交易→买入", last_verified: "2026-07-29", app_version: "8.05.001", evidence_run: "2026-07-29-guojin-r1", status: verified}
capabilities:
  - {key: cap.putong.limit_buy, supported: true, note: "限价买入", last_verified: "2026-07-29", status: verified}
verified_chains:
  - {key: chain.putong.buy_cancel, steps: ["买入", "撤单"], last_verified: "2026-07-29", evidence_run: "2026-07-29-guojin-r1", status: verified}
```

```yaml
# tests/fixtures/profile_invalid.yaml —— entries[0] 缺 required 字段 key（触发拒绝）
slug: guojin
app_version: "8.05.001"
entries:
  - {path: "交易→买入", last_verified: "2026-07-29", app_version: "8.05.001", evidence_run: "r1", status: verified}   # ★缺 required: key
capabilities: []
verified_chains: []
```

```yaml
# tests/fixtures/prerequisites_valid.yaml —— 顶层 4 项齐全、账户仅别名+脱敏尾号
slug: guojin
account_capabilities:
  - {alias: pt, type: 普通, capabilities: ["北交所交易", "全权限"], mask: "***5183"}
  - {alias: xy, type: 信用, capabilities: ["两融", "担保品持仓"], mask: "***2927"}
instrument_properties:
  - {code: "950025", name: "北证50ETF测试39", props: ["t0", "collateral"]}
known_codes:
  - {code: "950025", name: "北证50ETF测试39", market: 北交所, attributes: {t0: true, has_nav: false, collateral: true, financing_eligible: false}}
```

```yaml
# tests/fixtures/prerequisites_invalid.yaml —— P0 全号拒绝 fixture：裸 account_no（additionalProperties:false 拒绝）
# ★此 fixture 刻意保留"完整账号入受控文件即被拒"以持续守护 P0 脱敏（不得削弱）；profile_invalid 已覆盖"缺 required 字段"路径。
slug: guojin
account_capabilities:
  - {alias: pt, type: 普通, capabilities: ["北交所交易"], mask: "***5183", account_no: "***5183"}   # ★被禁字段：完整账号 → additionalProperties:false 拒绝
instrument_properties:
  - {code: "950025", name: "北证50ETF测试39", props: ["t0"]}
known_codes:
  - {code: "950025", name: "北证50ETF测试39", market: 北交所, attributes: {t0: true}}
```


```python
# tests/contracts/test_profile_prereq_schemas.py
from tools.contracts.validate import load_and_validate
import pathlib
FX = pathlib.Path(__file__).resolve().parents[1] / "fixtures"

def test_profile_valid():
    doc, errs = load_and_validate(FX / "profile_valid.yaml", "profile")
    assert errs == []

def test_profile_invalid():
    _, errs = load_and_validate(FX / "profile_invalid.yaml", "profile")
    assert errs

def test_prerequisites_valid():
    _, errs = load_and_validate(FX / "prerequisites_valid.yaml", "prerequisites")
    assert errs == []

def test_prerequisites_rejects_full_account():
    _, errs = load_and_validate(FX / "prerequisites_invalid.yaml", "prerequisites")
    assert errs  # 裸 account_no / 非脱敏 mask 被拒
```

- [ ] **Step 2: 运行确认失败**（schema 文件不存在）。
- [ ] **Step 3: 写两个 schema JSON.**（按上面数据模型；`additionalProperties:false`；`status` enum；`mask` pattern。**顶层 `required` 显式列全**：`profile`=`[slug, app_version, entries, capabilities, verified_chains]`；`prerequisites`=`[slug, account_capabilities, instrument_properties, known_codes]`。各 item 的 `required` 亦列全，使 `profile_invalid`(缺 entry.key) 与 `prerequisites_invalid`(裸 account_no) 均被拒。）
- [ ] **Step 4: 运行确认通过.**
- [ ] **Step 5: 提交（schema 与 测试/fixtures 分两次；fixtures 归 feat 批同 Plan1 惯例）.**

```bash
git add tools/contracts/schemas/profile.schema.json tools/contracts/schemas/prerequisites.schema.json tests/fixtures/profile_valid.yaml tests/fixtures/profile_invalid.yaml tests/fixtures/prerequisites_valid.yaml tests/fixtures/prerequisites_invalid.yaml
git commit -m "302968 feat profile/prerequisites 契约schema(禁完整账号)" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | code'
git add tests/contracts/test_profile_prereq_schemas.py
git commit -m "302968 test profile/prerequisites schema单测" -m $'\nCo-Authored-By: Claude Code | claude-opus-4-8 | test'
```

---

## Task 4: `app.yaml` + `env.yaml` 安全模板 + 认证签名器（§4.1 / §4.6）

**Files:**
- Create: `apps/guojin/app.yaml`、`apps/guojin/env.yaml`、`apps/guojin/env.yaml.example`
- Create: `tools/safety/env_sign.py`
- Create: `.secrets/README.md`、`.secrets/guojin.accounts.yaml.example`
- Test: `tests/safety/test_env_sign_and_default.py`

**Interfaces:**
- Consumes: `tools/safety/env_auth.verify_env(env, device_pkg, device_version, now_iso, integrity_ok)`、`tools/safety/secrets.account_hmac`。
- Produces: `env_sign.sign(env_yaml_path, key) -> bytes`（返回 HMAC-SHA256 hex 并可写 `<path>.sig`）；`env_sign.load_key(path) -> bytes`。

**★ 安全默认态设计（照 Global Constraints P0）：** `apps/guojin/env.yaml` 交付时 `revoked: true` 且 `assurance_level: operator_attested`、`attested_by: PENDING_OPERATOR_ATTESTATION`。这样任何 `verify_env` 调用都因 `revoked` 回退 `confirm_only`——系统处于最安全默认。用户次日的动作：填真实认证 → `revoked:false` → 用 `.secrets` 密钥 `env_sign` 生成 `env.yaml.sig`。

- [ ] **Step 1: 写失败测试.**

```python
# tests/safety/test_env_sign_and_default.py
from tools.contracts.validate import load_and_validate
from tools.safety.env_auth import verify_env
from tools.safety import env_sign
import pathlib, yaml
ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = "com.hexin.plat.android.GuoJinZXGSecurity"

def _load(p): return yaml.safe_load((ROOT / p).read_text(encoding="utf-8"))

def test_shipped_env_is_safe_default():
    env = _load("apps/guojin/env.yaml")
    # 交付态 revoked=true → 无论 integrity 如何，回退 confirm_only
    mode, reasons = verify_env(env, PKG, "8.05.001", "2026-07-29", True)
    assert mode == "confirm_only" and "revoked" in reasons

def test_example_is_within_version_and_attested_shape():
    env = _load("apps/guojin/env.yaml.example")
    # 样例填好且未撤销、日期未过期、版本命中 → integrity_ok=True 时可达 simulated_submit
    mode, reasons = verify_env(env, PKG, "8.05.001", "2026-08-01", True)
    assert mode == "simulated_submit", reasons

def test_env_sign_roundtrip():
    key = b"test-key-32bytes-xxxxxxxxxxxxxxxx"
    sig = env_sign.sign(ROOT / "apps/guojin/env.yaml.example", key)
    assert isinstance(sig, str) and len(sig) == 64

def test_app_yaml_passes_schema_and_is_masked():
    doc, errs = load_and_validate(ROOT / "apps/guojin/app.yaml", "app")
    assert errs == []
    txt = (ROOT / "apps/guojin/app.yaml").read_text(encoding="utf-8")
    for full in ("***5183", "***2927", "***1395", "***0047"):
        assert full not in txt  # 仓内不得出现完整账号
```

- [ ] **Step 2: 运行确认失败.**

- [ ] **Step 3: 写 `apps/guojin/app.yaml`（脱敏）.**

```yaml
slug: guojin
aliases: [国金, 国金证券]
packages: [com.hexin.plat.android.GuoJinZXGSecurity]
verified_versions:
  - {version: "8.05.001", verified_at: "2026-07-29"}
compatibility: {min: "8.05.001", max_exclusive: "8.06.000"}
test_accounts:
  - {alias: pt, type: 普通, mask: "***5183"}
  - {alias: xy, type: 信用, mask: "***2927"}
environment: env.yaml
profile: profile.yaml
prerequisites: prerequisites.yaml
```

- [ ] **Step 4: 写 `apps/guojin/env.yaml`（安全默认态 revoked）+ `env.yaml.example`（填好样例）.**

```yaml
# apps/guojin/env.yaml  —— 安全默认态：revoked=true，verify_env 天然回退 confirm_only。
# 真实认证须由负责人 shenjie 填写并用 .secrets 密钥 env_sign 生成 env.yaml.sig（见 .secrets/README.md）。
type: simulation
assurance_level: operator_attested
evidence:
  attested_by: PENDING_OPERATOR_ATTESTATION
  attested_at: "2026-07-29"
  valid_until: "2026-07-29"
  basis: ["占位——待负责人对国金模拟盘做一次性认证"]
  package: com.hexin.plat.android.GuoJinZXGSecurity
  version_range: {min: "8.05.001", max_exclusive: "8.06.000"}
  account_aliases: [pt, xy]
revoked: true
```

```yaml
# apps/guojin/env.yaml.example  —— 供负责人照抄的"填好"样例（此文件仅示例，不被执行流读取）。
# ★用假名/假日期，绝不冒用负责人真实身份签认证（spec §4.6/§D5 执行程序不得自签/伪造）。
# 负责人照此结构填写 apps/guojin/env.yaml 的真实值，再用 .secrets 密钥 env_sign 生成 .sig。
type: simulation
assurance_level: operator_attested
evidence:
  attested_by: EXAMPLE-OPERATOR        # 样例占位假名——真实认证请填到 env.yaml 且用真名
  attested_at: "2026-01-01"            # 样例假日期
  valid_until: "2099-12-31"            # 样例假日期(仅为让样例通过 verify_env 形状校验)
  basis: ["【样例】项目运营约定仅使用国金模拟盘", "【样例】账户 pt/xy 为指定测试账户"]
  package: com.hexin.plat.android.GuoJinZXGSecurity
  version_range: {min: "8.05.001", max_exclusive: "8.06.000"}
  account_aliases: [pt, xy]
revoked: false
```

- [ ] **Step 5: 写 `tools/safety/env_sign.py`.**

```python
# tools/safety/env_sign.py
"""环境认证签名器：用 .secrets 本地密钥对 env.yaml 正文算 HMAC-SHA256，写 <path>.sig。
执行程序据此校验认证未被篡改（配合 secrets.env_integrity_ok）。密钥不入仓。"""
import hashlib, hmac, pathlib

def load_key(path):
    return pathlib.Path(path).read_bytes()

def sign(env_yaml_path, key, write_sig=False):
    data = pathlib.Path(env_yaml_path).read_bytes()
    digest = hmac.new(key, data, hashlib.sha256).hexdigest()
    if write_sig:
        pathlib.Path(str(env_yaml_path) + ".sig").write_text(digest, encoding="utf-8")
    return digest
```

- [ ] **Step 6: 写 `.secrets/README.md` + `.secrets/guojin.accounts.yaml.example`（假号）.** README 说明：①放 `hmac.key`（32B 随机，`python -c "import os,pathlib;pathlib.Path('.secrets/hmac.key').write_bytes(os.urandom(32))"`）②放 `guojin.accounts.yaml`（完整账号↔alias 映射，供 `account_hmac` 比对）③负责人认证流程：把**真实**认证值填进 `apps/guojin/env.yaml`（真名 `attested_by`、真实 `attested_at`/`valid_until`、`basis`）→设 `revoked:false`→`python -c "from tools.safety import env_sign; env_sign.sign('apps/guojin/env.yaml', env_sign.load_key('.secrets/hmac.key'), write_sig=True)"`。④**禁止直接把 `apps/guojin/env.yaml.example` 改名为 `env.yaml`**——`env.yaml.example` 是**假认证样例**（假名/假日期、`revoked:false` 仅为演示形状），改名会得到**假认证却能通过 `verify_env`**（因其形状完整、未撤销、未过期）；真实认证必须由负责人本人填写并签名，example 只作照抄参考。example 用假号：

```yaml
# .secrets/guojin.accounts.yaml.example  —— 复制为 guojin.accounts.yaml 填真实号（该文件 gitignored）
accounts:
  - {alias: pt, type: 普通, login: "99999999", fund: "99999999", holder: "99999999"}
  - {alias: xy, type: 信用, login: "99999999", holder: "99999999"}
```

- [ ] **Step 7: 修 `.gitignore` 让 `.secrets/README.md` 与 `*.example` 可入仓.** 现状 `.gitignore` 第 1 行是 `.secrets/`（忽略整目录）——**git 无法用 `!` 重新包含被忽略目录下的文件**。必须把该行改为「忽略目录内容但保留说明/样例」：

```gitignore
# 将 .gitignore 的 `.secrets/` 一行替换为：
.secrets/*
!.secrets/README.md
!.secrets/*.example
```

  这样 `.secrets/` 目录本身不被忽略、其内容默认忽略，仅 `README.md` 与 `*.example` 例外可入仓；真实 `hmac.key`/`guojin.accounts.yaml` 仍被忽略。改后 `git status` 应能看到 `.secrets/README.md` 与 `.secrets/guojin.accounts.yaml.example` 为待添加，且 `git check-ignore .secrets/guojin.accounts.yaml` 仍命中（被忽略）。
  **★两处一致约束**：此白名单例外（`!.secrets/README.md` / `!.secrets/*.example`）**必须与 Task 2 `guard_git_add.py` 的 `_SECRET_ALLOW_PAT`（`(^|/)\.secrets/(README\.md|[^/]*\.example)$`）镜像一致**——否则 hook 会拦掉本 Task 必须提交的 `.secrets/README.md` 与 `.secrets/guojin.accounts.yaml.example`，而真实 `.secrets/guojin.accounts.yaml` / `hmac.key` 在两处都被拦/忽略。
- [ ] **Step 8: 运行确认通过.**
- [ ] **Step 9: 提交（实现+数据 与 测试 分两次）.** `git add apps/guojin/app.yaml apps/guojin/env.yaml apps/guojin/env.yaml.example tools/safety/env_sign.py .secrets/README.md .secrets/guojin.accounts.yaml.example .gitignore` → feat；`tests/safety/test_env_sign_and_default.py` → test。

---

## Task 5: `profile.yaml` 迁移（画像 → 机器权威）

**Files:**
- Create: `apps/guojin/profile.yaml`
- Test: `tests/apps/test_guojin_profile.py`

**Interfaces:**
- Consumes: Task 3 `profile.schema.json`（`load_and_validate(..., "profile")`）。
- Produces: `apps/guojin/profile.yaml` 机器权威，供 Task 7 派生、Task 8 反哺、Task 9 lint。

- [ ] **Step 1: 写失败测试.**

```python
# tests/apps/test_guojin_profile.py
from tools.contracts.validate import load_and_validate
import pathlib
P = pathlib.Path(__file__).resolve().parents[2] / "apps/guojin/profile.yaml"

def test_profile_schema_valid():
    doc, errs = load_and_validate(P, "profile")
    assert errs == []

def test_profile_has_core_entries():
    doc, _ = load_and_validate(P, "profile")
    keys = {e["key"] for e in doc["entries"]}
    assert {"trade.putong.buy", "trade.rzrq", "quote.detail"} <= keys

def test_profile_keys_unique():
    doc, _ = load_and_validate(P, "profile")
    for section in ("entries", "capabilities", "verified_chains"):
        ks = [x["key"] for x in doc[section]]
        assert len(ks) == len(set(ks)), section

def test_profile_masked():
    txt = P.read_text(encoding="utf-8")
    # 忠实转写但主动脱敏：股东/资金全号、登录号一律不得出现完整值
    for full in ("***5183", "***2927", "***1395", "***0047"):
        assert full not in txt
```

- [ ] **Step 2: 运行确认失败.**
- [ ] **Step 3: 写 `apps/guojin/profile.yaml`.** 数据取自 `profiles/国金证券-券商画像.md`：`entries[]`（`trade.putong.buy` 入口+resource-id 串、`trade.rzrq`、`trade.xsb`、`trade.dazong`、`quote.detail`、`quote.etf_list`、`quote.brief` 等，每条带 `last_verified:"2026-07-29"`/`app_version:"8.05.001"`/`evidence_run`/`status:verified`）；`capabilities[]`（普通限价买卖✓、市价✓、闪电两融✗、闪电市价✗、新三板大宗✗、两融担保品链✓、融资买入 unverified-无标的、仓位键-国金老版本无 等）；`verified_chains[]`（普通限价买入→撤单、闪电买卖、新三板买卖、两融担保品买/卖/卖券还款——带合同号 evidence）。**忠实转写 ground-truth + 主动脱敏**：`画像.md` 中多处出现的股东全号 `***2927` 必须写成 `***2927`、资金/股东号 `***5183` 写成 `***5183`；登录号 `***1395`/`***0047`、密码一律**不写入** `profile.yaml`（确认框 evidence 里的股东号一律写脱敏尾号，不写全号）。

- [ ] **Step 4: 运行确认通过.**
- [ ] **Step 5: 提交（数据 与 测试 分两次）.** `apps/guojin/profile.yaml`→feat；`tests/apps/test_guojin_profile.py`→test。

---

## Task 6: `prerequisites.yaml` 迁移（需求清单 → 机器权威）

**Files:**
- Create: `apps/guojin/prerequisites.yaml`
- Test: `tests/apps/test_guojin_prerequisites.py`

**Interfaces:**
- Consumes: Task 3 `prerequisites.schema.json`。
- Produces: `apps/guojin/prerequisites.yaml`，供 Plan 2 `prereq_extract` 的 needed_codes 解析、Task 7 派生、Task 8 反哺。

- [ ] **Step 1: 写失败测试.**

```python
# tests/apps/test_guojin_prerequisites.py
from tools.contracts.validate import load_and_validate
import pathlib
P = pathlib.Path(__file__).resolve().parents[2] / "apps/guojin/prerequisites.yaml"

def test_schema_valid():
    _, errs = load_and_validate(P, "prerequisites"); assert errs == []

def test_known_codes_present():
    doc, _ = load_and_validate(P, "prerequisites")
    codes = {c["code"] for c in doc["known_codes"]}
    assert {"950025", "950001", "950015"} <= codes

def test_accounts_masked_only():
    txt = P.read_text(encoding="utf-8")
    for full in ("***5183", "***2927", "***1395", "***0047"):
        assert full not in txt
    doc, _ = load_and_validate(P, "prerequisites")
    assert {a["alias"] for a in doc["account_capabilities"]} == {"pt", "xy"}
```

- [ ] **Step 2: 运行确认失败.**
- [ ] **Step 3: 写 `apps/guojin/prerequisites.yaml`.** 取自 `profiles/测试数据代码需求清单.md`：`account_capabilities`（pt 普通/mask ***5183/capabilities[北交所交易,全权限]；xy 信用/mask ***2927/capabilities[两融,担保品持仓,可用保证金]）；`instrument_properties` + `known_codes`（950025 北证50ETF T+0 无净值 担保品可买卖 非融资标的；950001 有净值 IOPV；950015 有持仓1300 可卖；950022/950027 担保品持仓；600008 A股对照；510300/510050 沪深ETF对照；每条 `attributes` 标 `t0`/`has_nav`/`holding`/`financing_eligible`/`collateral` 等布尔）。**忠实转写 ground-truth + 主动脱敏**：清单中出现的股东全号 `***2927`→`***2927`、资金/股东号 `***5183`→`***5183`；登录号 `***1395`/`***0047`、密码一律**不写入**（账户只留 `alias`+`mask`；`test_accounts_masked_only` 断言四个完整号均不出现）。

- [ ] **Step 4: 运行确认通过.**
- [ ] **Step 5: 提交（数据 与 测试 分两次）.**

---

## Task 7: `derive_docs.py` — yaml → md/速览 派生（§8.6 / D3）

**Files:**
- Create: `tools/derive_docs.py`
- Move+Overwrite: `git mv profiles/国金证券-券商画像.md apps/guojin/画像.md`；`git mv profiles/测试数据代码需求清单.md apps/guojin/前置条件.md`；派生覆盖为脱敏版；`apps/guojin/速览.md` 新派生。
- Test: `tests/tools/test_derive_docs.py`

**Interfaces:**
- Consumes: `apps/guojin/profile.yaml`、`apps/guojin/prerequisites.yaml`。
- Produces: `derive(app_dir) -> dict[str, str]`（文件名→内容）；`main(app_dir)` 写盘。每个派生文件首行 `<!-- 本文件由 tools/derive_docs.py 从 *.yaml 派生，勿手改；改 yaml 再生成 -->`。

- [ ] **Step 1: 写失败测试.**

```python
# tests/tools/test_derive_docs.py
from tools.derive_docs import derive
import pathlib
APP = pathlib.Path(__file__).resolve().parents[2] / "apps/guojin"

def test_derive_produces_three_docs():
    out = derive(APP)
    assert set(out) == {"画像.md", "前置条件.md", "速览.md"}

def test_derived_has_autogen_banner_and_sections():
    out = derive(APP)
    for name, content in out.items():
        assert "勿手改" in content.splitlines()[0]
    assert "功能支持" in out["画像.md"] or "能力" in out["画像.md"]
    assert "950025" in out["前置条件.md"]

def test_derive_idempotent():
    assert derive(APP) == derive(APP)

def test_derived_masked():
    out = derive(APP)
    for content in out.values():
        for full in ("***5183", "***2927"):
            assert full not in content
```

- [ ] **Step 2: 运行确认失败.**
- [ ] **Step 3: 实现 `tools/derive_docs.py`.**（读两 yaml，渲染：画像.md=功能支持矩阵+入口地图+已验证链（从 profile）；前置条件.md=known_codes 表+账户能力（从 prerequisites）；速览.md=一页速查（核心入口+核心码+已验证链摘要）。纯字符串渲染、确定性、幂等；账户只渲染 mask。）
- [ ] **Step 4: `git mv` 旧 md 入 apps/guojin，再运行派生覆盖为脱敏版.**

```bash
git mv profiles/国金证券-券商画像.md apps/guojin/画像.md
git mv profiles/测试数据代码需求清单.md apps/guojin/前置条件.md
python -c "from tools.derive_docs import main; main('apps/guojin')"
rmdir profiles 2>/dev/null || true
```

- [ ] **Step 5: 运行确认通过.**
- [ ] **Step 6: 提交（脚本+派生产物+git mv 与 测试 分两次）.**

---

## Task 8: `reback.py` — 结构化 upsert 反哺（§8.6）

**Files:**
- Create: `tools/reback.py`
- Test: `tests/tools/test_reback.py`

**Interfaces:**
- Produces: `upsert(doc: dict, section: str, entry: dict) -> dict`（按 `entry["key"]`/`entry["code"]` 在 `doc[section]` 内 upsert：存在则更新 `last_verified`+`evidence_run`+合并字段、不新增重复；不存在则追加）；`reback_run(profile_path, prereq_path, results: dict) -> None`（把一轮结果写回两 yaml，写盘前 `load_and_validate` 校验）。

- [ ] **Step 1: 写失败测试.**

```python
# tests/tools/test_reback.py
from tools.reback import upsert

def test_upsert_appends_new_key():
    doc = {"entries": [{"key": "a", "last_verified": "2026-07-01"}]}
    out = upsert(doc, "entries", {"key": "b", "last_verified": "2026-07-29"})
    assert len(out["entries"]) == 2

def test_upsert_updates_existing_no_dup():
    doc = {"entries": [{"key": "a", "last_verified": "2026-07-01", "status": "stale"}]}
    out = upsert(doc, "entries", {"key": "a", "last_verified": "2026-07-29", "status": "verified"})
    assert len(out["entries"]) == 1
    assert out["entries"][0]["last_verified"] == "2026-07-29"
    assert out["entries"][0]["status"] == "verified"

def test_upsert_by_code_key():
    doc = {"known_codes": [{"code": "950025", "name": "x"}]}
    out = upsert(doc, "known_codes", {"code": "950025", "attributes": {"t0": True}})
    assert len(out["known_codes"]) == 1 and out["known_codes"][0]["attributes"]["t0"]
```

- [ ] **Step 2: 运行确认失败.**
- [ ] **Step 3: 实现 `tools/reback.py`.**（`upsert` 用 `key` 或 `code` 作唯一键；`reback_run` 写盘前校验 schema，校验失败抛错不写。）
- [ ] **Step 4: 运行确认通过.**
- [ ] **Step 5: 提交（实现 与 测试 分两次）.**

---

## Task 9: `lint_profile.py` — 一致性 lint（§8.6）

**Files:**
- Create: `tools/lint_profile.py`
- Test: `tests/tools/test_lint_profile.py`

**Interfaces:**
- Produces: `lint(app_dir, today: str) -> list[str]`（返回问题列表，空=干净）。检查项：①`profile`/`prerequisites` 内**重复 key/code**；②**跨产物复制**（同一 key 在 entries 与 verified_chains 重复承载结构化信息）；③**stale**（`last_verified` 早于 `today` 超 N 天 且 `status!=stale` → 应标 stale）；④**md-yaml 漂移**（`derive_docs.derive` 的产物与磁盘 `apps/guojin/*.md` 不一致）。

- [ ] **Step 1: 写失败测试.**（构造：植入重复 key 的 profile、一个 last_verified 很旧但 status=verified 的 entry、一个手改过的 md → 断言各被抓；一个干净 app_dir → 返回 []。用 tmp_path 造 fixture app 目录。）
- [ ] **Step 2: 运行确认失败.**
- [ ] **Step 3: 实现 `tools/lint_profile.py`.**（`today` 参数注入避免 `Date.now` 类不确定；漂移检查复用 `derive_docs.derive`。）
- [ ] **Step 4: 运行确认通过.**
- [ ] **Step 5: 提交（实现 与 测试 分两次）.**

---

## Task 10: `SKILL.md` + references 软护栏（§8.5 skill）

**Files:**
- Create: `.claude/skills/app-selftest/SKILL.md`
- Create: `.claude/skills/app-selftest/references/{workflow.md, tiering.md, pitfalls.md}`
- （`references/safety-policy.md` 已存在，不改）
- Test: `tests/skills/test_skill_structure.py`

**Interfaces:**
- Produces: 可被 Skill 工具加载的 app-selftest skill：SKILL.md 有 frontmatter(`name`/`description`)，正文=触发条件 + 加载顺序（app.yaml 匹配设备包/版本→profile 派生上下文→prereq_extract 备前置→冻结 selection 默认high→定 mode(env.yaml)→测中 submit-guard→测后反哺）+ 护栏钩子指引 + 薄索引（指向 references 与 apps/<app>/ 派生 md，不内联坐标）。

- [ ] **Step 1: 写结构 lint 测试.**

```python
# tests/skills/test_skill_structure.py
import pathlib, re
SK = pathlib.Path(__file__).resolve().parents[2] / ".claude/skills/app-selftest"

def test_skill_has_frontmatter():
    txt = (SK / "SKILL.md").read_text(encoding="utf-8")
    assert txt.startswith("---")
    assert re.search(r"^name:\s*app-selftest", txt, re.M)
    assert re.search(r"^description:", txt, re.M)

def test_references_exist():
    for r in ("workflow.md", "tiering.md", "pitfalls.md", "safety-policy.md"):
        assert (SK / "references" / r).exists()

def test_tiering_encodes_high_default():
    txt = (SK / "references/tiering.md").read_text(encoding="utf-8")
    assert "high" in txt and "BLOCKED_ENVIRONMENT" in txt
```

- [ ] **Step 2: 运行确认失败.**
- [ ] **Step 3: 写 SKILL.md + 三个 references.**
  - `SKILL.md`：frontmatter + 触发（"给一份 Excel 用例 + Android app 要 AI 驱动自测"）+ 生命周期（spec §六）+ 护栏（引 hooks + references/safety-policy.md）+ 薄索引（apps/<app>/画像.md·前置条件.md·速览.md 由脚本派生，坐标运行时 `droid find` 动态取）。
  - `workflow.md`：取自 `自测经验总结.md` §一工作流（解析Excel分档→按屏分组→串行驱动→断言+截图→回填→重生成→记 metrics）+ 工具清单（droid/annotate/metrics/prereq_extract/derive_docs/reback）。
  - `tiering.md`：**默认只测 high**（[[feedback-high-priority-only]]）+ 分档 🟢/🟡/☑ + 一致性/视觉不降级 + **BLOCKED_ENVIRONMENT**（降级致无法真实提交/撤单的用例独立列、不计通过率）+ selection scope_hash 确认。
  - `pitfalls.md`：取自 画像§四 + 经验§二 坑清单（终端乱码 PYTHONUTF8、防重锁提交连贯、确认框挡 dump 用截图、canvas 坐标 displayed×1.42、下拉必须点选、市价需盘口深度、入口≤2次即止、担保品可用≠可融资）。
- [ ] **Step 4: 运行确认通过.**
- [ ] **Step 5: 提交（SKILL+references 与 测试 分两次）.**

---

## 遗留 / 待用户处理（本 plan 不做，早晨简报提示）

1. **[必需] `env.yaml` 真实 operator_attested 认证 + 签名**：负责人 shenjie 照 `apps/guojin/env.yaml.example` 把真实 `attested_by`/`attested_at`/`valid_until`/`basis` **填进 `apps/guojin/env.yaml`**，设 `revoked:false`（真实 attested 值），生成 `.secrets/hmac.key` 与 `guojin.accounts.yaml`，运行 `env_sign` 生成 `env.yaml.sig`。**禁止直接把 `env.yaml.example` 改名为 `env.yaml`**——example 是假认证样例（假名/假日期），改名会得到假认证却能通过 `verify_env`；必须由负责人本人填真实值再签名。**在此之前系统安全默认在 `confirm_only`**（下单/撤单类用例记 BLOCKED_ENVIRONMENT）。
2. **[可选] git 历史脱敏清洗**：旧提交里 `apps/guojin/*.md`(原 profiles)、`tools/annotate_excel.py`、`runs/*/report.md` 等仍含真实账号 `***5183`/`***2927`。若需彻底清除，用 `git filter-repo` 重写历史（破坏性、改 commit hash）——**需用户明确授权**。
3. **[条件] 上下文税护栏最终形态**：以 Task 1 spike 结论为准；若确认 hook 拿不到实时 token，则维持 D2 降级态（metrics.py + 阈值提醒）。

---

## Self-Review 记录

- **spec 覆盖**：§8.4→T1；§8.5(迁移)→T4/5/6/7、(skill)→T10；§8.6(反哺/派生/lint)→T7/8/9；§L3 硬护栏→T2；§8.1 遗漏的 profile/prerequisites schema→T3。✅
- **P0 不自签**：T4 交付 `revoked:true` 安全默认 + example + 签名器，测试断言现成 env→confirm_only、样例→simulated_submit；无任务写"有效真实认证"。✅
- **脱敏**：T4/5/6/7 测试均断言仓内无完整账号；完整号只进 `.secrets`（用户填）。✅
- **类型一致**：`load_and_validate(path, name)`、`verify_env(env,pkg,ver,now,integrity)`、`derive(app_dir)->dict`、`upsert(doc,section,entry)`、`lint(app_dir,today)`、`sign(path,key)` 全程一致。✅
- **命名与 Plan1 一致**：schema 名 `profile`/`prerequisites` 与文件名对齐 validate.py 约定。✅
- **占位扫描**：无 TBD/TODO；代码步骤给真实代码。✅
- **[复审修订] hook 白名单 ↔ .gitignore 两处一致**：T2 `guard_git_add._SECRET_ALLOW_PAT`（`(^|/)\.secrets/(README\.md|[^/]*\.example)$`）镜像 T4 Step7 `.gitignore` 的 `!.secrets/README.md` / `!.secrets/*.example`；两处均**放行** `.secrets/README.md`+`.secrets/*.example`、**仍拦/忽略** `.secrets/*.yaml`+`hmac.key`。T2 补 allow/block 双向断言。✅
- **[复审修订] D2 护栏真实发声**：D2 落在 `tools/metrics.py`（每批 metrics 算完调 `assess` 打印提醒），不依赖不存在的 Stop `metrics` payload；`context_tax_reminder.py` 只出 `assess` 纯函数、`_main` 仅手动 CLI 不接 Stop hook；settings.json 仅 T2 写。T1 补 `test_metrics_context_tax.py` 断言超阈值发声/未超静默。✅
- **[复审修订] T3 fixtures 完整**：顶层 `required` 显式列全（profile 5 项 / prerequisites 4 项）；valid fixtures 字面齐全能过校验；`profile_invalid` 缺 entry.key、`prerequisites_invalid` 保留裸 account_no（P0 全号拒绝不削弱）。✅
- **[复审修订] 防改名 + 主动脱敏**：`.secrets/README.md` 与遗留项 1 均禁"`env.yaml.example` 改名为 env.yaml"；T5/T6 步骤明确忠实转写+主动脱敏，测试断言 `***5183`/`***2927`/`***1395`/`***0047` 四号均不入 `profile.yaml`/`prerequisites.yaml`。✅
