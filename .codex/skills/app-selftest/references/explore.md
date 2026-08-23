# explore mode —— 画像路径半自动探索（换 App / 画像缺失时的前置模式）

> 配套：`SKILL.md`（主流程）、`onboarding.md`（换 App 指南）、`tools/init_app.py`（骨架生成）。
> 方案依据：`项目说明文档.md` §8.5（entries 半自动落地方案）。

## 触发时机（双轨）

| 轨 | 触发 | 范围 |
|---|---|---|
| **轨一 · 按需（日常跑批内嵌）** | 用例分析后命中 unverified / stale / 画像未命中的入口 | **只探当次缺失的 key**，不单独起任务 |
| **轨二 · 独立（一次性）** | 新 App 从零建画像（profile 为空）；大版本全量重验证（全部入口降级 unverified） | 全量探索 |

判断标准：探索投入是"当次快取"（轨一）还是"资产重建"（轨二）。

## 铁律（安全边界，违反即停）

1. **全程 `confirm_only` 等价**：只导航、看控件、到确认框**之前**为止，**绝不点提交**——探索 ≠ 验证。
2. **写盘只走 `tools/reback.py` 的 `reback_run`**（schema 门，失败不落盘），**永不手写 yaml**（§七-8 教训：手写绕过即翻车）。
3. **不生成/不修改 `env.yaml`**——环境认证永远人工（P0：执行程序不得自我认证）。
4. 每条探索产出**必须可回放**：path 串生成后按它重走一遍（纯导航），走不通当场重探。

## Step 0：种子（候选入口清单，复用度递减）

| 来源 | 适用 | 做法 |
|---|---|---|
| **白标复制** | 同花顺系券商 | `python tools/init_app.py <slug> --package <包名> --version <版本> --seed-from apps/guojin` ——自动抄 16 条 entries（全标 `status: unverified`、`evidence_run: seed-from-guojin`），探索循环只做"确认+修差异" |
| **用例反推** | 任何 App | Excel 标题含入口语义（"普通买入"→`trade.putong.buy`）；`key` 命名空间与 App 解耦，直接沿用 |
| **首页枚举** | 兜底 | `python tools/droid.py screen` dump 首页/交易 tab，可点菜单全列出来当候选 |

## Step 1：探索循环（对已知前置入口）

对每条候选 entry，执行固定动作序列——**全部用现有 `droid.py` 子命令，零新工具**：

```bash
python tools/droid.py screen              # 拉当前屏元素树(resource-id/文字/坐标)
python tools/droid.py find "买入"          # 定位入口 → 拿坐标
python tools/droid.py tap --text "买入"    # 进入
python tools/droid.py screen              # 再 dump：记录本屏全部可交互元素
python tools/droid.py type <测试码>        # 对输码框做交互探测
python tools/droid.py screen              # 观察回填行为：弹了下拉？键盘遮挡？
python tools/droid.py has "涨停"           # 断言关键控件存在(退出码，绕 GBK 乱码)
python tools/droid.py shot runs/explore-<日期>/shots/<key>.png   # 留证据
python tools/droid.py key BACK            # 回退，探下一个
```

每屏记录三类信息（path 串的原料）：
1. **导航链**：从哪进到哪（`交易→买入`）
2. **控件 id 清单**：输入框/按钮/弹框的 resource-id（`auto_stockcode`/`btn_transaction`/`dialog_title`/`ok_btn`）
3. **交互行为备注**：括号里那些（"输码后点下拉回填""±0.001 微调"）——靠**实际 type/tap 试探**观察，纯 dump 看不出来

**一屏多探**：进了买入页就顺便把价格微调、仓位键、限价/市价切换全探掉，不重复进页（省 token，metrics.md 的"一屏多用例"策略）。

## Step 1'：导航图爬取（前置入口未知时的兜底）

**触发**：无种子（异平台）或种子漂移失效，候选入口无法从已知导航链到达。

**核心认知：前置入口未知不是死路——探索循环本身就是"导航图爬取"，入口是靠发现出来的，不是靠预先知道的。**

```
起点：App 根（首页/首屏 tab）
循环：
  1. droid screen dump 当前屏元素树
  2. 提取全部可点击元素（clickable / 菜单文字 / 输入框）
  3. 逐个 tap → dump 下一屏 → 记录「屏A → 动作 → 屏B」的边
  4. 特征匹配：命中目标屏特征（见下）→ 回推路径链 → 该候选进入 Step 1 正常探索
  5. droid key BACK 回退 → 探下一个候选
产出：一张「屏 → 可达入口」导航图 + 每条候选入口的路径链
```

**剪枝与终止**（否则爬成无底洞）：
- 已访问屏 hash 去重（同屏重复 tap 是最大时间黑洞）
- 最大深度 3~5 层（交易类 App 入口必达区间），超限弃分支
- 中转屏（无输入框/按钮、只有 tab）标记"仅中转"，快速跳过
- 目标屏特征预置：resource-id 模式（含 `stockprice`/`btn_transaction`）或业务关键字，命中即停

**辅助信号缩短遍历**：用例标题反推（"普通买入"→直接去交易 tab）；白标种子（id 给到页面级）；`droid find` 全局定位（跨屏先找文字）。

**发现不了的兜底**：canvas 无 id 页面（闪电面板）→ path 串标 `canvas-only, 坐标待标定` 条目标 unverified 挂起转人工；或截图+多模态模型换算坐标（先做前者）。

## Step 2：归纳 path 串（few-shot + 回放校验）

以 `apps/guojin/profile.yaml` 现有 entries 为模板（few-shot），格式高度规律：

```
页面链；控件id(交互备注)；确认框结构；结果断言点
```

**回放校验（质量门）**：归纳后按 path 串重走一遍（纯导航），走不通说明归纳错了，当场重探。

## Step 3：写盘（只走 reback）

```bash
PYTHONUTF8=1 python -c "
from tools.reback import reback_run
reback_run(
    'apps/<slug>/profile.yaml', 'apps/<slug>/prerequisites.yaml',
    {'profile': {'entries': [{
        'key': 'trade.putong.buy',
        'path': '<压缩出的导航串>',
        'last_verified': '<今天>',
        'app_version': '<设备实际版本>',
        'evidence_run': 'explore-<日期>',
        'status': 'unverified',
    }]}}
)"
```

写完后：`python tools/derive_docs.py apps/<slug>` 重新派生 md + `python tools/lint_profile.py apps/<slug> <今天>` 查一致性。

## Step 4：升级 unverified → verified

- **`quote.*` 查询/展示类**：探索时已实际到达页面+截图留证，探索轮即可直接标 `verified`
- **`trade.*` 交易类**：必须等**盘中实测**走完下单链（过 `submit_guard`/`non_marketable` 安全门、撤单闭环），由那次 run 的 reback 翻 `verified`——探索不替代验证
- `lint_profile` 30 天 stale 机制自动兜底时效（机器生成的条目也不例外）

## 附带收益：同一循环就是版本重验证工具

App 大版本升级（如国金 8.05.001 → 9.02.10，超出 `compatibility`）时：全部 entries 批量降级 `unverified` → 跑一遍 explore mode → path 有漂移的地方自动更新、没漂移的直接翻回 verified。**画像维护和新 App 接入是同一个循环**。
