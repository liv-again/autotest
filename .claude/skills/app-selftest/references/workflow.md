# workflow — 工作流（取自 `自测经验总结.md` §一，已跑通、照这个来最省心）

## 主链路

```
解析Excel(分档) → 按"屏"分组用例 → 串行驱动手机(一屏多用例) → 元素树断言/视觉兜底 + 截图取证 → 生成results.json → 通用回填器写回标注Excel → 记性价比(metrics)
```

- **解析Excel(分档)**：按分档口径（见 `tiering.md`）过滤出本轮范围，默认只取 `优先级==high`。
- **按屏分组**：把用例按会落在同一个页面/入口的分组，进一次详情页就把该屏能验的用例全验掉，比逐例导航省数倍成本。
- **串行驱动手机**：`tools/droid.py` 一次一步操作真机（adb + uiautomator），不并发操作同一设备。
- **元素树断言 + 截图取证**：断言优先用 `droid.py has "关键词"`（退出码判断，内部比对不看输出，规避终端乱码）；dump 找不到图标/自定义绘制目标时，按 `visual-targets.md` 使用截图和 `droid.py tap --bbox` 兜底；需要留痕的关键结果截图（`droid.py shot`），每个用例留 1 张最能说明问题的即可。
- **生成结果 → 质量门 → 逐条复测 → 回填标注Excel**：执行器必须在每个步骤结束后提供独立的 `action → observation → status` 事实链，公共导航写入顶层 `setup_trace`，不得混入用例 `actual`。先调用 `python tools/build_results.py --input execution_records.json --out results.json`，由质量门拒绝空 `actual`、通用操作占位句、仅复述 action、跨用例大量复用结果及缺失 evidence。一个 sheet/模块完成后调用 `python tools/retest_results.py plan ...` 生成单用例队列，逐条重新 setup 和执行未通过用例，再用 `retest_results.py merge ...` 合并；合并保留首轮与复测两轮历史，最终回填使用第二轮状态。最后调用 `python tools/annotate_excel.py --src cases.xlsx --results results.final.json --out annotated.xlsx --evidence-root <run> --strict`。严格回填会拒绝证据路径不存在、未匹配结果或 `matched` 数量不一致。
- **记性价比**：跑完一批用 `tools/metrics.py` 记 output token / 上下文税(cache_read) / 单行成本，写入 `runs/metrics.md`；超阈值会由 `metrics.py` 里接线的 `assess` 自动打印提醒（考虑新开精简会话/固化 Maestro）。

## 工具清单（都在 `tools/`）

| 工具 | 作用 |
|---|---|
| `droid.py` | adb 驱动助手：`current/screen/find/has/tap/type/key/swipe/shot/dump-xml`；`tap --bbox` 支持视觉模型返回框中心点击，是真机驱动与断言的唯一入口。 |
| `annotate_excel.py` | 通用 Excel 回填 CLI/API：按行号/用例 ID/名称定位，追加或更新 AI 列、内联证据并生成汇总；不负责决定本轮筛选范围。 |
| `retest_results.py` | 首轮 sheet/模块完成后生成未通过用例的单条复测队列，并将复测结果合并为最终结果；保留 `attempts` 两轮历史，要求复测定位和证据完整。 |
| `metrics.py` | `now`/`tokens SINCE UNTIL`：按会话 transcript 算 token/成本；`context_tax_metrics`/`remind` 是 D2 上下文税阈值提醒的真实触发点。 |
| `prereq_extract.py` | 测前按带极性规则表从 `prerequisites.yaml` 的 `known_codes` 解出「本轮前置」（缺码高亮），一次性备齐测试数据。 |
| `derive_docs.py` | 把 `profile.yaml`/`prerequisites.yaml` 派生成 `apps/<app>/{画像.md,前置条件.md,速览.md}`（勿手改 md，改 yaml 再生成）。 |
| `reback.py` | 测后把本轮结果按声明标识字段（`key`/`alias`/`code`）结构化 `upsert` 回 `profile.yaml`/`prerequisites.yaml`；写盘前做 schema 校验，失败不写。 |
| `lint_profile.py` | 查重复 key/code、跨产物复制、stale（`last_verified` 过旧却仍标 verified）、md 相对 yaml 的派生漂移。 |
| `safety/*` | `env_auth.verify_env`（环境认证→mode）、`submit_guard.guard_submit`（逐笔硬校验）、`recovery.plan_recovery`（撤单闭环/恢复）、`secrets.*`（HMAC/防篡改）——见 `safety-policy.md`。 |

## 环境自检（开工前顺手过一遍）

- **设备探测用 `python tools/droid.py wait-device`**：内置 30s 间隔 × 最多 3 次重试，3 次未连接即返回退出码 1 → **停止任务，不无限等待**（不要手动 `adb devices` 干等）。
- 命令统一加 `PYTHONUTF8=1` 前缀（尤其涉及中文输出/重定向时，见 `pitfalls.md`）。
- 目标 App 的 `FLAG_SECURE` 已关闭（否则截图 0 字节）。
