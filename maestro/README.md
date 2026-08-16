# 国金证券 · 下单/撤单链 Maestro 固化

把已实测跑通的**下单→撤单**回归链固化成 [Maestro](https://maestro.mobile.dev) flow，CI/回归时**无需 AI**即可跑，是"实时跑通→固化脚本"的终点，也是最省的回归方式。

## 覆盖的链路（均已在 2026-07-28/29 盘中实测通过）
| flow | 链路 | 选择器稳健度 |
|---|---|---|
| `putong_limit_buy_cancel.yaml` | 普通交易 限价买入 → 撤单 | ⭐⭐⭐ resource-id |
| `putong_limit_sell_cancel.yaml` | 普通交易 限价卖出 → 撤单 | ⭐⭐⭐ resource-id |
| `xsb_buy_cancel.yaml` | 新三板 限价买入 → 撤单 | ⭐⭐⭐ resource-id |
| `xsb_sell_cancel.yaml` | 新三板 限价卖出 → 撤单 | ⭐⭐⭐ resource-id |
| `flash_buy_cancel.yaml` | 闪电买入 → 撤单 | ⭐ **坐标(设备相关)** |
| `flash_sell_cancel.yaml` | 闪电卖出 → 撤单 | ⭐ **坐标(设备相关)** |
| `rzrq_collateral_buy_cancel.yaml` | 融资融券 担保品限价买入 → 撤单 | ⭐⭐⭐ resource-id |
| `rzrq_collateral_sell_cancel.yaml` | 融资融券 担保品限价卖出 → 撤单 | ⭐⭐⭐ resource-id |
| `rzrq_sell_repay_cancel.yaml` | 融资融券 卖券还款限价 → 撤单 | ⭐⭐⭐ resource-id |

> ⚠️ **闪电两条用坐标**：国金闪电下单是 canvas 紧凑覆盖面板，元素树取不到 id（uiautomator dump 不到），只能用屏幕百分比坐标点击。**坐标按 1260×2844 标定，换设备/分辨率需重新校准**。其余七条用 resource-id，跨设备稳健。
>
> ⚠️ **融资融券三条**（2026-07-29 实测链路：担保品买 6→撤7、担保品卖 8→撤9、卖券还款 10→撤11）需**信用/两融账号**，入口多一层：`交易 → 顶部『融资融券』子tab(id=tv_tab_rzrq) → 菜单担保品买入/卖出/卖券还款`。买卖页与普通交易同构复用同批 id，提交按钮 id 为 `btn_buy`(文本按类型变)，确认按钮走文本『确认买入/确认卖出』。**卖出/还款页按『我的持仓』代码文本选中回填**(顺序随持仓变)。SELL_CODE 必须是**信用账户有担保品持仓**的码。这三条 Maestro 未安装未 `maestro test` 验证过，跑前先验。

## 关键设计：为什么用「跌停价买 / 涨停价卖」
下单类回归的最大难点是**不能真成交**（否则改变持仓、无法撤单、留残留）。策略：
- **买入** → 用 `copyTextFrom` 取当前**跌停价**作委托价 → 远低于市价 → 必挂"已报"不成交 → 可撤单。
- **卖出** → 取当前**涨停价**作委托价 → 远高于市价 → 必挂"已报"不成交 → 可撤单。
- 数量固定 100（北交所ETF 最小交易单位）。
- 每条 flow 自带撤单收尾 → **账户零残留**。

## 前置条件（跑之前必须满足）
1. **交易时段**：工作日 9:30–15:00。非交易时段柜台拒单 `[120147]`，flow 会在断言处失败。
2. **已登录**模拟交易账户（开发专用 **1395 / 资金账号 ***5183，全权限、模拟资金充足）。
3. **测试标的代码**（见 `env`，可覆盖）：
   - 买入类：任意北交所ETF代码即可（无需持仓），默认 `950025`。
   - 卖出类：**必须是账户有持仓的**北交所ETF，默认 `950015`（该账户持仓 1300）。
   - 换券商/换账户时，按 `../profiles/测试数据代码需求清单.md` 提前备码。
4. **设备**：adb 可连、屏幕解锁。截图非必需（断言走文本/id）；如需截图，App 的 FLAG_SECURE 需关闭。
5. 安装 Maestro：`curl -fsSL "https://get.maestro.mobile.dev" | bash`（需 Java 11+）。

## 运行
```bash
# 单条
maestro test maestro/putong_limit_buy_cancel.yaml
# 覆盖标的代码
maestro test -e BUY_CODE=950001 maestro/putong_limit_buy_cancel.yaml
# 全部（顺序跑，单机串行）
maestro test maestro/
```

## 已知坑（flow 里已规避，换 App 版本时注意）
- **下拉必须点选**：输入代码后要点下拉建议项（`stock_code_tv`/`tv_stock_name`）才回填，直接提交无效。
- **防重复提交锁**：`买入按钮 → 确认按钮` 必须连贯（flow 里两步相邻、无等待）。过慢/重复点会触发客户端「请勿重复提交委托请求」使委托作废。
- **收键盘**：App 用自定义数字键盘；flow 用 `hideKeyboard`。若某设备上它误触发页面返回，改成点击键盘上的「确定/完成」键或空白区。
- **撤单取最新一条**：flow 撤"撤单列表第一条"（最新委托在最上）。共享账户若有他人更新委托插到最上，需改成按代码/价格匹配行。
- **确认框挡 dump**：下单/撤单确认框会挡 uiautomator，但 Maestro 走 accessibility 仍可点 `ok_btn`；纯 adb 方案才需截图。
- **`copyTextFrom` 跌/涨停价含前缀**：`dietingprice`/`zhangtingprice` 的文本是「跌停67.343」/「涨停125.063」(含中文前缀)。若价格框把整串当输入导致校验失败，改为：①先点『跌停/涨停』标签自动带价，或 ②用 `-e PRICE=<当日非成交价>` 传固定价替换 copyTextFrom 段。跑 `maestro test` 时优先验证此处。
- **两融入口多一层 tab**：融资融券三条先 `tapOn 交易 → tapOn id=tv_tab_rzrq → tapOn 菜单`；若信用账号未登录，tv_tab_rzrq 点开会是空/登录态，flow 在 auto_stockcode 断言处失败。

## 维护
- resource-id、入口、交互细节见 `../profiles/国金证券-券商画像.md`。
- 测试数据（代码）需求见 `../profiles/测试数据代码需求清单.md`。
