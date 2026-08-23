---
name: app-selftest-prepare
description: app-selftest 的前置任务。读用例→收集所需信息(代码+路径)→与 profile/prerequisites 核对→缺码提问人工补、缺路径探索→冻结 selection 产出 scope_hash 交付物。主任务 app-selftest 消费此交付物，不现场重新收集核对。
---

# app-selftest-prepare

## 触发条件

跑 `app-selftest` 主任务**之前**，先跑本前置任务把「代码 / 路径 / 范围」备齐。主任务第 0 步检测到前置交付物缺失/过期时，也应引导先跑本任务。

## 输入 / 输出契约

- **输入**：用例文件（`cases.yaml` 或 Excel）+ app slug（如 `guojin`）+ 市场（如 `北交所`）。
- **输出（交付物，主任务消费，可审计）**：
  - `selection.yaml` + `scope_hash`——冻结的测试范围（用例/优先级/档位）。
  - 本轮前置清单（`本轮前置.yaml`/`.md`）——每例所需代码 + 已解析码 + `⚠️缺码` 清单。
  - 路径核对结果——每例所需入口 + `profile.yaml` 是否收录 + 缺路径标记。

## 流程（4 步）

1. **收集**：解析用例，提取两条需求链——① 代码需求（委托/标的代码）② 路径需求（菜单入口，如「大宗交易→定价买入」）。

2. **缺码核对（复用硬引擎，不重写）**：
   `python tools/prereq_extract.py --cases <cases> --rules tools/prereq_rules.yaml --prerequisites apps/<app>/prerequisites.yaml --out-yaml ... --out-md ...`
   → 得 `missing_codes` + `⚠️缺码` 清单 → **一次性列全提问人工补齐**（别一条一条问）→ 写回 `prerequisites.yaml`（改 yaml 后 `derive_docs.py` 重派生，勿手改 md）。

3. **缺路径核对（软核对）**：
   逐条对照用例所需入口 vs `profile.yaml` 的 `entries`（入口地图）。缺的标「入口未收录」→ 走 explore mode（见 `app-selftest/references/explore.md`）探索 → 写回 `profile.yaml`。
   纪律：入口试 ≤2 次找不到就标「入口待确认」，不无限摸索烧 token。

4. **冻结 + 出交付物**：
   按 `app-selftest/references/tiering.md` 分档（默认只测 `high`），生成 `selection.yaml` + `scope_hash`，声明「代码齐 + 路径明 + 范围冻结」。

## 护栏 / 边界

- 本任务只做「核对 + 补缺 + 冻结」，**不驱动真机跑用例**（那是主任务 `app-selftest` 的事）。
- 缺码 vs 代码缺失要分清：**缺码** = 用例需要某属性的码、`prerequisites.yaml` 里没有可用码（提问补）；**目标代码缺失** = 用例指定的具体代码在 App 内搜不到 → 直接判 ❌错误（见 `tiering.md`），不是缺码。
- 缺码提问人工时一次性列全，别测中反复打断。
