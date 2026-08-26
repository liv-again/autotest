# 视觉目标兜底

当目标是图标、图片或自定义 Canvas 内容，且 `uiautomator dump` 没有对应节点时，使用视觉兜底流程。

## 资料位置

- App-specific 图标语义放在 `apps/<app>/visual_anchors.yaml`。
- 参考截图放在 `apps/<app>/visual/`，运行证据放在 `runs/<run>/shots/`。
- 通用操作规则放在本文件；不要把某个 App 的图标含义写进通用规则。

视觉词典中的 anchor 至少应说明：

```yaml
- key: quote.chart.fullscreen
  meaning: 进入分时图横屏/全屏模式
  screen: quote.detail
  visual_description: 图表区域右上角的四角展开图标
  nearby_text: 分时、日K
  reference_image: visual/quote_fullscreen.png
  action: tap_center
  verify_after:
    orientation: landscape
```

## 执行顺序

1. 先用 `find`/`has`/`tap --text`/`tap --id` 尝试无障碍节点。
2. 找不到时，保存原始截图：`python tools/droid.py shot runs/<run>/shots/<name>.png`。
3. 让视觉模型基于原始截图返回目标 `bbox: [x1, y1, x2, y2]` 和置信度；目标不清晰或置信度不足时不要点击。
4. 用 `python tools/droid.py tap --bbox x1 y1 x2 y2` 点击框中心。
5. 点击后重新截图或 dump，并验证 anchor 的 `verify_after`；失败时标记为未确认，不盲目重复点击。

坐标必须对应原始设备截图，而不是聊天窗口中的缩略图。若模型使用了缩略图，先按原图宽高做比例换算。涉及交易提交、撤单或其他不可逆动作时，视觉点击只能作为导航/打开面板的兜底，提交前仍须经过现有安全护栏和明确状态确认。
