# app-selftest

AI 驱动 Android App 用 Excel 用例做业务自测的作业指导。用户给一份 Excel 用例 + 一个 Android app（如国金证券北交所ETF用例），要求驱动真机把用例跑一遍并出报告时使用。

## 何时使用

- 用户要求「帮我测一下 XX 证券这批用例」、用 Excel 用例驱动 Android 真机自测；
- 需要按优先级筛选用例、按屏驱动断言取证、结构化反哺 App 画像、守交易安全护栏、控制上下文税。

## 如何使用（生命周期）

0. 测前：确认 `app-selftest-prepare` 已产出 `selection.yaml` + `scope_hash` + 本轮前置清单（代码齐、路径明），缺失/过期则先跑前置任务，不现场重新收集核对；
1. 测前：`python tools/droid.py wait-device` 探测真机（超时即停止，不无限等待）→ 读 `apps/<app>/app.yaml` 用设备实际 package/version 匹配，越界则停止并要求重验证；
2. 测前：读前置任务已核对/补全的 `apps/<app>/画像.md` / `速览.md` 建入口/能力/已验证链上下文；画像仍缺入口 = 前置未完成，回前置任务；
3. 测前：读前置任务产出的「本轮前置」清单（代码已解析/补齐），仍缺码 = 前置未完成，回前置任务；
4. 测前：用前置任务的 `scope_hash` 冻结 selection，可审计、不临场扩大；
5. 测前：读 `apps/<app>/env.yaml` 走 `tools/safety/env_auth.verify_env` 定 mode（默认安全降级 confirm_only）；
6. 测中：按 `references/workflow.md` 串行驱动 + 断言截图，下单类经 `tools/safety/submit_guard.py` 硬校验；
7. 测后：先按 `sheet+row` 生成结构化 `results.json`，执行 `python tools/annotate_excel.py --src <用例.xlsx> --results <run>/results.json --out <run>/标注.xlsx --strict` 自动回填并核对 `matched` 数量；再用 `tools/reback.py` 反哺画像 → `tools/derive_docs.py` 重派生 md → `tools/lint_profile.py` 查漂移 → `tools/metrics.py` 记 metrics。

## 完整细节

- 完整工作流 / 坑清单 / 分档口径 / 交易安全策略：见本目录 `SKILL.md` 与 `references/`。
- 数据权威：`apps/<app>/profile.yaml`、`prerequisites.yaml` 是机器权威数据源；`画像.md` / `前置条件.md` / `速览.md` 由脚本派生，勿手改。
- 护栏：`git add .` 拦截 + 秘密路径隔离（`.githooks/pre-commit`，任何平台提交都会触发）；交易安全硬校验见 `references/safety-policy.md`。
